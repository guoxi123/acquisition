"""query_agent：手写 LangGraph ReAct 子图（不依赖 prebuilt create_react_agent）。

结构：agent（LLM + bind_tools，决定调工具或直接答）⟷ tools（执行 tool_calls），
有 tool_call 就回 agent 继续推理，没有就 END。和主图同构、可 trace、可扩展中断/校验。
"""

import json

from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.graph import START, END, StateGraph
from langgraph.graph.message import MessagesState

from app.agent.llm import get_llm


def build_query_agent(tools, system_prompt: str):
    """构建一个 ReAct 子图：tools 为可调用工具集，system_prompt 注入身份与 user_id。"""
    tool_by_name = {t.name: t for t in tools}

    async def agent(state):
        # 把 system prompt（含 user_id）前置拼到消息列表
        llm = get_llm(temperature=0).bind_tools(tools)
        messages = [SystemMessage(content=system_prompt)] + list(state["messages"])
        resp = await llm.ainvoke(messages)
        return {"messages": [resp]}

    async def call_tools(state):
        last = state["messages"][-1]
        results = []
        for tc in last.tool_calls:
            tool = tool_by_name[tc["name"]]
            obs = await tool.ainvoke(tc["args"])
            results.append(
                ToolMessage(
                    content=json.dumps(obs, ensure_ascii=False, default=str),
                    tool_call_id=tc["id"],
                    name=tc["name"],
                )
            )
        return {"messages": results}

    def should_continue(state):
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None):
            return "tools"
        return END

    g = StateGraph(MessagesState)
    g.add_node("agent", agent)
    g.add_node("tools", call_tools)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", should_continue)  # 有 tool_call → tools，否则 END
    g.add_edge("tools", "agent")  # 执行完回 agent 继续推理
    return g.compile()
