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

# thread_id → (asyncio.Task, assistant_msg_id) 注册表，用于取消
_tasks: dict[str, tuple[asyncio.Task, str]] = {}
# thread_id → 取消标志
_cancelled: dict[str, bool] = {}


class StreamRequest(BaseModel):
    query: str
    thread_id: str | None = None


async def _run_stream_graph(
    thread_id: str, assistant_msg_id: str, state: dict
):
    """后台跑 stream 图（check_cache → call_actors → llm_analysis → score → output）。"""
    from app.agent.v2.graph import get_stream_graph

    logger.info(f"[stream-graph] 开始: thread={thread_id} category={state.get('category')} market={state.get('marketplace')}")
    state["assistant_msg_id"] = assistant_msg_id
    graph = get_stream_graph()
    try:
        await graph.ainvoke(
            state, config={"configurable": {"thread_id": thread_id}, "recursion_limit": 50}
        )
        logger.info(f"[stream-graph] 完成: thread={thread_id}")
    except asyncio.CancelledError:
        logger.info(f"[stream-graph] 被用户取消: thread={thread_id}")
        # 取消时保留 output_result 已写入的（已授权）sellers，只翻 done/cancelled 标志。
        # 不能用 state["scored_sellers"]：流式图无 checkpointer，节点返回值不回写调用方 dict，
        # 会读到初始空值，既覆盖掉已流式写入的 sellers（既有 bug），也会泄露未授权卖家。
        async with async_session() as db:
            msg = await db.get(MemoryMessage, uuid.UUID(assistant_msg_id))
            cur = dict((msg.meta or {}) if msg else {})
            cur.update({"done": True, "cancelled": True})
            await db.execute(
                MemoryMessage.__table__.update()
                .where(MemoryMessage.message_id == uuid.UUID(assistant_msg_id))
                .values(meta=cur)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"[stream-graph] 失败: thread={thread_id} error={e}")
        # 合并写：保留已写入的 progress，只追加 error/done（避免整体覆盖丢进度）
        from app.agent.v2.nodes import _write_msg
        await _write_msg(
            assistant_msg_id,
            content=f"（出错：{e}）",
            meta_patch={"sellers": [], "done": True, "error": str(e)},
        )
    finally:
        _tasks.pop(thread_id, None)
        _cancelled.pop(thread_id, None)


@router.post("")
async def chat_stream(
    req: StreamRequest, user: User = Depends(get_current_user)
) -> dict:
    from app.agent.v2.intent import parse_intent

    logger.info(f"[chat/stream] user={user.username} query={req.query[:50]} thread_id={req.thread_id}")

    parsed = await parse_intent({"user_query": req.query})
    if parsed.get("need_human_confirm"):
        logger.info(f"[chat/stream] HITL: missing={parsed.get('missing')}")
        return {
            "status": "need_input",
            "missing": parsed.get("missing", []),
            "marketplace": parsed.get("marketplace"),
            "category": parsed.get("category"),
        }

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

    await memory_agent.add_message_and_maybe_compress(
        session_uuid, MessageRole.user, req.query
    )
    assistant_msg_id = await memory_agent.storage.add_message(
        session_uuid, MessageRole.assistant, ""
    )

    state = {
        "user_query": req.query,
        "marketplace": parsed.get("marketplace") or "amazon.com",
        "category": parsed.get("category") or "",
        "shipping_type": parsed.get("shipping_type"),
        "service_mode": parsed.get("service_mode"),
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
    _tasks[thread_id] = (task, str(assistant_msg_id))

    return {
        "status": "streaming",
        "thread_id": thread_id,
        "msg_id": str(assistant_msg_id),
    }


@router.post("/{thread_id}/cancel")
async def cancel_stream(
    thread_id: str, user: User = Depends(get_current_user)
) -> dict:
    """取消正在运行的图，已输出数据保留在 DB 中。"""
    async with async_session() as db:
        session = await db.get(MemorySession, uuid.UUID(thread_id))
    if session is None:
        raise HTTPException(404, "会话不存在")
    if session.user_id != user.id and not user.is_super_admin:
        raise HTTPException(403, "无权访问此会话")

    task_info = _tasks.get(thread_id)
    if task_info is None:
        return {"status": "not_found", "message": "没有正在运行的任务"}

    task, assistant_msg_id = task_info
    if not task.done():
        task.cancel()
        logger.info(f"[chat/stream] 已请求取消: thread={thread_id}")
        return {"status": "cancelled", "message": "取消请求已发送"}

    return {"status": "already_done"}


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
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                return

            if heartbeat_count % 15 == 0:
                yield ": heartbeat\n\n"

            await asyncio.sleep(0.2)

    return StreamingResponse(
        event_gen(), media_type="text/event-stream"
    )
