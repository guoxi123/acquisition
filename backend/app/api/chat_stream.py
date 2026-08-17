"""流式对话 API：POST /api/chat/stream + GET /api/chat/stream/{id}/events。

POST 触发（同步 parse_intent + HITL 检查，完整则后台 create_task 跑 stream 图）。
GET SSE 轮询 DB（200ms），content 增长 → delta，meta done → sellers。
POST /{thread_id}/cancel 取消正在运行的图，保留已输出数据。
"""

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.auth.deps import get_current_user
from app.core.db import async_session
from app.memory import agent as memory_agent
from app.memory.models import MemoryMessage, MemorySession, MessageRole
from app.models.user import User
from app.utils.logger import logger

router = APIRouter(prefix="/api/chat/stream", tags=["chat-stream"])


class StreamRequest(BaseModel):
    query: str
    thread_id: str | None = None
    marketplace: str | None = None  # HITL 补充重跑时注入，覆盖意图识别结果
    category: str | None = None


async def _run_stream_graph(
    thread_id: str, assistant_msg_id: str, state: dict
):
    """后台跑 stream 图（check_cache → call_actors → llm_analysis → score → output）。"""
    from app.agent.trace import JsonlTracer
    from app.agent.v2.graph import get_stream_graph

    logger.info(f"[stream-graph] 开始: thread={thread_id} category={state.get('category')} market={state.get('marketplace')}")
    state["assistant_msg_id"] = assistant_msg_id
    graph = get_stream_graph()
    try:
        await graph.ainvoke(
            state,
            config={
                "configurable": {"thread_id": thread_id},
                "recursion_limit": 50,
                "callbacks": [JsonlTracer(thread_id)],  # 本地 trace：LLM 调用写 logs/trace_*.jsonl
            },
        )
        logger.info(f"[stream-graph] 完成: thread={thread_id}")
    except asyncio.CancelledError:
        logger.info(f"[stream-graph] 进程级取消: thread={thread_id}")
        # 保留已写入的 sellers，只翻 done/cancelled（流式图无 checkpointer，不能用 state）
        from app.agent.v2.nodes import _write_msg
        await _write_msg(assistant_msg_id, meta_patch={"done": True, "cancelled": True})
    except Exception as e:
        # CancelledByUser 是 Exception 子类，catch 在这里统一处理
        from app.agent.v2.nodes import CancelledByUser, _write_msg
        if isinstance(e, CancelledByUser):
            logger.info(f"[stream-graph] 用户协作式取消: thread={thread_id}")
            await _write_msg(assistant_msg_id, meta_patch={"done": True, "cancelled": True})
        else:
            logger.error(f"[stream-graph] 失败: thread={thread_id} error={e}")
            await _write_msg(
                assistant_msg_id,
                content=f"（出错：{e}）",
                meta_patch={"sellers": [], "done": True, "error": str(e)},
            )
    finally:
        pass


@router.post("")
async def chat_stream(
    req: StreamRequest, user: User = Depends(get_current_user)
) -> dict:
    logger.info(f"[chat/stream] user={user.username} query={req.query[:50]} thread_id={req.thread_id}")

    if req.thread_id:
        async with async_session() as db:
            session = await db.get(MemorySession, uuid.UUID(req.thread_id))
        if session is None:
            raise HTTPException(404, "会话不存在")
        if session.user_id != user.id and not user.is_super_admin:
            raise HTTPException(403, "无权访问此会话")
        thread_id = req.thread_id
        session_uuid = uuid.UUID(thread_id)
    else:
        sid = await memory_agent.create_session(user_id=user.id)
        thread_id = str(sid)
        session_uuid = uuid.UUID(thread_id)

    # 补充重跑（带 thread_id + marketplace/category）：原 query 已在上轮存过，不重复存用户消息；
    # 否则记用户消息。assistant 占位消息始终新建（图往里写流式内容）。
    is_resupply = bool(req.marketplace or req.category)
    if not (req.thread_id and is_resupply):
        await memory_agent.add_message_and_maybe_compress(
            session_uuid, MessageRole.user, req.query
        )
    assistant_msg_id = await memory_agent.storage.add_message(
        session_uuid, MessageRole.assistant, ""
    )

    # 配额 / 意图 / HITL 全交给图（check_quota → parse_intent → …）；endpoint 只注入补充值
    state = {
        "session_id": thread_id,
        "user_query": req.query,
        "marketplace": req.marketplace,
        "category": req.category,
        "shipping_type": None,
        "service_mode": None,
        "business_country": None,
        "min_total_feedback": None,
        "min_seller_score": None,
        "need_human_confirm": False,
        "missing": [],
        "human_approved": True,
        "cache_hit": False,
        "products": [],
        "sellers": [],
        "scored_sellers": [],
        "assistant_msg_id": str(assistant_msg_id),
        "final_result": {},
        "errors": [],
        "user_id": str(user.id),
    }
    task = asyncio.create_task(
        _run_stream_graph(thread_id, str(assistant_msg_id), state)
    )

    return {
        "status": "streaming",
        "thread_id": thread_id,
        "msg_id": str(assistant_msg_id),
    }


