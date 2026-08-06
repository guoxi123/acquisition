"""V2 对话 API：POST /api/chat + /chat/resume。

鉴权（JWT）+ 会话创建权限（超管无限，普通用户 1 次）+ memory 集成。
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from langgraph.types import Command
from pydantic import BaseModel

from app.auth.api import _user_out
from app.auth.deps import get_current_user
from app.core.db import async_session
from app.memory import agent as memory_agent
from app.memory.models import MemorySession, MessageRole
from app.memory.storage import get_messages as get_session_messages
from app.models.user import User
from app.utils.logger import logger

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    query: str
    thread_id: str | None = None


class ResumeRequest(BaseModel):
    thread_id: str
    response: dict


def _extract_interrupt(state) -> dict | None:
    tasks = state.tasks
    if isinstance(tasks, dict):
        tasks = tasks.values()
    for t in tasks:
        interrupts = getattr(t, "interrupts", None) or []
        if interrupts:
            return interrupts[0].value
    return None


def _result_summary(result: dict | None) -> str:
    if not result:
        return "完成"
    return f"找到 {result.get('count', 0)} 个卖家"


async def _verify_session_access(thread_id: str, user: User) -> None:
    """验证用户对会话的访问权（自己的或超管）。"""
    async with async_session() as db:
        session = await db.get(MemorySession, uuid.UUID(thread_id))
    if session and session.user_id != user.id and not user.is_super_admin:
        raise HTTPException(status_code=403, detail="无权访问此会话")


@router.post("")
async def chat(
    req: ChatRequest, user: User = Depends(get_current_user)
) -> dict:
    from app.agent.v2.graph import build_v2_graph, checkpointer_ctx

    logger.info(f"[chat] user={user.username} query={req.query[:50]} thread_id={req.thread_id}")
    if req.thread_id:
        await _verify_session_access(req.thread_id, user)
        thread_id = req.thread_id
    else:
        sid = await memory_agent.create_session(user_id=user.id)
        thread_id = str(sid)
    session_uuid = uuid.UUID(thread_id)

    await memory_agent.add_message_and_maybe_compress(
        session_uuid, MessageRole.user, req.query
    )

    async with checkpointer_ctx() as cp:
        await cp.setup()
        graph = build_v2_graph(cp)
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 50}
        await graph.ainvoke({"user_query": req.query, "user_id": str(user.id)}, config=config)
        state = await graph.aget_state(config)
        intr = _extract_interrupt(state)

    if state.next and intr is not None:
        logger.info(f"[chat] HITL 中断: thread={thread_id} q={intr.get('question', '')[:40]}")
        await memory_agent.add_message_and_maybe_compress(
            session_uuid, MessageRole.assistant, intr.get("question", "")
        )
        return {
            "thread_id": thread_id,
            "status": "need_input",
            "interrupt": intr,
            "user": _user_out(user),
        }
    result = state.values.get("final_result")
    logger.info(f"[chat] 完成: thread={thread_id} sellers={len((result or {}).get('sellers', []))}")
    await memory_agent.add_message_and_maybe_compress(
        session_uuid, MessageRole.assistant, _result_summary(result),
        meta={"sellers": (result or {}).get("sellers", [])},
    )
    return {
        "thread_id": thread_id,
        "status": "done",
        "result": result,
        "user": _user_out(user),
    }


@router.post("/resume")
async def chat_resume(
    req: ResumeRequest, user: User = Depends(get_current_user)
) -> dict:
    from app.agent.v2.graph import build_v2_graph, checkpointer_ctx

    logger.info(f"[chat/resume] user={user.username} thread={req.thread_id}")

    await _verify_session_access(req.thread_id, user)
    thread_id = req.thread_id
    session_uuid = uuid.UUID(thread_id)
    await memory_agent.add_message_and_maybe_compress(
        session_uuid, MessageRole.user, str(req.response)
    )

    async with checkpointer_ctx() as cp:
        await cp.setup()
        graph = build_v2_graph(cp)
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 50}
        await graph.ainvoke(Command(resume=req.response), config=config)
        state = await graph.aget_state(config)
        intr = _extract_interrupt(state)

    if state.next and intr is not None:
        await memory_agent.add_message_and_maybe_compress(
            session_uuid, MessageRole.assistant, intr.get("question", "")
        )
        return {
            "thread_id": thread_id,
            "status": "need_input",
            "interrupt": intr,
            "user": _user_out(user),
        }
    result = state.values.get("final_result")
    await memory_agent.add_message_and_maybe_compress(
        session_uuid, MessageRole.assistant, _result_summary(result),
        meta={"sellers": (result or {}).get("sellers", [])},
    )
    return {
        "thread_id": thread_id,
        "status": "done",
        "result": result,
        "user": _user_out(user),
    }
