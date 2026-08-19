# Agent 可观测的"穷办法":当 LangSmith 和 Langfuse 都用不起

> 可观测是 LLM Agent 上生产的硬门槛。社区主流答案是 LangSmith(云)和 Langfuse(自部署),两家都好——但一个数据出境、一个吃内存。我的线上是 1.8G ECS + 含敏感数据,两个都不合适。本文讲一个零依赖、零额外内存、数据不出域的"穷办法":LangChain callback + 本地 jsonl。几十行代码,拿到排查 agent 最需要的四样东西。

## 一、可观测是 Agent 上生产的门槛

传统服务出问题,看日志、看 metrics、看 trace,基本能定位。LLM Agent 不一样——一次请求里可能有多次 LLM 调用(意图识别、tool 决策、最终回答)、多次工具调用、一堆中间状态。没有专门的"agent 可观测",线上就是个黑盒:用户说"查得不准",你连是哪一步出问题都不知道。

所以可观测是 agent 生产的硬门槛,不是锦上添花。

社区主流答案是两个:LangSmith(LangChain 官方云)和 Langfuse(开源自部署)。两家都做得很好——但"很好"是有代价的。

## 二、我的处境:1.8G 内存 + 数据不能出境

线上是一台阿里云入门 ECS:2 核、1.8G 内存、40G 盘。跑着 pg + redis + backend(uvicorn 4 workers) + frontend + nginx,常年占用 ~500M,剩 ~1.3G 可用。

业务上,流量里有用户手机号、卖家联系方式(花钱采的)这些敏感数据。

## 三、主流方案为什么都不合适

**LangSmith 云端**:接入只要两个环境变量,功能强。但数据要发到美国 `smith.langchain.com`——**数据出境 + 第三方托管**,对含企业联系方式的 To B 业务,隐私和合规风险太大。

**Langfuse 自部署**:开源、数据在自己服务器,功能对标 LangSmith。但它要 `postgres + clickhouse + langfuse` 一套,**光 clickhouse 就吃 500M-1G**,叠到我那 1.3G 可用内存上,**必 OOM**。

升级服务器?给一个还在验证的 side project 加钱不划算。

**两个主流方案,一个隐私不行、一个资源不行。** 那就自己搞个"穷办法"。

## 四、穷办法原理:LangChain callback → 本地 jsonl

agent 用的是 LangChain 的 ChatModel(`langchain_openai.ChatOpenAI`),它有个机制:**callback**。每次 LLM 调用,会触发一堆钩子(`on_llm_start` / `on_llm_end` / ...)。

只要写一个 callback handler,在 `on_llm_start` 记录"调谁、给什么 prompt",在 `on_llm_end` 记录"返回什么、耗时、token",写进一个 jsonl 文件——就有了一个最小的"LLM 调用 trace"。

- 每次请求用 `thread_id` 当 `run_id`,把这次请求的所有 LLM 调用串起来
- 每个事件一行追加到 `logs/trace_YYYY-MM-DD.jsonl`
- 排查:`grep <thread_id> logs/trace_*.jsonl`

零外部依赖(langchain 本来就有)、零额外内存(就是写文件)、数据不出服务器。

## 五、实现:JsonlTracer

```python
import json, time, uuid
from datetime import datetime, timezone
from pathlib import Path
from langchain_core.callbacks import BaseCallbackHandler

LOG_DIR = Path("logs")

class JsonlTracer(BaseCallbackHandler):
    """记录 LLM 调用到 logs/trace_YYYY-MM-DD.jsonl。同一 run_id 串一次请求。"""

    def __init__(self, run_id: str | None = None):
        self.run_id = run_id or uuid.uuid4().hex[:8]
        self._starts: dict[str, float] = {}

    def _log(self, event: dict) -> None:
        event["ts"] = datetime.now(timezone.utc).isoformat()
        event["run_id"] = self.run_id
        LOG_DIR.mkdir(exist_ok=True)
        path = LOG_DIR / f"trace_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
        self._starts[str(run_id)] = time.monotonic()
        self._log({"type": "llm_start", "rid": str(run_id),
                   "model": (serialized or {}).get("name", ""),
                   "prompt": (prompts[0] if prompts else "")[:1000]})

    def on_llm_end(self, response, *, run_id, **kwargs):
        start = self._starts.pop(str(run_id), None)
        dur_ms = int((time.monotonic() - start) * 1000) if start else None
        try:
            out = response.generations[0][0].text if response.generations else ""
        except Exception:
            out = ""
        usage = (response.llm_output or {}).get("token_usage")
        self._log({"type": "llm_end", "rid": str(run_id),
                   "dur_ms": dur_ms, "out": str(out)[:1000], "token": usage})
```

