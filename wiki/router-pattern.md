# Router 模式:一次 LLM 调用给 Agent 请求分流

> 一个 Agent 系统会收到形形色色的请求:有的要执行重任务(采集),有的只是查查已有数据,有的就是闲聊。如果所有请求都走同一条重流程,既慢又贵。Router 模式用一次 LLM 调用把请求分类,导向最合适的处理线——这是 Anthropic《Building Effective Agents》里最实用的工作流模式之一。本文讲它的落地和一个关键取舍。

## 一、问题:不该所有请求都走重流程

获客 Agent 收到的请求大致三类:

- **获客采集**:"美国站卖杯子的中国卖家"——要查库、不够还要 Apify 采集、评分、发配额。重流程,耗时费钱。
- **查询已获取**:"查看我的卖家"/"我获取的中国卖家"——只读已落库的数据,不该触发采集。
- **闲聊/问功能**:"你是谁""能做什么"——一次 LLM 调用就能答。

如果这三类都进同一条采集流程:闲聊也走意图识别→查库→采集判定,白跑一堆节点;查询已获取的也可能误触采集(花钱)。正确做法是**先分流,各走各的**。

## 二、Router 是什么

Anthropic 把 LLM 应用分成 **workflow**(预定义路径)和 **agent**(LLM 自主决策)。Router 是最常用的 workflow 之一:

> 一个 LLM 调用,把输入分类,路由到不同的下游处理程序。

它不是"自主 agent",是"用 LLM 做一次判断,然后走预定义的分支"。好处是可控、可预测、省 token——因为分类之后,每条分支都是确定的处理流程。

## 三、我的三分流

```
classify_intent (一次 LLM 调用)
   ├── acquisition → check_quota → parse_intent → query_db → ...(采集主流程)
   ├── query       → query_agent (ReAct + tools,查已获取数据)
   └── chat        → direct_llm (系统提示词直接答)
```

- `acquisition` 走完整的获客采集图(会花钱调 Apify)
- `query` 走 query_agent(只读、永不采集)
- `chat` 走 direct_llm(单次 LLM 回答身份/功能)

每条分支用最适合它的形态:采集要可预测(workflow)、查询要灵活(ReAct agent)、闲聊要简单(单 LLM)。这正是 Router 的价值——**让每种请求都用最合适的处理方式**。

## 四、实现

意图分类节点:

```python
class IntentClass(BaseModel):
    intent: Literal["acquisition", "query", "chat"] = Field(default="acquisition")

classify_prompt = """判断用户查询的意图类别:
- acquisition:找/搜/采集【新】卖家(出现目标市场、品类、找卖家、FBA 等,需要新数据)
- query:查看/筛选/统计用户【已获取】的卖家("我的卖家/我获取的/筛选"等)
- chat:其他(问候、闲聊、问你是谁/能做什么)
只返回 intent 字段。"""

async def classify_intent(state):
    result = await _structured_invoke(IntentClass, [...], "classify_intent")
    intent = result.intent if result else "acquisition"  # 失败默认走最稳的主流程
    return {"intent": intent}
```

图的条件边:

```python
builder.add_conditional_edges(
    "classify_intent",
    lambda s: (
        "check_quota" if s.get("intent") == "acquisition"
        else "query_agent" if s.get("intent") == "query"
        else "direct_llm"
    ),
)
```

## 五、为什么用 LLM 分类,不用关键词规则

一个常见疑问:分类这步非用 LLM 不可吗?关键词规则("含'我的'→query")不行吗?

能用,但不稳。自然语言变体太多:
- "帮我看看之前查过的卖家"——没"我的",但该是 query
- "找一下我获取的中国卖家"——有"找"(像 acquisition),但实际是 query(查已获取)
- "你能帮我找美国站的吗"——有"你"(像 chat),但实际是 acquisition

规则要覆盖这些,会写成一堆 if-else 补丁,而且新说法一来就漏。LLM 一次调用,理解语义,泛化好。代价是一次 LLM 调用(便宜的分类模型够用),值。

## 六、踩过的坑

1. **分类失败要兜底**:`_structured_invoke` 可能返回 None(模型抽风/网络),这时默认走最稳的主流程(`acquisition`),而不是报错或走 `chat`(那样用户啥也得不到)。
2. **加新意图类不要乱动 classify**:我后来加 query 类时,只改了 prompt 和 IntentClass 的 Literal,没动 Router 的条件边逻辑结构——分支是数据驱动的(`s.get("intent")`),不是硬编码 if-else。
3. **eval 守准确率**:classify 是整个系统的入口,分错了一切都错。给它配一组测试集(参见同系列《Agent eval:意图分类准确率》),改 prompt 后跑一遍,防退化。

## 七、总结

Router 是 Agent 系统的"前台":一次 LLM 调用把请求分到专职处理线。它让每种请求都用最合适的形态(采集走 workflow、查询走 agent、闲聊走单 LLM),可控、省、快。

关键取舍:
- 用 LLM 分类(语义泛化)不用规则(变体太多)
- 失败默认走最稳的主流程
- 入口分类配 eval 守准确率

> Anthropic 的五个 workflow 模式里,Router 是投产率最高的一个。只要你的 Agent 要处理"不止一种请求",都该先想 Router——而不是把所有逻辑塞进一个又大又全的 agent。
