# 手写 LangGraph ReAct 子图:为什么我没用 create_react_agent

> ReAct 是 LLM Agent 最经典的循环:"想了再调,调完再想"。LangGraph 提供了 `create_react_agent` 一行起 agent,很省心。但我最后手写了一个 ReAct 子图——因为对要长期维护、持续扩展、上生产的 agent,透明和可控比省那 30 行重要得多。本文拆解 ReAct 的本质 + 手写实现 + 和 prebuilt 的取舍。

## 一、ReAct:让 LLM"想了再调,调完再想"

ReAct = Reasoning + Acting。LLM 不直接答,而是循环:推理 → 调工具 → 看结果 → 再推理 → ... → 最终答。

适合"步骤不能预先确定、要 LLM 自己决定调什么工具"的场景。比如"查我获取的中国高分卖家"——可能要 filter(国籍)+ sort(评分),也可能先 count 看有没有,LLM 根据问法自己组合。

LangGraph 提供了 `langgraph.prebuilt.create_react_agent`,一行就能起一个 ReAct agent:

```python
from langgraph.prebuilt import create_react_agent
agent = create_react_agent(llm, tools, prompt=system_prompt)
```

很省心。但我最后没用它,手写了一个 ReAct 子图。为什么?

## 二、场景:可扩展的查询 agent

我的项目里有个"查询线":用户问已获取的数据(我的卖家/筛选/统计),交给一个 query_agent,它能调一组 tools(get_my_sellers / filter_my_sellers / 未来的 count / search_in_stock...)。

这个 agent 要满足:
- 加 tool 不改图、不改意图分类(可扩展)
- 每步能 trace(已接了本地 jsonl trace)
- 能注入 user_id(防越权)
- 未来可能加"调用上限""工具校验""人工审批某次调用"

create_react_agent 一行起来是爽,但上面这些需求,它要么藏起来、要么改起来别扭。

## 三、create_react_agent 的"省心"与代价

prebuilt 把 ReAct 循环全封装了。代价是:

1. **黑盒**:agent 内部怎么循环、状态长啥样、工具怎么调,都得去看源码。线上出问题,定位难。
2. **状态/中间步骤不可插手**:想在"调工具前"加个校验(比如"这个 user_id 只能查自己的")、"调工具后"加个审计,prebuilt 里要 hack。
3. **trace 不统一**:我自己接的 LangChain callback trace 写 jsonl,prebuilt 的执行流要确认能不能被覆盖到。
4. **迭代上限/错误处理**要翻文档找参数,不如自己写的直观。

对一个要长期维护、不断加 tool、上生产的 agent,我宁愿多写 30 行,换全部可控。

## 四、ReAct 的本质:一张两节点循环图

抛开 prebuilt,ReAct 的图结构其实极简——**两个节点 + 一个条件边循环**:

```
START → agent ──(有 tool_calls)──→ tools ──→ agent(回循环)
              └─(无 tool_calls)──→ END
```

- **agent 节点**:把消息(含历史)喂给 LLM(bind 了 tools),LLm 返回要么"要调工具"(tool_calls),要么"最终答案"(纯文本)
- **tools 节点**:执行上一条消息里的 tool_calls,结果作为 ToolMessage 追加回消息列表
- **should_continue**:看最后一条消息有没有 tool_calls,有就回 tools,没有就 END

就这些。手写一遍,以后想加什么都好办。

## 五、手写实现

```python
import json
from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.graph import START, END, StateGraph
from langgraph.graph.message import MessagesState

def build_query_agent(tools, system_prompt: str):
    """手写 ReAct 子图:agent(LLM 决策) ⟷ tools(执行),无 tool_call 即 END。"""
    tool_by_name = {t.name: t for t in tools}

    async def agent(state):
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
            results.append(ToolMessage(
                content=json.dumps(obs, ensure_ascii=False, default=str),
                tool_call_id=tc["id"], name=tc["name"]))
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
    g.add_conditional_edges("agent", should_continue)
    g.add_edge("tools", "agent")
    return g.compile()
```

几个点:

- **MessagesState**:`langgraph.graph.message` 自带,`{messages: Annotated[list, add_messages]}`,每次 return `{"messages": [...]}` 是**追加**(不是覆盖),天然维持对话历史。
- **system_prompt 前置**:每次 agent 节点都拼上 system(含注入的 user_id),LLM 永远知道当前是谁、该把 user_id 传给 tool。
- **async tool**:`@tool` 装饰的 async 函数,用 `await tool.ainvoke(args)`,不阻塞。
- **ToolMessage 的 content**:tool 返回的是 Python 对象(list/dict),转成 JSON 字符串塞 content,LLM 才"读得懂";tool_call_id 必须对上,LangGraph 靠它关联请求和结果。

主图节点调用:

```python
async def query_agent_node(state):
    subgraph = build_query_agent(ALL_TOOLS, system_prompt_with_user_id)
    result = await subgraph.ainvoke({"messages": [HumanMessage(content=state["user_query"])]})
    # 从 result["messages"] 里取最终回答 + tool 结果(如 sellers 列表)
```

## 六、手写 vs prebuilt

| 维度 | create_react_agent | 手写 ReAct |
|---|---|---|
| 上手成本 | 一行 | ~30 行 |
| 透明度 | 黑盒 | 全看得到(两个节点 + 一个路由) |
| 加校验/审计 | hack 参数/源码 | 在 tools 节点前后随便插 |
| trace | 要确认兼容 | 和主图同构,callback 直接覆盖 |
| 迭代上限/错误 | 找参数 | 自己加(node 里计数、try/except) |
| 适合 | 原型/简单场景 | 长期维护、要控的生产 agent |

不是说 prebuilt 不好——**原型阶段用它快速验证**;真要上生产、要持续扩展,手写一次的收益(可控、可观测、可演进)远大于那 30 行的成本。

## 七、几个落地细节

1. **迭代上限**:ReAct 可能死循环(LLM 反复调 tool 不收敛)。子图调用时加 `recursion_limit`:
   ```python
   subgraph.ainvoke(..., config={"recursion_limit": 25})
   ```
2. **工具不存在/报错**:`tool_by_name[tc["name"]]` 可能 KeyError,或 `ainvoke` 抛异常。要 try/except,把错误作为 ToolMessage 回给 LLM(让它自己处理),而不是让整图崩。
3. **并行 tool_calls**:LLM 可能一次返回多个 tool_calls,call_tools 里可顺序执行,也可 `asyncio.gather` 并发。
4. **结构化结果提取**:tool 返回 list/dict,主图节点从 messages 里捞 ToolMessage,JSON 解析回对象给前端用(我在 query_agent_node 专门做这步,把 sellers 提出来写 meta.sellers)。

## 八、总结

ReAct 不是什么黑魔法,本质就是"LLM 决策 ⟷ 工具执行"的循环图。`create_react_agent` 把它封装得很好,但封装的代价是不透明、不好扩展。

手写一遍——两个 async 节点(agent / tools)+ 一个 should_continue 路由——你拿到的是:每一步都能 trace、每一步都能插校验/审计、加 tool 不改图、迭代上限和错误处理自己说了算。

> 框架的 prebuilt 是"快速起跑器",不是"终身伴侣"。Agent 要长期演进时,把 ReAct 这层薄薄的循环握在自己手里,后面所有的需求(观测、权限、限流、审批)都有了落脚点。
