# 生产才暴露的 Agent Bug:多 Worker 下的"幽灵取消"

> 本地单进程跑得好好的 Agent,一上生产(4 workers)就出怪事:用户暂停查询后刷新页面,一直"断线重连"转圈。排查了一圈,根因是一个几乎所有 LangGraph 项目都会踩的坑——**进程内状态在多 worker 下不共享**。

## 一、现象:本地没问题,线上"卡死"

线上反馈:用户发起一次查询,点"暂停",然后刷新页面——页面一直 loading,像是永远在等一个不会来的响应。

诡异的是:**本地开发环境完全复现不了**。同样的代码,本地暂停→刷新一切正常。

这种"只在线上出现"的 bug 最磨人。

## 二、架构速览:单线流式图 + SSE

先交代下背景。这是一个获客 Agent,用 LangGraph 画了一张单线流式图:

```
classify_intent → check_quota → parse_intent → query_db → ... → output_result
```

后端把图扔到后台 task 跑,前端通过 SSE 轮询 DB 拿增量:

- 图节点边跑边往 DB 写 `content`(流式回复)、`progress`(思考过程)、`sellers`(结果)、`done`(完成标记)
- SSE endpoint 每 200ms 轮询一次,把增量推给前端

为了处理"刷新时图还在跑 / 历史残留消息没 done 标记"的情况,我之前加了一段**孤儿收尾**逻辑(就这段是 bug 源头):

```python
# stream_events 轮询循环里
if not meta.get("done") and thread_id not in _tasks:
    # 没人在这条消息上干活了 → 主动写 done,免得前端永远等
    await _write_msg(msg.message_id, meta_patch={"done": True, "cancelled": True})
    continue
```

意图很单纯:`_tasks` 是个 `{thread_id: task}` 的注册表,POST /stream 起 task 时往里塞,task 结束在 finally 里 pop。如果一条消息 `done` 还没写、`_tasks` 里又没对应 task——说明这消息"孤儿"了(旧残留/异常/reload),给它补个 done。

本地单进程,这段逻辑完美工作。

## 三、排查:cancelled 盖掉了 need_input

线上复现:用"卖杯子"(故意缺目标市场,触发 HITL 补充)测。期望前端收到 `need_input`,弹补充框。实际前端收到的是:

```json
{"done": true, "sellers": [], "cancelled": true}
```

——`cancelled: true`,**没有 need_input**。前端按 cancelled 处理,不弹补充框,看上去就是"转圈没反应"。

但后端日志里,output_result 的 HITL 分支明明跑了("信息不全,等待补充:目标市场"),而且 `[stream-graph] 完成`——task 正常完成,不是取消。

查 DB 这条消息的 meta:

```json
{"done": true, "need_input": true, "missing": ["目标市场"], "cancelled": true, ...}
```

`need_input` 和 `cancelled` **同时存在**。output_result 写了 `need_input`(对),但又被写了个 `cancelled`(错)。

谁写的 cancelled?代码里只有三处写 cancelled:取消接口、CancelledError 异常、孤儿收尾。前两个日志里没触发,那就只剩**孤儿收尾**。

可 task 明明正常跑完了,孤儿收尾为什么会触发?

## 四、根因:`_tasks` 是进程内 dict,多 worker 不共享

盯着孤儿收尾那个条件 `thread_id not in _tasks` 看了半天,突然反应过来——

**生产是 `uvicorn --workers 4`,4 个独立进程**。`_tasks` 是模块级 Python dict,**每个 worker 进程各有一份,互不相通**。

时序是这样的:

1. 用户 POST /stream,请求被负载均衡打到 **worker A**。A 起 task、`_tasks["thread-x"] = task`、返回 streaming
2. 前端 GET /events(SSE),请求打到 **worker B**。B 的 `_tasks` 是空的,根本没有 `"thread-x"`
3. worker B 的孤儿收尾一看:`done` 没写(图还在 worker A 跑)、`thread-x not in _tasks`(B 自己的字典)→ **判定为孤儿**
4. B 抢在 worker A 的 output_result 写 `need_input` **之前**,给这条消息写了 `done + cancelled`
5. worker B 的 SSE 立刻把"done + cancelled(无 need_input)"推给前端,然后 return 关闭连接
6. 稍后 worker A 的 output_result 才慢悠悠写上 `need_input`——但前端早就收完了

本地单 worker,A 和 B 是同一个进程、同一份 `_tasks`,永远不会误判。所以本地复现不了。

**进程内状态 + 多 worker = 定时炸弹。**

## 五、修复:孤儿判定不依赖共享状态

两处改:

**1. 孤儿判定改成按时间,不查 `_tasks`:**

```python
# done 没写 且 消息创建超过 90s → 多半真死了(reload/异常),才收尾
if not meta.get("done") and msg.created_at:
    if (now - msg.created_at).total_seconds() > 90:
        await _write_msg(msg.message_id, meta_patch={"done": True})
        continue
```

正常查询几秒就写 done,根本碰不到 90s。真孤儿(task 崩了/服务器 reload)才收尾。跨 worker 也能用——因为 `created_at` 在 DB 里,不在进程内存。

**2. 收尾不写 `cancelled`:**

孤儿收尾只补 `done`,不写 `cancelled`。这样即便误触发,也不会盖掉 HITL 的 `need_input`——前端照常弹补充框。取消标记只由"用户主动取消"那个接口写。

改完上线,SSE done payload 变成 `{"done":true, "cancelled":false, "need_input":true}`,前端正确弹补充框。bug 根除。

## 六、教训

1. **凡是 `dict`/`set`/全局变量当"注册表"用的,多 worker 一定会出问题。** 这类 bug 在本地(单进程)永远复现不了,等上了生产才暴露——而且现象常常很诡异(像这次的"幽灵取消")。
2. **跨 worker 的状态必须放共享存储**:DB、Redis,或者干脆像这次一样改成"无状态判定"(用消息自己的 created_at)。
3. **可观测是前提。** 这次能快速定位,是因为有 trace + 能直接查 DB 的 meta。没有这些,线上 agent 是黑盒,只能瞎猜。
4. **写"兜底逻辑"要小心它的副作用。** 孤儿收尾本意是好的(防卡死),但它写的 `cancelled` 把正常的 HITL 给盖了——兜底逻辑反而成了 bug 源。兜底动作要尽量"幂等、不覆盖业务字段"。

## 七、一句话总结

> 本地单进程跑通 ≠ 生产跑通。任何依赖进程内状态的 agent 设计(注册表、内存锁、计数器),上线多 worker 前都要换成共享存储或无状态方案——否则你只是在等一个"线上才出现"的灵异 bug。
