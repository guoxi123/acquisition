# HITL 前端 UI:缺字段弹补充框,补完重发

> 后端用"轻量 HITL"(状态字段 + SSE need_input,不用 interrupt,详见同系列《轻量 HITL》)实现了"缺信息就问"。但那只解决了后端怎么"把问题传出来"。前端要接住:看到 need_input → 弹补充框 → 用户补完 → 重新发请求带补充值。本文讲这个前端闭环。

## 一、问题:前端怎么接住"缺字段"信号

后端的轻量 HITL,落在前端就是:一次查询的 SSE done payload 里,如果带了 `need_input: true` + `missing: [...]`,说明 Agent 在说"我缺这些字段,补完再来"。

前端要做三件事:
1. 看到 need_input → 切换到"补充模式"(弹补充输入框)
2. 用户补充 → 把补充值组装好
3. 重新发请求,带上补充值 → Agent 重跑,这次字段齐了

而且整个过程不能让用户觉得"出了个错",要像自然的对话:"AI 问我要市场,我填,它接着查"。

## 二、pendingInterrupt:驱动补充框的 state

加一个 state 记录"当前在等用户补充什么":

```ts
const [pendingInterrupt, setPendingInterrupt] = useState<{
  question: string;
  current: { marketplace?: string; category?: string };
} | null>(null);
```

SSE 的 done 带 need_input 时,handleNeedInput 把它设上:

```ts
function handleNeedInput(p: {missing, marketplace, category}) {
  const missing = p.missing?.length ? p.missing : ["目标市场", "产品品类"];
  setPendingInterrupt({
    question: `请补充:${missing.join("、")}`,
    current: { marketplace: p.marketplace, category: p.category },
  });
  setLoading(false);   // 停转圈(不是在跑,是在等输入)
}
```

`pendingInterrupt` 非空 → 输入框区域渲染"补充模式":

```tsx
{pendingInterrupt ? (
  <补充输入框 placeholder="输入品类(如 cups),市场用默认或 marketplace=amazon.co.uk" />
) : (
  <普通输入框 placeholder="美国站卖杯子的2个中国卖家" />
)}
```

用户看到的是:AI 说了一句"请补充:目标市场",下面输入框变成补充模式,提示怎么填。

## 三、resume:补完重新发(带注入值)

用户在补充框输入,点提交(或回车),触发 `resume()`。它做两件关键事:

### 1. 解析用户输入,组装补充值

用户可能只填品类("cups"),也可能用结构化格式("marketplace=amazon.co.uk,category=cups"):

```ts
async function resume() {
  const t = input.trim();
  let marketplace, category;
  if (t.startsWith("marketplace=")) {
    // 结构化:marketplace=xxx,category=yyy
    const [mp, cat] = t.split(",");
    marketplace = mp.split("=")[1]?.trim();
    if (cat?.includes("=")) category = cat.split("=")[1]?.trim();
  } else {
    // 纯品类:市场用 pendingInterrupt 里已有的(或让后端再判)
    category = t;
    marketplace = pendingInterrupt?.current?.marketplace;
  }
  ...
}
```

### 2. 重新 streamChat,带上补充值

关键:不是调什么 /resume 接口,而是**重新 POST /stream,把 marketplace/category 作为参数带上**:

```ts
const res = await streamChat(t, threadId, marketplace, category);
setMessages([...用户消息]);
setMessages([...空 assistant 占位]);
attachStream(res.thread_id);   // 接 SSE,和发新查询完全一样
```

`streamChat` 的签名支持可选 marketplace/category:

```ts
async function streamChat(query, threadId?, marketplace?, category?) {
  return fetch("/api/chat/stream", {
    body: JSON.stringify({ query, thread_id: threadId, marketplace, category }),
  });
}
```

后端 endpoint 收到带 marketplace/category 的请求,把它们塞进 state,图重跑(parse_intent 里 override,用户给的值优先)。这次字段齐了,need_confirm=False,正常往下查。

## 四、为什么"重新发"而不是"恢复"

标准 HITL(interrupt/resume)是"暂停图 → 用户补 → 从断点继续"。轻量 HITL 是"图正常结束 → 用户补 → 重新发,图从头跑"。

前端视角,两者区别:
- interrupt/resume:调 /resume 接口,带 Command(resume=...),后端从 checkpoint 续
- 轻量:调 /stream 接口(同一个!),带 marketplace/category,后端从头跑

前端代码更简单——**补充和发新查询走同一个 endpoint(/stream)、同一个函数(streamChat + attachStream)**,只是多带两个参数。没有 /resume、没有 Command、没有 interrupt 协议。

代价是图重跑(多一次意图识别 LLM 调用),但对"缺字段补完重跑"这种场景,成本可接受。

## 五、完整闭环

```
用户:"卖杯子"
  ↓ POST /stream
后端:意图识别缺市场 → need_input:true, missing:["目标市场"]
  ↓ SSE done
前端:handleNeedInput → setPendingInterrupt → 输入框变补充模式
  ↓ 用户填"美国站"
前端:resume() → streamChat("美国站", threadId, marketplace="amazon.com") → attachStream
  ↓ POST /stream(带 marketplace)
后端:parse_intent override(marketplace=amazon.com)→ 字段齐 → 查库 → 结果
  ↓ SSE done(正常,无 need_input)
前端:onDone → 显示 sellers + 配额,结束
```

整个闭环,前端只用了"重新 streamChat + attachStream",和发新查询的代码路径一致。pendingInterrupt 只是控制输入框显不显示补充提示。

## 六、一个细节:补充时不重复存用户消息

后端有个逻辑:如果请求带 thread_id 且带了 marketplace/category(说明是补充重跑),就**不重复往会话存用户消息**(原 query 上轮已经存了),只建新的 assistant 占位。否则补充"美国站"会在会话历史里多一条孤立的"美国站",上下文断裂。

## 七、总结

HITL 前端闭环,核心两个东西:

1. **pendingInterrupt** state:看到 need_input 就设上,驱动补充框显示;提交后清空
2. **resume = 重新 streamChat 带补充值**:和发新查询同路径,只是多带 marketplace/category

不引入 /resume 接口、不学 interrupt 协议。前端代码复杂度和"发新查询"持平,只是多一个"补充模式"的 UI 状态。

> HITL 的前端不一定要懂 interrupt/resume。当后端用"状态字段 + 重发"实现 HITL 时,前端就是"看到一个标记 → 弹个框 → 重新发请求带参数"。简单、和正常流程统一、好维护。
