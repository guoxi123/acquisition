# 大模型流式输出技术方案

> 版本：1.0
> 核心约束：LLM stream → 写 DB → 前端从 DB 读（DB 作为中转，不直接转发 LLM stream）

---

## 一、架构

```
POST /api/chat/stream（触发，非阻塞）
  │
  ├─ 创建 memory_session + memory_message（content="" 占位）
  ├─ 后台跑 V2 图（asyncio.create_task）
  └─ 返回 {thread_id}

后台图执行：
  llm_analysis 节点
    └─ DeepSeek astream() → 每个 chunk
        └─ UPDATE memory_messages SET content = content || chunk
  完成 → UPDATE meta = {sellers:[...]}, status=done

GET /api/chat/stream/{thread_id}/events（SSE）
  └─ 每 200ms SELECT content FROM memory_messages
      ├─ content 变长 → SSE: {delta: "新增部分"}
      └─ meta 有值 → SSE: {done: true, sellers:[...]} → close

前端：
  POST 触发 → 拿 thread_id
  EventSource(/events) → 增量渲染 → 完成显示表格
```

---

## 二、技术选型

| 层 | 选型 | 理由 |
|------|------|------|
| **LLM 流式** | `ChatOpenAI.astream()`（langchain-openai async generator） | 已用 langchain-openai，原生 async stream，每个 chunk 是 AIMessageChunk |
| **DB 写** | asyncpg `UPDATE memory_messages SET content = content \|\| $1` | chunk 级追加。每 500ms batch flush（避免每 chunk 写一次 DB 太频繁） |
| **SSE 推送** | FastAPI `StreamingResponse(media_type="text/event-stream")` | 原生支持，无需额外库。每个 yield 一条 `data: {...}\n\n` |
| **前端接收** | `EventSource` API（浏览器原生） | 自动重连、简单。或 `fetch` + `ReadableStream`（更灵活） |

---

## 三、三层详解

### 3.1 LLM 异步流式

```python
# llm_analysis 节点改造：支持 chunk 回调
async def llm_analysis_stream(state, on_chunk):
    llm = get_llm()
    accumulated = ""
    async for chunk in llm.astream(prompt):
        text = chunk.content
        accumulated += text
        await on_chunk(text)  # 每 chunk 回调（写 DB）
    return accumulated
```

DeepSeek `astream()` 返回 `AIMessageChunk` 流，`chunk.content` 是文本片段。

### 3.2 数据流式保存到 DB

**策略：batch flush（不是每 chunk 写一次）**

```python
# 后台图执行，on_chunk 累积到 buffer + 定时 flush
buffer = []
last_flush = time.time()

async def on_chunk(text):
    buffer.append(text)
    if time.time() - last_flush > 0.5:  # 500ms flush
        await db.execute(
            update(MemoryMessage)
            .where(MemoryMessage.message_id == msg_id)
            .values(content=MemoryMessage.content + "".join(buffer))
        )
        await db.commit()
        buffer.clear()
        last_flush = time.time()
```

**为什么 batch 500ms**：每 chunk（~几个字）写一次 DB = 每秒几十次 UPDATE（性能差）。500ms batch = 每秒 2 次写，前端体感流畅。

**为什么 UPDATE content 而非 INSERT 新行**：一条 assistant 消息对应一个 memory_messages 行，content 逐段增长（前端轮询 content 长度差 = delta）。

### 3.3 前端 SSE 从 DB 流式获取

```python
@router.get("/stream/{thread_id}/events")
async def stream_events(thread_id: str, user=Depends(get_current_user)):
    async def event_gen():
        last_len = 0
        while True:
            async with async_session() as db:
                msg = await db.get(MemoryMessage, assistant_msg_id)
            if len(msg.content) > last_len:
                delta = msg.content[last_len:]
                last_len = len(msg.content)
                yield f"data: {json.dumps({'delta': delta})}\n\n"
            if msg.meta:  # 完成
                yield f"data: {json.dumps({'done': True, 'sellers': msg.meta.get('sellers', [])})}\n\n"
                return
            await asyncio.sleep(0.2)  # 200ms 轮询

    return StreamingResponse(event_gen(), media_type="text/event-stream")
```

```typescript
// 前端
const es = new EventSource(`/api/chat/stream/${threadId}/events`);
es.onmessage = (e) => {
  const data = JSON.parse(e.data);
  if (data.done) {
    setSellers(data.sellers);  // 表格
    es.close();
  } else {
    setText(prev => prev + data.delta);  // 增量文本
  }
};
```

---

## 四、数据流总览

```
用户输入
  │
  ▼
POST /stream → 创建 session + message(content="")
  │              后台 asyncio.create_task 跑图
  ▼ 返回 thread_id
前端 EventSource(/events)
  │
  ▼ 轮询 DB（200ms）
  │  ┌─ content 增长 → SSE delta → 前端 append
  │  └─ meta 有值 → SSE done + sellers → 前端表格
  │
后台图：
  llm_analysis DeepSeek astream
    chunk → buffer → 500ms flush UPDATE content
  score/output → UPDATE meta={sellers}
```

---

## 五、关键取舍

| 决策 | 选择 | 理由 |
|------|------|------|
| **同进程 vs Arq worker** | 同进程 `asyncio.create_task` | MVP 简单。Arq 更健壮（进程崩溃不丢），但复杂 |
| **chunk 写 DB 频率** | 500ms batch flush | 平衡 DB 负载 + 前端流畅 |
| **SSE 轮询 vs Redis pub/sub** | SSE 轮询 DB（200ms） | 简单（无 Redis pub/sub）。Redis pub/sub 更实时但复杂 |
| **EventSource vs fetch+stream** | EventSource | 原生、自动重连。fetch+stream 更灵活（POST body）但需手动处理 |
| **图非 LLM 节点** | 不流式（采集/评分快速完成） | 只有 llm_analysis 是 LLM 文本生成，值得流式 |

---

## 六、与现有架构的改动点

| 现有 | 改为 |
|------|------|
| `POST /api/chat`（同步等图跑完） | `POST /api/chat/stream`（触发 + 后台跑图 + 返回 thread_id） |
| `llm_analysis`（同步 ainvoke） | `llm_analysis_stream`（astream + on_chunk 回调写 DB） |
| 无 SSE | `GET /api/chat/stream/{id}/events`（SSE 轮询 DB） |
| 前端 fetch 同步等 | 前端 EventSource 接收增量 |
