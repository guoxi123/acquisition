# 两级会话压缩:让 Agent 记住长对话又不爆 token

> LLM Agent 的记忆有两个坑:只传当前问题→失忆;全传历史→token 爆。本文讲一套"原始不动 + 两级压缩 + 可追溯"的记忆系统:layer_2 把原文压成摘要、layer_3 在摘要累积时再合并成全局摘要,让 LLM 拿到的上下文永远有界,而且原始消息一条不丢、随时可回溯。

## 一、问题:失忆 与 token 爆

Agent 上线初期,每个请求只把当前 user_query 喂给 LLM:

```python
llm.astream([System, HumanMessage(user_query)])
```

LLM 每次"失忆":用户问完"你是谁",再问"那能做什么",它不知道上文,回答重复或不连贯。

简单修:把历史全塞进去。但长对话几万 token,超窗口 + 费钱。

只取最近 N 条?丢早期上下文("你之前说的方案二呢")。

需要一个**压缩**机制:早期压成摘要(省 token),近期保留原文(保细节)。

## 二、设计思路:三个原则

1. **原始消息永不修改**(Source of Truth)。所有压缩都是衍生视图,不破坏原文,只更新 `is_compressed` 标记。
2. **两级压缩**。layer_2(原文→摘要)+ layer_3(摘要过多→再合并)。token 有界。
3. **可追溯**。映射表记录"原始消息 ↔ 压缩版本",即使喂 LLM 的是摘要,也能随时取回原文。

## 三、数据模型(4 张表)

```
memory_sessions              会话(统计:total_messages / total_tokens)
memory_messages              原始消息(role/content/is_compressed)——永不改
memory_compression_versions  压缩版本(strategy / status / compressed_content)
memory_message_compression_map  原始消息 ↔ 压缩版本(多对多,可追溯)
```

关键设计:**不额外加 layer 列,复用现有字段**:
- `compression_versions.strategy`:`summary` = layer_2,`hierarchical` = layer_3
- `status`:`active`(在用)/ `archived`(被 layer_3 合并,get_context 不再取)

省掉一次 migration。

## 四、layer_2:原文 → 摘要

**触发**:未压缩消息 ≥ 16 条(保留最近 4 条不压)。

```python
async def compress(session_id):
    msgs = 取未压缩消息(排除最近 4)
    dialog = "\n".join(f"{m.role}: {m.content[:200]}" for m in msgs)
    summary = await llm.ainvoke("压缩成摘要,保留需求/方案/决策,限300字:" + dialog)
    建 version(strategy=summary, status=active) + 映射 + 标记 msgs.is_compressed=True
```

**增量**:只压未压缩的(已压过的不重复)。每 12 条(16−4)产生一个 layer_2 summary,~300 字。

## 五、layer_3:摘要 → 全局摘要(收敛的关键)

### 问题:layer_2 也会累积

每 12 条加一个 layer_2。长对话(100+ 条)会累积 8 个 layer_2,光摘要就 2400 字,token 又涨。

### 解决:layer_2 active ≥ 3 → 合并最老 3 个

```python
async def compress_layer_3(session_id):
    l2 = 取最老 3 个 layer_2(summary, active)
    summaries = "\n\n".join(f"[摘要{v.version_number}] {v.compressed_content}" for v in l2)
    layer3 = await llm.ainvoke("合并成全局摘要,保留主线/结论,丢细节,限300字:" + summaries)
    建 version(strategy=hierarchical, status=active)
    被合并的 3 个 layer_2 标 status=archived   # get_context 不再取
```

layer_3 的 prompt 比 layer_2 更**抽象**:它压的是"摘要的摘要",要保留**主线**(用户目标/已定方案/结论),丢细节(细节在 layer_2/原文里)。

### 收敛效果

被合并的 layer_2 标 archived,不再取;新对话继续产生 layer_2,累积到 3 再合并。

```
对话 52 条 → layer_2 有 3 个 → 触发 layer_3:
  合并 layer_2_v1~v3 → layer_3_v1
  v1~v3 标 archived
对话 64 条 → layer_2_v4(新产生)
对话 76 条 → layer_2_v5
对话 88 条 → layer_2_v6 → 又 3 个 → 再触发 layer_3
  合并 v4~v6 → layer_3_v2(或并入 v1)
```

