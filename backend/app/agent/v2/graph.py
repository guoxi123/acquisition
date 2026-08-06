"""V2 LangGraph 图：parse_intent → human_confirm(HITL) → check_quota → query_db
→ acquire_if_needed(判断) → [够: llm_analysis→score→output | 不够: call_actors 子agent → 回 query_db]

call_actors 是子 agent（子图：采集入库 → 查联系方式），完成后回到 query_db 再判断，形成采集循环。
带 AsyncPostgresSaver Checkpointer，支持 HITL interrupt/resume。
"""

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph

from app.agent.v2.intent import parse_intent
from app.agent.v2.nodes import (
    acquire_if_needed,
    call_actors,
    check_quota,
    human_confirm,
    llm_analysis,
    lookup_contacts,
    output_result,
    query_db,
    score_sellers,
)
from app.agent.v2.state import V2State


def _route_acquire(state) -> str:
    """acquire_if_needed 后的条件路由：够 / 达 max_rounds / 上一轮 0 新增 → llm_analysis；否则 → call_actors 子 agent。"""
    remaining = state.get("remaining") or 0
    sellers = state.get("sellers") or []
    fetch_round = state.get("fetch_round") or 0
    max_rounds = state.get("max_rounds") or 3
    last_new = state.get("last_new_count") or 0
    if (
        len(sellers) >= remaining
        or fetch_round >= max_rounds
        or (fetch_round > 0 and last_new == 0)
    ):
        return "llm_analysis"
    return "call_actors"


def build_call_actors_subagent():
    """子 agent：call_actors（采集入库）→ lookup_contacts（查联系方式）。
    作为主图一个节点嵌入；完成后由主图边回到 query_db 再判断。"""
    b = StateGraph(V2State)
    b.add_node("call_actors", call_actors)
    b.add_node("lookup_contacts", lookup_contacts)
    b.add_edge(START, "call_actors")
    b.add_edge("call_actors", "lookup_contacts")
    b.add_edge("lookup_contacts", END)
    return b.compile()


def build_v2_graph(checkpointer=None):
    builder = StateGraph(V2State)
    builder.add_node("parse_intent", parse_intent)
    builder.add_node("human_confirm", human_confirm)
    builder.add_node("check_quota", check_quota)
    builder.add_node("query_db", query_db)
    builder.add_node("acquire_if_needed", acquire_if_needed)
    builder.add_node("call_actors", build_call_actors_subagent())  # 子 agent
    builder.add_node("llm_analysis", llm_analysis)
    builder.add_node("score_sellers", score_sellers)
    builder.add_node("output_result", output_result)

    builder.add_edge(START, "parse_intent")
    builder.add_edge("parse_intent", "human_confirm")
    builder.add_conditional_edges(
        "human_confirm",
        lambda s: "check_quota" if s.get("human_approved") else END,
    )
    # 配额耗尽 → 直转 output_result 提示升级；否则查库
    builder.add_conditional_edges(
        "check_quota",
        lambda s: "output_result" if s.get("quota_exhausted") else "query_db",
    )
    builder.add_edge("query_db", "acquire_if_needed")
    # 判断：够 → llm_analysis；不够 → call_actors 子 agent
    builder.add_conditional_edges("acquire_if_needed", _route_acquire)
    builder.add_edge("call_actors", "query_db")  # 子 agent 完成 → 回 query_db 再判断
    builder.add_edge("llm_analysis", "score_sellers")
    builder.add_edge("score_sellers", "output_result")
    builder.add_edge("output_result", END)

    return builder.compile(checkpointer=checkpointer) if checkpointer else builder.compile()


def checkpointer_ctx():
    """返回 AsyncPostgresSaver 的 async context manager。

    from_conn_string 返回 context manager，需 async with 进入后 setup()。
    用法：async with checkpointer_ctx() as cp: await cp.setup(); graph = build_v2_graph(cp)
    """
    from app.core.config import settings

    # psycopg 要纯 postgresql://，去掉 SQLAlchemy 的 +asyncpg 方言
    pg_url = settings.database_url.replace("+asyncpg", "")
    return AsyncPostgresSaver.from_conn_string(pg_url)


def build_stream_graph(checkpointer=None):
    """流式图：check_quota → query_db → acquire_if_needed(判断)
    → [够: llm_analysis→score→output | 不够: call_actors 子agent → 回 query_db]。

    不含 parse_intent/human_confirm（POST /stream 同步处理意图 + HITL）。
    call_actors 是子 agent（采集 → 查联系方式），与 query_db 经 acquire_if_needed 形成循环。
    """
    builder = StateGraph(V2State)
    builder.add_node("check_quota", check_quota)
    builder.add_node("query_db", query_db)
    builder.add_node("acquire_if_needed", acquire_if_needed)
    builder.add_node("call_actors", build_call_actors_subagent())  # 子 agent
    builder.add_node("llm_analysis", llm_analysis)
    builder.add_node("score_sellers", score_sellers)
    builder.add_node("output_result", output_result)
    builder.add_edge(START, "check_quota")
    builder.add_conditional_edges(
        "check_quota",
        lambda s: "output_result" if s.get("quota_exhausted") else "query_db",
    )
    builder.add_edge("query_db", "acquire_if_needed")
    builder.add_conditional_edges("acquire_if_needed", _route_acquire)
    builder.add_edge("call_actors", "query_db")  # 回边
    builder.add_edge("llm_analysis", "score_sellers")
    builder.add_edge("score_sellers", "output_result")
    builder.add_edge("output_result", END)
    return builder.compile(checkpointer=checkpointer) if checkpointer else builder.compile()


# 单例：图编译一次，复用（LangGraph 图是无状态的，可并发 ainvoke）
_stream_graph = None


def get_stream_graph():
    global _stream_graph
    if _stream_graph is None:
        _stream_graph = build_stream_graph()
    return _stream_graph
