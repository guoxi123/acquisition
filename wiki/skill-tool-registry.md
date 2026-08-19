# 可扩展的 Skill/Tool 注册表:加功能不改图

> Agent 上线后,"加个新查询功能"是最常见的需求。如果每次都要改意图分类、改图、改节点,架构很快会腐化。本文讲一套"加功能 = 加一个函数"的设计:工具注册表 + tool-calling agent。它让 query 线从 1 个工具长到 N 个,全程不动图、不动 Router。

## 一、问题:加一个查询功能要改多少

最初 query 线只有一个能力:"查看我获取的所有卖家"。实现是一个 `get_my_sellers` 工具 + 一个 ReAct agent。

很快需求来了:
- "我获取的**中国**卖家" → 要按国籍筛
- "统计我的卖家" → 要计数
- "我获取的高分卖家" → 要按评分筛

如果每个都加一个意图类 + 一个节点,意图分类从 3 类涨到 N 类,图越来越臃肿,Router 越来越难准。这违背"对扩展开放、对修改关闭"。

## 二、思路:工具注册表 + tool-calling agent

换个角度:query 线其实只做一件事——"根据用户的查询,调用合适的工具查数据,然后回答"。**具体调哪个工具,让 LLM 自己决定**。

这样:
- query 这一类是个"大桶",所有"查已获取数据"的需求都进它
- 框架是固定的(tool-calling ReAct)
- 加功能 = 加一个工具函数,LLM 看 description 自己会用

意图分类不用改(query 统一进),图不用改(ReAct 循环固定),只往"工具箱"里加东西。

## 三、实现

每个工具是一个 `@tool` 装饰的纯函数,name/description/参数 schema 从函数签名和 docstring 自动推断:

```python
# skills.py
@tool
async def get_my_sellers(user_id: str, limit: int = 100) -> list:
    """查看当前用户已获取过的所有亚马逊卖家。
    用户问"我的卖家/我获取的/收藏的卖家"时调用。user_id 系统注入。"""
    ...

@tool
async def filter_my_sellers(user_id: str, country: str = "", min_score: int = 0, category: str = "", limit: int = 100) -> list:
    """按条件筛选当前用户已获取的亚马逊卖家(国籍/评分/品类)。
    用户问"我获取的中国卖家/高分卖家/某品类的卖家"时调用。"""
    ...

# 注册表:query_agent 用这里的全部工具
ALL_TOOLS = [get_my_sellers, filter_my_sellers]
```

query_agent(一个手写 ReAct 子图,详见同系列《手写 LangGraph ReAct 子图》)创建时把 `ALL_TOOLS` 全注册给 LLM:

```python
subgraph = build_query_agent(ALL_TOOLS, system_prompt)
```

LLM 拿到所有工具的 description,根据用户查询自己选:
- "查看我的卖家" → `get_my_sellers`
- "我获取的中国卖家" → `filter_my_sellers(country="China")`

## 四、加新 skill 的流程

以后要加"统计我的卖家数量",就三步:

1. 在 `skills.py` 写个 `@tool` 函数 `count_my_sellers`,写好 docstring(告诉 LLM 什么时候调)
2. 加进 `ALL_TOOLS` 列表
3. 完了

不改 `classify_intent`(query 统一进)、不改图(ReAct 循环固定)、不改主图节点。LLM 看到 `count_my_sellers` 的 description,用户问"我有多少卖家"时自然会调。

这是"对扩展开放(加函数)、对修改关闭(不动现有结构)"在 agent 架构里的落地。

## 五、为什么工具必须是纯函数

每个 `@tool` 都设计成**纯查库函数**:输入参数(含 user_id),输出结构化数据(list/dict)。好处:

1. **可单测**:不用起 agent,直接 `await get_my_sellers.ainvoke({"user_id":...})` 验证返回对不对。
2. **可复用**:这个函数既能被 ReAct agent 调用,也能被固定节点直接调,还能未来注册到别的 agent。
3. **关注点分离**:工具只管"查数据",怎么组合、怎么回答交给 agent/LLM。

## 六、一个隐含的安全点:user_id 注入

所有工具都强制带 `user_id` 参数,而且是从登录态注入(在 query_agent 的 system prompt 里告诉 LLM"当前 user_id=xxx,调工具时传进去"),**不让 LLM 问用户、不让 LLM 瞎编**。这样工具查的永远是当前用户自己的数据,不会越权。

这是 tool-calling agent 上生产必须守的底线:**工具的权限边界,靠参数强制,不靠 LLM 自觉**。

## 七、总结

"加查询功能"在 agent 项目里是高频需求。如果每次都改架构,系统很快变怪物。用"工具注册表 + tool-calling agent",把"加功能"从改架构降级成加函数:

- 工具 = `@tool` 纯函数(可测、可复用)
- 注册表 = `ALL_TOOLS` 列表(加一个就多一个能力)
- agent = 固定的 ReAct 循环(LLM 看 description 自选工具)

意图分类不动、图不动,只往工具箱加东西。

> Agent 的可扩展性,不在图有多复杂,而在"加一个能力要动几处"。理想状态是动一处(加个函数)。工具注册表就是奔着这个状态去的。
