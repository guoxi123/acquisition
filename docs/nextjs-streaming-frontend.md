# Next.js 流式对话前端:SSE 状态机

> 后端用 SSE 推流式增量(参见《流式 SSE 对话》),前端怎么接?一个流式 AI 对话前端,本质上是个状态机:loading → streaming → (need_input | done)。本文讲前端怎么解析 SSE、统一接线、管理状态,以及一个把"发送/补充/断点续传"三种场景收敛到一个函数的技巧。

## 一、问题:流式前端要处理五种事件 + 三种场景

后端 SSE 推五种事件:`delta`(文本增量)、`sellers`(卖家列表)、`progress`(思考过程)、`done`(完成 + 配额/HITL 元信息)、`error`。

前端要在三种场景下接 SSE:
- **发新查询**:POST /stream → 拿 thread_id → 接 SSE
- **HITL 补充**:缺字段弹补充框 → 用户补 → 重新 POST 带补充值 → 再接 SSE
- **断点续传**(刷新/切会话):直接接已有 thread 的 SSE,恢复到当前状态

三种场景逻辑高度重叠(都是"接 SSE + 渲染增量 + 收尾"),写三遍就乱。

## 二、SSE 解析(connectStream + 五个回调)

封装一个 `connectStream` 函数,把"建立 SSE 连接 + 解析事件"和"UI 更新"解耦——前者统一,后者通过回调注入:

```ts
export function connectStream(
  threadId: string,
  onDelta: (text: string) => void,
  onSellers: (sellers: Seller[]) => void,
  onProgress: (steps: ProgressStep[]) => void,
  onDone: (sellers, cancelled, quota) => void,
  onNeedInput: (p: {missing, marketplace, category}) => void,
  onError: (err: string) => void,
): AbortController {
  // fetch SSE,逐行 parse "data: {...}"
  // data.done ? (need_input ? onNeedInput : onDone) : delta/sellers/progress 分发
}
```

解析逻辑(按 SSE 协议,`data: ` 前缀的行):

```ts
const reader = res.body!.getReader();
// 按 \n 切行,data: 开头的 JSON.parse,按字段分发
if (data.done) {
  if (data.need_input) onNeedInput(data);
  else onDone(data.sellers, data.cancelled, data as QuotaMeta);
} else if (data.delta) onDelta(data.delta);
else if (data.sellers) onSellers(data.sellers);
else if (data.progress) onProgress(data.progress);
```

关键:`done` 带 `need_input` 时走补充回调,否则走完成回调——这是 HITL 和正常收尾的分叉点。

## 三、attachStream:三种场景收敛成一个函数

发新查询、HITL 补充、断点续传,接 SSE 后的处理逻辑完全一样(增量渲染 + 收尾)。抽成一个 `attachStream(tid)`,内部调 `connectStream` 并处理所有 UI 更新:

```ts
function attachStream(tid: string) {
  const ctrl = connectStream(
    tid,
    (delta) => setMessages(...)         // 追加文本到当前气泡
    (sellers) => setMessages(...)       // 表格数据
    (progress) => setMessages(...)      // 思考过程
    (_s, _c, quota) => { setLoading(false); setMessages(... quota); refreshSessions(); }
    handleNeedInput,                    // HITL:弹补充框
    (err) => { setMessages(...错误); setLoading(false); }
  );
  streamControllerRef.current = ctrl;
}
```

三种场景都调它:

```ts
// 发新查询
const res = await streamChat(q, threadId);
setMessages([...占位 assistant]);
attachStream(res.thread_id);

// HITL 补充(重新 streamChat 带补充值)
const res = await streamChat(t, threadId, marketplace, category);
setMessages([...占位]);
attachStream(res.thread_id);

// 断点续传(切会话/刷新,直接接已有 thread)
attachStream(sid);
```

抽 attachStream 前,这三个场景各写一遍 connectStream 的回调(几十行重复代码),改一处要改三处。抽完,UI 逻辑集中在一个地方。

## 四、状态机:loading / streaming / need_input / done

一个对话气泡的生命周期:

```
loading(loading=true,气泡空,显示转圈)
  ↓ 收到第一个 delta/progress
streaming(文本逐字增长、思考过程显示)
  ↓ 收到 done
  ├── need_input → 弹补充框(pendingInterrupt),loading=false
  └── 正常 → 显示 sellers 表格 + 配额,loading=false,结束
```

`loading` 状态控制转圈动画 + 输入框禁用;`pendingInterrupt` 控制补充框显示;`done` 关闭一切。

几个细节:
- **loading 占位**:发请求后立即 push 一个空 assistant 气泡,SSE 的 delta 往里填。用户马上看到"AI 在打字",不用等后端。
- **空会话欢迎语**:没消息时显示静态 WELCOME(引导用户怎么问)。
- **过滤空历史**:渲染时过滤"text 空且无 sellers/progress 且非当前 loading 占位"的消息,避免历史空气泡堆积。

## 五、断点续传

刷新页面 / 切换会话,要恢复到该会话当前状态:

1. `loadSession(sid)`:拉历史消息,渲染
2. 看最后一条 assistant 消息的 `meta.done`:没 done(图还在跑)→ `attachStream(sid)` 接 SSE 续上;有 done → 不接(已完成)
3. SSE 首次连接发"快照"(后端把已有 content/sellers 一次推过来),前端填上,然后继续推增量

关键:后端 DB 里有 `done` 标记,前端据此判断"还要不要接 SSE"。没这个标记,刷新后永远不知道图跑完没。

## 六、HITL 补充闭环(详情见《HITL 前端 UI》)

`handleNeedInput` 把 `pendingInterrupt` 设上,输入框切到"补充模式"(placeholder 变成"输入品类,市场用默认或 marketplace=xxx")。用户补充后,`resume()` 重新 `streamChat` 带上补充值 + `attachStream`——走的是和发新查询完全一样的路径(只是带了 marketplace/category 参数)。

## 七、总结

流式 AI 对话前端,核心是把"接 SSE + 更新 UI"抽成一个统一函数(attachStream),让发新查询/HITL 补充/断点续传三种场景复用。状态用 `loading` + `pendingInterrupt` 两个 flag 驱动,覆盖 loading/streaming/need_input/done 四态。

后端 SSE 的五种事件(delta/sellers/progress/done/need_input),前端 connectStream 解析后分发到五个回调,UI 逻辑集中在 attachStream。

> 流式前端的复杂度不在"接 SSE"(就一个 fetch + 按行 parse),在"三种场景共用一套渲染逻辑 + 状态管理"。把重复抽掉(attachStream),把状态理清(loading/pendingInterrupt),剩下的就是直白的回调分发。