@router.post("/{thread_id}/cancel")
async def cancel_stream(
    thread_id: str, user: User = Depends(get_current_user)
) -> dict:
    """取消正在运行的图。多 worker 安全：不依赖进程内 _tasks，先写 DB 标志（跨 worker），
    再对本 worker 的 task（若有）额外 cancel。"""
    from sqlalchemy import select

    from app.agent.v2.nodes import _write_msg
    from app.memory.models import MemoryMessage, MessageRole

    sid = uuid.UUID(thread_id)
    async with async_session() as db:
        session = await db.get(MemorySession, sid)
    if session is None:
        raise HTTPException(404, "会话不存在")
    if session.user_id != user.id and not user.is_super_admin:
        raise HTTPException(403, "无权访问此会话")

    # 1) 从 DB 找该会话最新 assistant 消息（跨 worker：不依赖 _tasks）
    async with async_session() as db:
        msg = (
            await db.execute(
                select(MemoryMessage)
                .where(
                    MemoryMessage.session_id == sid,
                    MemoryMessage.role == MessageRole.assistant,
                )
                .order_by(MemoryMessage.sequence_number.desc())
                .limit(1)
            )
        ).scalars().first()
    if msg is None or (msg.meta or {}).get("done"):
        return {"status": "already_done"}

    # 2) 跨 worker 生效：写 DB 标志，前端 SSE 立即收到 done
    await _write_msg(str(msg.message_id), meta_patch={"done": True, "cancelled": True})
    logger.info(f"[chat/stream] 已写入取消标志: thread={thread_id}")
    return {"status": "cancelled", "message": "取消请求已发送"}


@router.get("/{thread_id}/events")
async def stream_events(
    thread_id: str, user: User = Depends(get_current_user)
) -> StreamingResponse:
    """SSE 轮询 DB：content 增长 → delta；meta done → sellers + close。"""
    async with async_session() as db:
        session = await db.get(MemorySession, uuid.UUID(thread_id))
    if session and session.user_id != user.id and not user.is_super_admin:
        raise HTTPException(403, "无权访问此会话")

    async def event_gen():
        last_len = 0
        last_sellers_len = 0
        last_progress_len = 0
        heartbeat_count = 0
        sent_snapshot = False

        while True:
            async with async_session() as db:
                msg = (
                    await db.execute(
                        MemoryMessage.__table__.select()
                        .where(
                            MemoryMessage.session_id == uuid.UUID(thread_id),
                            MemoryMessage.role == MessageRole.assistant,
                        )
                        .order_by(MemoryMessage.sequence_number.desc())
                        .limit(1)
                    )
                ).first()
            if msg is None:
                await asyncio.sleep(0.2)
                continue

            content = msg.content or ""
            meta = msg.meta or {}
            heartbeat_count += 1

            # 首次连接：发送快照（断点续传：把已有内容一次推给前端）
            if not sent_snapshot:
                if content:
                    yield f"data: {json.dumps({'delta': content}, ensure_ascii=False)}\n\n"
                    last_len = len(content)
                sellers = meta.get("sellers", [])
                if sellers:
                    yield f"data: {json.dumps({'sellers': sellers}, ensure_ascii=False)}\n\n"
                    last_sellers_len = len(sellers)
                progress = meta.get("progress", [])
                if progress:
                    yield f"data: {json.dumps({'progress': progress}, ensure_ascii=False)}\n\n"
                    last_progress_len = len(progress)
                sent_snapshot = True

            # 增量
            if len(content) > last_len:
                delta = content[last_len:]
                last_len = len(content)
                yield f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"

            sellers = meta.get("sellers", [])
            if len(sellers) > last_sellers_len:
                last_sellers_len = len(sellers)
                yield f"data: {json.dumps({'sellers': sellers}, ensure_ascii=False)}\n\n"

            progress = meta.get("progress", [])
            if progress and len(progress) > last_progress_len:
                last_progress_len = len(progress)
                yield f"data: {json.dumps({'progress': progress}, ensure_ascii=False)}\n\n"

            # done 未设且消息已超过 90s（task 多半已死/异常/reload）→ 主动收尾，避免前端永远等不到 done。
            # 不查 _tasks：多 worker 下 _tasks 进程内不共享，会误判孤儿、抢在 output_result 写 need_input 前覆盖。
            # 不写 cancelled：避免覆盖 HITL 的 need_input（让前端正常弹补充 UI）。
            if not meta.get("done") and msg.created_at:
                from datetime import datetime, timezone
                from app.agent.v2.nodes import _write_msg
                _created = msg.created_at
                if _created.tzinfo is None:
                    _created = _created.replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc) - _created).total_seconds() > 90:
                    await _write_msg(str(msg.message_id), meta_patch={"done": True})
                    continue  # 下一轮读到 done，正常走下面的收尾发送

            if meta.get("done"):
                payload = {
                    "done": True,
                    "sellers": sellers,
                    "cancelled": meta.get("cancelled", False),
                }
                # 透传配额 meta（output_result 写入），供前端展示额度/升级提示
                for k in (
                    "plan", "quota", "used", "remaining_after",
                    "new_granted", "new_skipped", "exhausted",
                    "upgrade_available", "unlimited",
                ):
                    if k in meta:
                        payload[k] = meta[k]
                # 透传 HITL 补充请求（output_result 在 need_human_confirm 时写入）
                for k in ("need_input", "missing", "marketplace", "category"):
                    if k in meta:
                        payload[k] = meta[k]
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                return

            if heartbeat_count % 15 == 0:
                yield ": heartbeat\n\n"

            await asyncio.sleep(0.2)

    return StreamingResponse(
        event_gen(), media_type="text/event-stream"
    )
