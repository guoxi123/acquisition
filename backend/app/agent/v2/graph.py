"""V2 LangGraph 单线流式图：check_quota → parse_intent → query_db
→ acquire_if_needed(判断) → [够: llm_analysis→score→output | 不够: call_actors 子agent → 回 query_db]

HITL 与配额靠 state 字段 + 条件边（无 interrupt、无 checkpointer）：
- check_quota 配额耗尽 → 直转 output_result 提示升级
- parse_intent 信息不全(need_human_confirm) → 直转 output_result 提示补充；前端 SSE 看到
  need_input 后重新 POST 补充 marketplace/category，重跑此图（每次 ainvoke 用全新 state）

call_actors 是子 agent（子图：采集入库 → 查联系方式），与 query_db 经 acquire_if_needed 形成采集循环。
"""

from langgraph.graph import END, START, StateGraph

from app.agent.v2.intent import parse_intent
from app.agent.v2.nodes import (
    acquire_if_needed,
    call_actors,
    check_quota,
    classify_intent,
    direct_llm,
    llm_analysis,
    lookup_contacts,
    output_result,
    query_agent_node,
    query_db,
    score_sellers,
)
from app.agent.v2.state import V2State


def _route_acquire(state) -> str:
    """acquire_if_needed 后的条件路由：够 / 达 max_rounds / 上一轮 0 新增 → llm_analysis；否则 → call_actors 子 agent。"""
    target = state.get("target") or state.get("remaining") or 0
    sellers = state.get("sellers") or []
    fetch_round = state.get("fetch_round") or 0
    max_rounds = state.get("max_rounds") or 3
    last_new = state.get("last_new_count") or 0
    if (
        len(sellers) >= target
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


def build_stream_graph(checkpointer=None):
    """单线流式图：check_quota → parse_intent → query_db → acquire_if_needed(判断)
    → [够: llm_analysis→score→output | 不够: call_actors 子agent → 回 query_db]。

    配额耗尽(check_quota) / 信息不全(parse_intent) 均由条件边直转 output_result 给提示后 END。
    无 checkpointer：HITL 靠前端看到 need_input 后重新 POST 补充，每次 ainvoke 用全新 state。
    """
    builder = StateGraph(V2State)
    builder.add_node("classify_intent", classify_intent)
    builder.add_node("direct_llm", direct_llm)
    builder.add_node("query_agent", query_agent_node)
    builder.add_node("check_quota", check_quota)
    builder.add_node("parse_intent", parse_intent)
    builder.add_node("query_db", query_db)
    builder.add_node("acquire_if_needed", acquire_if_needed)
    builder.add_node("call_actors", build_call_actors_subagent())  # 子 agent
    builder.add_node("llm_analysis", llm_analysis)
    builder.add_node("score_sellers", score_sellers)
    builder.add_node("output_result", output_result)

    builder.add_edge(START, "classify_intent")
    # 前置意图判定：获客→check_quota 主流程；查询→query_agent；其他→direct_llm
    builder.add_conditional_edges(
        "classify_intent",
        lambda s: (
            "check_quota" if s.get("intent") == "acquisition"
            else "query_agent" if s.get("intent") == "query"
            else "direct_llm"
        ),
    )
    builder.add_edge("query_agent", END)
    builder.add_edge("direct_llm", END)
    # 配额耗尽 → output_result 提示升级；否则意图识别（else 去向，勿再 add_edge）
    builder.add_conditional_edges(
        "check_quota",
        lambda s: "output_result" if s.get("quota_exhausted") else "parse_intent",
    )
    # 信息不全(need_human_confirm) → output_result 提示补充；否则查库
    builder.add_conditional_edges(
        "parse_intent",
        lambda s: "output_result" if s.get("need_human_confirm") else "query_db",
    )
    builder.add_edge("query_db", "acquire_if_needed")
    builder.add_conditional_edges("acquire_if_needed", _route_acquire)
    builder.add_edge("call_actors", "query_db")  # 子 agent 完成 → 回 query_db 再判断
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
