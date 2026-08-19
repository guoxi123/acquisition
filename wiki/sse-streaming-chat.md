# 流式 SSE 对话:后端跑图、前端实时看

> 用户发一句话,Agent 要跑好几个节点(意图识别、查库、可能采集、评分、生成回复),整个流程可能几秒到几十秒。如果等全部跑完再返回,用户体验极差(一直转圈)。本文讲怎么用"图写 DB + SSE 轮询推增量"实现流式输出:思考过程、卖家结果、最终回复,边产生边显示。

## 一、问题:Agent 跑得慢,用户等不及

一次获客查询,后端要:意图识别(1 次 LLM)→ 查库 → 不够采集(Apify,慢)→ LLM 分析 → 评分 → LLM 生成回复。快则 3 秒,慢则 30 秒+。

如果用传统的"请求 → 等响应"模式,用户发完请求要盯着 loading 转几十秒,不知道后端在干嘛,体验崩。

要的是**流式**:后端边跑边把进度推给前端——"正在解析意图""查到 10 个""采集第 2 轮""最终回复逐字出来"。

## 二、为什么不用 WebSocket

流式推送,第一反应是 WebSocket(双向长连接)。但这里后端→前端的推送是单向的,而且 Agent 跑在后台 task 里,和 HTTP 请求不同生命周期。

Server-Sent Events(SSE)更合适:单向、基于 HTTP、浏览器原生支持、断线自动重连简单。关键是**实现简单**——一个普通 GET endpoint,把响应设成 `text/event-stream`,持续 yield 数据就行。

## 三、方案:图写 DB + SSE 轮询推增量

核心设计:**图的节点边跑边把中间结果写进 DB,前端用 SSE 轮询 DB 把增量推过去**。

```
POST /stream(起后台 task 跑图,立即返回 thread_id)
   ↓
后台 task: graph.ainvoke(...)  ← 节点往 memory_messages 表写 content/progress/sellers/done
   ↓
GET /stream/{thread_id}/events(SSE)  ← 每 200ms 轮询 DB,推增量给前端
```

为什么让图写 DB、SSE 轮询,而不是图直接推 SSE?因为**解耦**:图的执行(后台 task)和 SSE 连接(前端持有)是两个独立的东西,SSE 断了图还在跑、图跑完 SSE 重连还能拿全量。DB 是它们的中介。

## 四、后端实现

### 1. POST /stream:起后台 task

```python
@router.post("")
async def chat_stream(req, user):
    # 建会话、建 assistant 占位消息
    assistant_msg_id = await add_message(session, assistant, "")
    state = {... "assistant_msg_id": str(assistant_msg_id), ...}
    task = asyncio.create_task(_run_stream_graph(thread_id, str(assistant_msg_id), state))
    _tasks[thread_id] = (task, str(assistant_msg_id))
    return {"status": "streaming", "thread_id": thread_id, "msg_id": str(assistant_msg_id)}
```

立即返回,不等图跑完。图在后台 task 里跑,节点用 `_write_msg` 往 DB 写。

### 2. 节点写 DB(增量)

节点不直接推前端,而是更新 DB 里那条 assistant 消息的 `meta` 和 `content`:

```python
# update_progress:往 meta.progress 追加一条进度
await update_progress(msg_id, "parse_intent", "正在解析意图…", "running")

# _write_msg:合并写 content + meta(保留已有字段)
await _write_msg(msg_id, content="部分回复…", meta_patch={"sellers": [...], "done": False})
```

### 3. GET /events:SSE 轮询推增量

```python
@router.get("/{thread_id}/events")
async def stream_events(thread_id, user):
    async def event_gen():
        last_len = 0
        last_sellers_len = 0
        sent_snapshot = False
        while True:
            msg = 查 DB 最后一条 assistant 消息
            content = msg.content or ""
            meta = msg.meta or {}

            # 首次连接:发快照(断点续传用)
            if not sent_snapshot:
                if content: yield delta(content); last_len = len(content)
                ...
                sent_snapshot = True

            # content 增长 → 推 delta
            if len(content) > last_len:
                yield delta(content[last_len:]); last_len = len(content)

            # sellers 增长 → 推全量 sellers(前端表格逐行渲染)
            sellers = meta.get("sellers", [])
            if len(sellers) > last_sellers_len:
                yield sellers(sellers); last_sellers_len = len(sellers)

            # done → 推收尾 payload,关闭
            if meta.get("done"):
                yield done_payload(sellers, meta); return

            await asyncio.sleep(0.2)

    return StreamingResponse(event_gen(), media_type="text/event-stream")
```

## 五、四种增量

SSE 推四种事件,前端分别处理:

| 事件 | 含义 | 前端动作 |
|---|---|---|
| `delta` | content 的增量文本 | 追加到当前气泡 |
| `sellers` | 卖家列表(全量) | 表格逐行渲染 |
| `progress` | 思考过程步骤 | 折叠面板显示进度 |
| `done` | 完成(含 sellers/cancelled/quota/need_input) | 收尾,关连接 |

`done` payload 还透传配额信息(`exhausted`/`upgrade_available`)和 HITL 标记(`need_input`/`missing`),前端据此显示升级提示或弹补充框。

## 六、断点续传

用户刷新页面 / SSE 断线重连,会重新 GET /events。首次连接时 `sent_snapshot` 逻辑把 DB 里已有的 content/sellers/progress **一次性快照**推给前端,避免空白;之后继续推增量。

关键是 DB 里有 `done` 标记:图跑完了 done=True,SSE 重连后读到 done,推一次 done payload 就关闭,不会卡 loading。

## 七、总结

流式 Agent 对话,不一定非要 WebSocket。用"图写 DB + SSE 轮询推增量",实现简单、解耦清晰、天然支持断点续传:

- 后端:节点写 DB(content/progress/sellers/done),SSE endpoint 轮询推增量
- 前端:SSE 解析四种事件,边收边渲染
- 断线:重连读快照 + 继续,done 标记决定收尾

> 流式的本质是"把一个长任务拆成可见的增量"。DB 是增量的持久化中介——它让"生产增量的图"和"消费增量的 SSE"彻底解耦,各跑各的,互不阻塞。