**layer_2 active 永远 < 3,layer_3 永远 ~1 个。**

## 六、get_context:分层取

```python
async def get_context(session_id, max_summaries=5):
    l3 = 取最近 1 个 layer_3(hierarchical, active)          # 全局摘要
    l2 = 取最近 max_summaries 个 layer_2(summary, active)    # 近期摘要(archived 的不取)
    recent = 取最近 N 条未压缩原文                             # layer_1
    return {"summaries": l3 + l2, "recent_messages": recent}
```

三层粒度,递减:

| 层 | 内容 | 粒度 |
|---|---|---|
| layer_3(全局摘要) | 主线/结论 | 最粗(~300字) |
| layer_2(近期摘要) | 前几轮概要 | 中(~300字/个) |
| recent(原文) | 最近 4 条完整 | 最细 |

类似人脑:刚说的全记 → 昨天的记大概 → 上个月的记要点。

## 七、接到 agent(让记忆真生效)

光有 get_context 不够,得让 LLM 调用时真用上。封装一个 helper:

```python
async def _history_messages(session_id):
    ctx = await get_context(session_id)
    summaries = "\n\n".join(s["content"] for s in ctx["summaries"])   # 摘要合并成文本
    msgs = []
    for m in ctx["recent_messages"]:
        if m["role"] == "user":    msgs.append(HumanMessage(m["content"]))
        elif m["role"] == "assistant" and m["content"]:  # 过滤空占位
            msgs.append(AIMessage(m["content"]))
    return summaries, msgs
```

direct_llm / query_agent_node 调它:

```python
summaries, history = await _history_messages(state["session_id"])
sys = DIRECT_SYSTEM_PROMPT + (f"\n\n【之前对话摘要】\n{summaries}" if summaries else "")
messages = [SystemMessage(sys)] + history   # history 末条是当前 user_query
await llm.astream(messages)
```

**短对话**(没触发压缩):get_context 只返回原文(无摘要)→ 等同 token_window,零额外 LLM 成本。
**长对话**:自动带 layer_3 + layer_2 + 原文。

一套实现,自适应两种模式。

## 八、收敛分析:token 有界

| 对话长度 | layer_2 数 | layer_3 数 | summaries token |
|---|---|---|---|
| < 16 条 | 0 | 0 | 0(只原文) |
| ~28 条 | 1 | 0 | ~300 字 |
| ~52 条 | 2 | 0 | ~600 字 |
| ~64 条 | 3 → 合并 | 1 | ~300(layer_3)+ 0(layer_2 全 archived)|
| 100+ 条 | ≤ 2(不断合并) | 1 | **≤ ~900 字(有界)** |

不管对话多长,发给 LLM 的 summaries **永远 ≤ 1 个 layer_3(300) + 2 个 layer_2(600)**,加上最近 4 条原文。token 有上界。

## 九、两个实现细节

### 1. 过滤空 assistant 占位

chat_stream 每次请求会先建一条空 assistant 消息(占位,图边跑边写 content)。get_context 的 recent 会包含它。_history_messages 过滤 `assistant and content`(空的不加),否则 LLM 收到末尾一条空 assistant 会困惑。

### 2. session_id 注入

state 里加 `session_id`(chat_stream 注入 thread_id),_history_messages 用它取该会话的历史。没 session_id(异常)时 fallback 到只传 user_query。

## 十二、总结

两级压缩让 Agent 记住长对话又不爆 token:

- **layer_2**(原文→摘要):增量压缩,保留语义概要
- **layer_3**(摘要→全局摘要):合并收敛,token 有界——这是防"摘要自身累积"的关键
- **可追溯**:原始不动 + 映射表,喂 LLM 的是摘要也能回溯原文
- **分层取**:全局→近期→原文,粒度递增,自适应对话长度

实现路径:summary 接 agent(记忆生效)→ summary 上限(防累积)→ layer_3 合并(收敛)。每步都有明确价值,不堆复杂度。

> 记忆系统的核心不是"压得多狠",是"token 有界 + 信息不丢"。两级压缩用"摘要的摘要"收敛 token,用"原始不动 + 映射"保证可追溯——长对话下,LLM 永远拿到有界的、递减粒度的上下文。
