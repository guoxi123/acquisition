# 轻量 HITL:不用 interrupt/checkpointer,让 LangGraph Agent 学会"问下去"

> HITL(Human-in-the-Loop)是 Agent 的标配能力。LangGraph 官方给了正解:`interrupt()` + `Command(resume=...)` + checkpointer。但它有点重——本文讲一种"穷人版"做法:不暂停图、不存 checkpoint,靠状态字段 + 条件边 + 前端重发,实现"缺信息就问、补完重跑"。简单、可观测、跨 worker 安全。

## 一、HITL 是什么,为什么 Agent 离不开它

HITL = Human-in-the-Loop,人在回路。Agent 跑到一半,发现信息不够/要确认/要审批,暂停下来问人,人补完再继续。

对 LLM Agent 尤其重要:LLM 会猜、会幻觉,关键决策(比如"这个查询条件对不对""要不要执行这个花钱的操作")让它先问一句人,比让它自己拍板靠谱得多。

LangGraph 官方给了 HITL 的标准答案:`interrupt()` + `Command(resume=...)` + checkpointer。

## 二、我的场景:意图识别缺字段

获客 Agent,用户输入自然语言找亚马逊卖家。意图解析(prompt + LLM)提取 marketplace/category 等。但用户可能只说"卖杯子"——缺目标市场,没法查。

这时候该停下来问:"你要哪个站的?美国站/英国站/..."

标准 interrupt 流程大概是这样:

```python
async def human_confirm(state):
    if not state["need_human_confirm"]:
        return {"human_approved": True}
    response = interrupt({"question": "请补充:目标市场", ...})  # 暂停,等 resume
    return {"marketplace": response["marketplace"], ...}
```

配合 checkpointer(AsyncPostgresSaver)+ 前端用 `Command(resume=...)` 喂回答案,图从断点继续。

## 三、interrupt 的代价

标准做法没问题,但落地有几处"重":

1. **要引入 checkpointer**(AsyncPostgresSaver),多一套表/连接管理,图的每次执行都要存/取 checkpoint。
2. **API 形态变了**:不能一个 POST 搞定,要 POST 触发 → 检测到 interrupt 返回 → 前端 POST /resume 带答案 → 图继续。endpoint 要管 thread_id、interrupt 状态。
3. **前端要懂 interrupt/resume 协议**,而不是简单的"发请求→接流式结果"。
4. **调试心智负担**:图能"停在中途",排查时要理解 checkpoint 里的状态。

对一个"信息不全就问一句、补完重跑"的简单场景,这些有点重。

## 四、轻量方案:状态字段 + 条件边 + 前端重发

核心思路:**HITL 不一定要"暂停图",也可以是"图正常跑完、把'要问什么'作为结果输出,前端补完重新发一次"**。

### 1. 意图解析只负责"判缺",不负责"问"

parse_intent 解析完,把缺什么写进 state:

```python
return {
    "marketplace": parsed.marketplace,            # 可能 None
    "category": parsed.category,
    "need_human_confirm": parsed.need_confirm,    # 缺关键字段就 True
    "missing": parsed.missing,                    # ["目标市场"]
    ...
}
```

### 2. 图用条件边把"缺字段"直接导向收尾节点

```python
# 信息不全 → output_result 写补充提示;否则查库
builder.add_conditional_edges(
    "parse_intent",
    lambda s: "output_result" if s.get("need_human_confirm") else "query_db",
)
```

### 3. 收尾节点写"要问什么",然后正常结束

output_result 检测到 need_human_confirm,写一条"请补充:目标市场"的收尾消息,带 `need_input` 标记:

```python
if state.get("need_human_confirm"):
    missing = state.get("missing") or ["目标市场", "产品品类"]
    await _write_msg(msg_id,
        content=f"请补充:{', '.join(missing)}",
        meta_patch={"sellers": [], "done": True, "need_input": True,
                    "missing": missing, "marketplace": ..., "category": ...})
    return {"final_result": {"need_input": True}}
# ... 正常分支(查库/采集/评分)
```

图到这里**正常 END**,没有暂停、没有 checkpoint。

### 4. SSE 把 need_input 透传给前端

```python
# stream_events 的 done payload
for k in ("need_input", "missing", "marketplace", "category"):
    if k in meta:
        payload[k] = meta[k]
```

### 5. 前端看到 need_input → 弹补充框 → 重新发请求

前端解析 done,带 need_input 就走补充 UI:

```js
if (data.done) {
  if (data.need_input) onNeedInput(data);  // 弹"请补充"输入框
  else onDone(...);                        // 正常收尾
}
```

用户补充后,resume 不调 /resume、不喂 Command,而是**重新 POST /stream,把补充的 marketplace/category 一起带上**:

```js
await streamChat(query, threadId, marketplace, category);
```

### 6. 后端把补充值注入 state,图重跑

endpoint 收到带 marketplace/category 的请求,直接塞进 state:

```python
state = {
    "user_query": req.query,
    "marketplace": req.marketplace,   # 用户补充的
    "category": req.category,
    ...
}
```

parse_intent 里做个 override——用户已经明确给的值优先,LLM 结果作补充:

```python
marketplace = injected_marketplace or parsed.marketplace
category = injected_category or parsed.category
need_confirm = parsed.need_confirm and not (marketplace and category)
```

这样补充重跑时 marketplace/category 齐了,need_confirm=False,正常走 query_db。

**没有 checkpoint、没有 interrupt、没有 /resume,整条链路就是"普通请求 + 流式响应 + 必要时重发一次"。**

## 五、什么时候够用,什么时候该上 interrupt

这套"穷人版 HITL"不是万能的,它有个根本前提:**补充信息后,从头重跑图的代价可以接受**。

- ✅ 适合:信息不全问一句、参数确认、简单二选一。重跑成本 = 再跑一遍意图识别(一次便宜 LLM 调用),可接受。
- ❌ 不适合:图已经跑了很久(比如采集了半小时)、暂停恢复要"接着上次往下"——这时候重跑代价太大,interrupt/resume(从 checkpoint 续)才是对的。
- ❌ 不适合:一次要问多轮、复杂的审批流(人审 → 改 → 再审)——那是状态机,interrupt 更合适。

一句话:**"问完就重跑"用轻量版,"停住等回复再继续"用 interrupt**。我的获客查询属于前者,所以选了轻量。

## 六、顺带的好处:天然不怕多 worker

这套方案还有个隐藏好处——**没有进程内状态**,所以跨 worker 安全。

(踩坑详见同系列另一篇《多 Worker 下的"幽灵取消"》——那里因为用了一个进程内 `_tasks` dict,4 workers 下出 bug;而这套 HITL 全靠 DB 里的 `need_input` 字段和前端的"重发",跨 worker 无害。)

## 七、总结

LangGraph 的 interrupt/checkpointer 是 HITL 的"正解",但不是唯一解。如果你的场景是"缺信息就问、补完重跑",完全可以:

1. 用**状态字段**(need_human_confirm)+ **条件边**把缺字段导向收尾
2. 收尾节点写 **need_input + missing**,图正常 END
3. SSE 透传,前端看到就**弹补充框**
4. 补充后**重新 POST 带上参数**,图从头跑(注入值 override LLM)

没有 checkpointer、没有 interrupt、没有 /resume 协议。简单、可观测、跨 worker 安全。代价是重跑——而多数"问一句"的场景,重跑成本可以接受。

> HITL 的本质是"把控制权交还给人",不是"必须用 interrupt"。能不暂停就不暂停,能用重跑解决就重跑,工程上往往更省。