记的东西就四样:**prompt(输入)、output(输出)、耗时、token**——这正是排查 agent 最需要的四样。

## 六、集成:给图的 ainvoke 挂上 tracer

LangGraph 的 `ainvoke` 支持 config 里传 callbacks,会透传给图内的 LLM 调用:

```python
await graph.ainvoke(state, config={
    "configurable": {"thread_id": thread_id},
    "recursion_limit": 50,
    "callbacks": [JsonlTracer(thread_id)],   # 用 thread_id 当 run_id
})
```

一次请求的所有 LLM 调用(classify / parse_intent / direct_llm / query_agent 的 tool 决策...),都被同一个 tracer 记下,带同一个 run_id。

排查(线上直接 grep,不用进容器——前提是给 backend 挂了 `./logs:/app/logs` volume,让容器内的日志落到宿主机):

```sh
ssh root@server
cd /opt/acquisition/logs
ls                                # 看有哪些 trace
grep <thread_id> trace_*.jsonl    # 排查某个请求
tail -5 trace_$(date +%F).jsonl   # 最近的调用
```

每行长这样:

```json
{"type":"llm_start","rid":"019fdac8-...","model":"ChatOpenAI",
 "prompt":"System: 判断用户查询的意图类别...","run_id":"72196567-..."}
{"type":"llm_end","rid":"019fdac8-...","dur_ms":681,
 "out":"{\"intent\":\"query\"}","token":{"prompt_tokens":185,"completion_tokens":7,"total_tokens":192},
 "run_id":"72196567-..."}
```

一眼看到:这个请求判成了 query、用了 681ms、192 token。哪个 LLM 调用慢、哪个返回错了、哪个 prompt 把用户带歪了——grep 一把全出来。

## 七、取舍:它不能做什么

诚实说,这个穷办法不是 LangSmith/Langfuse 的替代品,它有明确边界:

**能做**:记录每次 LLM 调用的输入/输出/耗时/token,按请求串起来,grep 排查。这对定位"哪一步出了问题""prompt 是不是该改""是不是某次调用超时"已经够用。

**不能做**:
- **没有 web 面板**,排查靠命令行 grep(团队大了不友好)。
- **不记 graph 节点流转**(只记 LLM 调用;节点的 state 变化得靠业务日志/progress)。
- **没有聚合统计**(日调用量、p99 延迟、错误率,得自己写脚本扫 jsonl)。
- **没有 eval / prompt 版本管理**(那是另一摊)。

## 八、什么时候该升级

这套方案是为"资源紧 + 数据敏感 + 团队小"量身的最小可行。当你:
- 有了一台内存充裕的服务器(≥4G)→ 上 **Langfuse 自部署**,拿面板和聚合。
- 数据出境合规 OK + 想省运维 → 上 **LangSmith 云端**,功能最全。
- 团队多人、要协同排查 → 任何有 web 面板的方案都行,命令行 grep 撑不住协作。

好消息是:**升级时不用改 agent 代码**。LangChain 的 callback 机制和 LangSmith/Langfuse 都兼容——tracer 留着(本地兜底),同时设 LangSmith/Langfuse 的环境变量,两路 trace 并行,平滑切换。

## 九、总结

"可观测"不等于"上 LangSmith/Langfuse"。它的核心是:**每次 LLM 调用,你都能事后回看输入、输出、耗时、token**。

资源紧、数据敏感、团队小的时候,一个 `BaseCallbackHandler` + 一个 jsonl 文件,几十行代码,就能拿到这四样——零依赖、零额外内存、数据不出域。排查靠 `grep thread_id`,够个人/小项目用很久。

> 可观测是门槛,但门槛不一定要花大钱迈。先用最便宜的方式保证"线上不是黑盒",等业务起来了再升级到专业方案——而且升级路径是平滑的,前期投入不浪费。
