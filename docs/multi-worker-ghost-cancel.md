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

不过回头看,这只是**止血**:它修的是"SSE 误判孤儿"这个症状,取消机制本身还是老一套——`task.cancel()` 打进程内 task。后来我把取消整个重做了,见第七节。

## 六、后记:真正的根治——协作式取消

90s 时间阈值上线后又想了一层:那个 bug 的本质不是"孤儿判定写错了",而是**取消机制本身就是进程内的**。

原来的取消链路:取消接口 → 查 `_tasks[thread_id]` → `task.cancel()` → CancelledError → 写 done。这条链有三个进程内依赖:

1. `_tasks` 注册表——多 worker 下打不到正确进程,取消接口大概率查不到 task,直接 no-op
2. `task.cancel()` 只对**本进程**的 task 有效
3. 即便取消了,LLM 调用、Apify 采集这些 await 点之间的代码还是会跑完当前节点才停

也就是说:用户在 worker B 的页面上点取消,图在 worker A 上跑——**根本取消不掉**。幽灵取消 bug 只是这个设计缺陷暴露出的第一个症状。

### 重做:取消信号放 DB,图自己检查

新方案三行就能说清:

1. **取消接口只写 DB**:`meta.cancelled = True`,不再碰任何 task 对象
2. **图节点开头自查**:每个耗时节点(query_db / call_actors / acquire_if_needed)入口调 `check_cancel(msg_id)`,读 DB 发现 `cancelled` 就 `raise CancelledByUser`,图立即停止
3. **顶层统一收尾**:chat_stream 的 `_run_stream_graph` 捕获 `CancelledByUser`,写 `done + cancelled`,保留已产出的 sellers

```python
class CancelledByUser(Exception):
    """协作式取消:DB 是共享取消信号,任意 worker 都能取消任意 worker 的图。"""

async def check_cancel(msg_id: str) -> None:
    if not msg_id:
        return
    async with async_session() as db:
        msg = await db.get(MemoryMessage, uuid.UUID(msg_id))
        if msg and (msg.meta or {}).get("cancelled"):
            raise CancelledByUser()
```

取消接口简化成:查 DB 最新 assistant 消息 → 没跑完就写 `cancelled=True` → 返回。哪个 worker 接到这个请求都无所谓,因为它只是写一行数据库。

`_tasks` / `_cancelled` 注册表整个删掉。SSE 那边也一样:done 由图(或取消接口)写进 DB,SSE 只是轮询转发,天然跨 worker。

### 为什么这叫"协作式"

`task.cancel()` 是**抢占式**——外部强杀,task 没有发言权,随时可能死在任意 await 点,资源清理靠运气。协作式(cooperative)取消是图**主动配合**:取消方只立标志,执行方在安全的检查点自己决定停下——此时 DB 连接、半成品数据都处于一致状态,想保留已产出的 sellers 就保留。

代价是"检查点之间的代码不会立刻停"。所以检查点要放在耗时节点入口:query_db / call_actors(每轮采集)/ acquire_if_needed——这些是真正花时间的环节,LLM 生成、外部采集都会在下一个节点边界被拦下。对秒级的 Agent 查询,这个延迟完全可接受。

### 演进复盘

| 阶段 | 做法 | 问题 |
|------|------|------|
| v1 | `_tasks` 注册表 + `task.cancel()` | 多 worker 下查不到 task;孤儿收尾误判盖掉 need_input |
| v2(止血) | 孤儿判定改 created_at>90s,收尾不写 cancelled | 症状消失,但取消本身仍可能 no-op |
| v3(根治) | 取消信号进 DB,节点入口 check_cancel 协作式退出 | 无进程内状态,任意 worker 可取消任意 worker 的图 |

一个通用模式浮现出来:**多 worker 架构里,"控制指令"和"执行状态"要么都进程内,要么都共享存储,不能混搭**。v1 的 bug 就是混搭——状态(task)在进程内,信号(取消请求)却可能从任何进程来。SSE/DB 写状态已经是共享的,取消信号跟进 DB 之后,整个链路才真正自洽。

## 七、教训

1. **凡是 `dict`/`set`/全局变量当"注册表"用的,多 worker 一定会出问题。** 这类 bug 在本地(单进程)永远复现不了,等上了生产才暴露——而且现象常常很诡异(像这次的"幽灵取消")。
2. **跨 worker 的状态必须放共享存储**:DB、Redis,或者干脆像这次一样改成"无状态判定"(用消息自己的 created_at)。
3. **可观测是前提。** 这次能快速定位,是因为有 trace + 能直接查 DB 的 meta。没有这些,线上 agent 是黑盒,只能瞎猜。
4. **写"兜底逻辑"要小心它的副作用。** 孤儿收尾本意是好的(防卡死),但它写的 `cancelled` 把正常的 HITL 给盖了——兜底逻辑反而成了 bug 源。兜底动作要尽量"幂等、不覆盖业务字段"。
5. **止血和根治要分清。** 90s 阈值修的是 SSE 误判,取消机制本身的进程内依赖还在——同类问题会在别的地方再冒出来。修完症状要追问一句:根因设计是不是也要改?

## 八、一句话总结

> 本地单进程跑通 ≠ 生产跑通。任何依赖进程内状态的 agent 设计(注册表、内存锁、计数器),上线多 worker 前都要换成共享存储或无状态方案——否则你只是在等一个"线上才出现"的灵异 bug。取消这种控制流也一样:与其抢进程内的 task 句柄,不如把取消信号放进所有 worker 都看得见的地方,让执行方自己体面地退出。
