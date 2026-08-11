# LLM 调用的错误分类与重试:429 指数退避、其他线性、永久快速失败

> 做 agent 不可避免要调一堆外部依赖:LLM(DeepSeek)、搜索/补联系方式 provider(天眼查、企查查、DuckDuckGo)。这些调用经常抽风——限流、超时、连接断、5xx。本项目原来满地裸 `except Exception`,要么静默吞掉、要么干脆不重试,一次网络抖动就让一整轮采集白费。本文讲怎么把异常分成「瞬时/永久」两类,再据此决定重试策略,以及一个最重要的边界:**为什么这套模式不能套到 DB 上**。

## 一、问题:不是所有异常都该重试

外部调用失败时,本能反应是「重试」。但盲目重试是错的:

- **429 限流**:对端在喘息,重试有希望成功——但得等一会儿(退避),立刻重试只会火上浇油。
- **超时 / 连接断 / 5xx**:瞬时基础设施抖动,重试通常能恢复。
- **401 鉴权失败 / 404 / 参数错误**:这些是永久错误,重试 100 次结果也一样。重试它们等于白白烧三次退避时间。

所以重试要想「有的放矢」,前提是**先分类**:这个错误是瞬时的(重试有意义),还是永久的(快速失败别浪费)?

项目里原来的处理是反面教材:

```python
# orchestrator._structured_invoke:except 一把抓,瞬时错误也直接 return None
except Exception as e:
    logger.warning("%s error: %s", label, e)
    return None

# tianyancha / qichacha:except 吞掉返回空,没有任何重试
except Exception as e:
    return {"phones": [], "emails": []}
```

一次 429 或几秒的网络抖动,就让本可恢复的调用直接失败。需要一套统一的「分类 + 重试」工具。

## 二、把异常分成两类

定义两个异常类,作为整套机制的词汇表:

```python
class TransientError(Exception):
    """可恢复瞬时错误:429 限流、超时、连接中断、5xx——重试有望成功。
    显式抛出时可用 rate_limited=True 走指数退避。"""
    def __init__(self, *args, rate_limited: bool = False) -> None:
        super().__init__(*args)
        self.rate_limited = rate_limited


class PermanentError(Exception):
    """不可恢复永久错误:4xx 鉴权/参数/余额、schema 错误——重试无意义。"""
```

`TransientError` 上挂了个 `rate_limited` 标记,用来区分「429 这种限流」和「普通瞬时错误」——因为两者退避策略不一样(下一节解释)。

这两个类既用于**在调用点显式标记**(比如某个 provider 检测到 `error_code != 0` 明确是永久错误时 `raise PermanentError`),也是分类器内部的统一结论。

## 三、怎么分类:duck-type,不耦合具体库

难点:异常来自好几个不同的库——`openai`(LLM)、`httpx`(provider)、未来的 `apify_client`……。分类器不能 `import` 所有这些库(耦合、还可能没装)。

解法是 **duck-type**:看异常对象上有没有 `status_code`、是不是已知的超时/连接类型,不依赖具体类型:

```python
def _status_code(exc: BaseException) -> int | None:
    """openai.APIStatusError / httpx.HTTPStatusError / apify 都把状态码挂在
    .status_code 或 .response.status_code,duck-type 取出来。"""
    code = getattr(exc, "status_code", None)
    if code is None:
        code = getattr(getattr(exc, "response", None), "status_code", None)
    return code if isinstance(code, int) else None


def classify(exc: BaseException) -> tuple[bool, bool]:
    """→ (is_transient, is_rate_limited)。
    (False, _)    永久,快速失败
    (True, True)  429 限流,指数退避
    (True, False) 其他瞬时,线性退避"""
    if isinstance(exc, PermanentError):
        return False, False
    if isinstance(exc, TransientError):
        return True, exc.rate_limited
    # httpx 超时 / 传输层(DNS、连接被拒、TLS…)
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True, False
    # 标准库超时 / 连接错误(openai.APITimeoutError 也继承 asyncio.TimeoutError)
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return True, False
    # 带 HTTP status_code:429 限流、5xx 服务端、其余 4xx 客户端
    code = _status_code(exc)
    if code is not None:
        if code == 429:
            return True, True
        if 500 <= code < 600:
            return True, False
        if 400 <= code < 500:
            return False, False
    # 未知异常:LLM/搜索都是幂等调用,重试安全,按瞬时线性退避有限兜底
    return True, False
```

分类矩阵:

| 异常 | is_transient | is_rate_limited | 结论 |
|---|---|---|---|
| `PermanentError` | False | - | 快速失败 |
| `TransientError()` | True | False | 线性退避 |
| `TransientError(rate_limited=True)` | True | True | 指数退避 |
| `httpx.ReadTimeout` / `httpx.ConnectError` | True | False | 线性退避 |
| `asyncio.TimeoutError` / `ConnectionError` | True | False | 线性退避 |
| status 429 | True | True | 指数退避 |
| status 500/503 | True | False | 线性退避 |
| status 401/404 | False | - | 快速失败 |
| 未知 `ValueError` 等 | True | False | 线性兜底 |

最后一行「未知默认瞬时」是个有意取舍:LLM/搜索调用都是幂等的(同输入重发等价),把未知异常当瞬时、有限次重试,比直接放弃更韧;真碰上永久错误,3 次重试也就浪费几秒,不会造成数据问题。这个默认**只在幂等调用上成立**——这是下一节 DB 边界的伏笔。

## 四、重试函数:三种策略

```python
async def with_retry(fn, *, max_attempts=3, base_delay=1.0, max_delay=30.0, label=""):
    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except Exception as exc:
            is_transient, rate_limited = classify(exc)
            if not is_transient or attempt >= max_attempts:
                raise                              # 永久错误,或重试耗尽 → 原样上抛
            if rate_limited:
                delay = base_delay * (2 ** (attempt - 1))   # 429 → 指数:1, 2, 4…
            else:
                delay = base_delay * attempt               # 其他瞬时 → 线性:1, 2, 3…
            delay = min(delay, max_delay)
            jitter = random.uniform(0, delay * 0.1)        # ±10% 抖动
            logger.warning("…瞬时错误(第 %d/%d 次),%.1fs 后重试…", attempt, max_attempts, delay + jitter, exc)
            await asyncio.sleep(delay + jitter)
    raise RuntimeError("with_retry 不可达出口")
```

三种策略,对应三种场景:

1. **429 → 指数退避** `base·2ⁿ⁻¹`(1s, 2s, 4s…)。限流意味着对端在过载,需要越来越长的恢复窗口,指数增长给足缓冲。
2. **其他瞬时 → 线性退避** `base·n`(1s, 2s, 3s…)。超时、连接抖动通常很快恢复,线性快速试探,不浪费时间。
3. **永久 → 不重试**,立即 `raise`。401 鉴权失败重试三次也是 401,快速失败把上层 fallback(返回空、降级)立刻触发。

两个细节:

- **抖动(jitter)**:每次退避叠加 0~10% 随机量。限流场景下,如果多个请求同时被 429、又同时按固定的指数退避重试,会再次同步打满对端;抖动把重试时间错开,避免「惊群」。
- **原样上抛,不包装**:重试耗尽或永久错误时,直接 `raise` 原始异常(不包成 `TransientError`)。这样上层的 `except` 还能拿到原始的 `status_code`、`response`,日志和错误处理不受影响。

## 五、接入:LLM 调用点 + 搜索 provider

接入是外科手术式的:只把「真正发请求的那一行」包进 `with_retry`,**各自原有的 fallback 一律保留**。

LLM 的三个 `ainvoke` 点:

```python
# orchestrator._structured_invoke(意图解析等复用)
resp = await with_retry(lambda: llm.ainvoke(full), label=label)
# 上面仍套着原 except → return None,瞬时重试耗尽才落到这里

# query_agent.agent(ReAct 推理)
resp = await with_retry(lambda: llm.ainvoke(messages), label="query_agent")

# nodes.llm_analysis(卖家画像)
try:
    resp = await with_retry(lambda: llm.ainvoke(prompt), label="llm_analysis")
    s["analysis"] = str(resp.content)[:200]
except Exception:
    s["analysis"] = ""    # 保留:画像生成失败不影响主流程
```

httpx 搜索 provider 的四个请求点(天眼查、企查查、DuckDuckGo 搜索、官网抓取):

```python
# tianyancha / qichacha:GET 包重试,耗尽仍走 except → 空结果
resp = await with_retry(lambda: client.get(...), label="tianyancha")

# website_provider.find_website(DuckDuckGo 搜官网)
try:
    r = await with_retry(
        lambda: client.get("https://html.duckduckgo.com/html/", params={"q": q}),
        label="ddg_search",
    )
except Exception:
    return None
```

注意 provider 这层刻意保留了外层 `except → 返回空`:联系方式补全是 best-effort 的增强,即便重试耗尽,也该优雅降级(返回空联系方式)而不是让整轮采集挂掉。「快速失败」在这里的意思是「别浪费重试次数」,最终的优雅降级仍是调用点的既有行为。

## 六、踩坑:DB 为什么不能套这个模式

这是整套设计**最重要的边界**。乍看 DB 操作也有瞬时错误(连接断、deadlock),似乎也该重试,但仔细想有三个根本不同:

**1. 写操作不幂等——这是致命的。** `await db.commit()` 超时后,你**不知道数据到底落库没有**(网络可能在 TCP ACK 之后、commit 落盘之前断开)。这时候盲目重试 `INSERT` 一个卖家,就会插重复行;重试「采集入库」就会重复计数。LLM/搜索调用是幂等的(同输入重发等价),重试天然安全;DB 写不是。

**2. 事务 / session 语义。** SQLAlchemy 的 session 一旦抛错就进入 broken 态,**同一个 session 直接 retry 会再失败一次**——必须先 `rollback()` 再开新 session。一个通用的 `with_retry` 装饰器套在原 session 上根本做不到这件事。要重试 DB 写,得把「开 session → 执行 → commit」整个流程重构进每次 attempt,这就不是一个通用函数能覆盖的了。

**3. 连接池已经兜底了瞬时重连。** SQLAlchemy 的 `pool_pre_ping` + 自动重连覆盖了大部分「连接被断」的瞬时错误。DB 层真正剩下的瞬时错误(deadlock、临时不可用)很少;剩下的永久错误(唯一约束、非空、外键)本来就该 fail-fast。

所以结论:**第一版只覆盖 LLM + 搜索 provider,DB 完全不进重试机制。** 真哪天观察到 DB 瞬时错误再说,而且那时也只该给**读操作**加(读是幂等的),写操作要加必须先保证幂等(比如按自然键 UPSERT)。

## 七、刻意排除:流式 astream 和 Apify

除了 DB,还有两类调用本轮也没接,**是刻意排除而非遗漏**:

**流式 `astream`(output_result / direct_llm)。** 这两个节点用 `llm.astream()` 流式生成回复,边收边把累积内容 flush 进 DB。重试流式调用语义不明:错误可能发生在第一个 chunk 之前(可重试),也可能发生在流到一半(已经 yield 了内容、已经 flush 了 DB)。后者重试会从头再来,导致 DB 里出现断裂的重复内容。区分这两种情况很麻烦,而这两个节点本来就有 fallback(output_result 失败返回空回复)。流式重试留给后续单独设计。

**Apify adapters。** `google_search` / `junglee_crawler` / `amazon_seller` / `amazon_products` 这几个 adapter 用的是 `apify_client`(同步库,靠 `asyncio.to_thread` 包进线程池),异常类型在单独的 `errors.py` 里,错误模型跟 httpx / openai 都不一样。在没摸清它的异常分类前贸然接进 `classify`,等于瞎猜——可能把永久错误当瞬时反复重试,也可能反过来。正确做法是先搞清楚 `apify_client` 的异常类型(哪些带 status_code、哪些是 actor 运行失败),再给 `classify` 加专门分支,然后接入。

## 八、验证:单测

`tests/core/test_retry.py` 18 条用例,纯逻辑、不依赖网络和 LLM:

- **13 条 `classify` 矩阵**:覆盖上表每一行(429/5xx/4xx、httpx 超时/连接、标准库超时、显式两类、未知兜底)。
- **5 条 `with_retry` 行为**:永久错误零重试零 sleep、429 走指数、其他瞬时走线性、重试后成功、耗尽后原样上抛原始异常。

退避时长做**精确断言**:用 monkeypatch 把 `asyncio.sleep` 换成记录器、把 `random.uniform` 抖动置零,直接断言 sleep 时长序列:

```python
async def test_rate_limit_uses_exponential(sleeps):
    fn = Flaky([_exc_with_status(429), _exc_with_status(429)])  # 两次 429 后成功
    assert await with_retry(fn, base_delay=1.0, max_attempts=5) == "ok"
    assert fn.calls == 3
    assert sleeps == [1.0, 2.0]   # 指数:2^0, 2^1

async def test_other_transient_uses_linear(sleeps):
    fn = Flaky([httpx.ConnectError("c"), httpx.ConnectError("c")])
    assert await with_retry(fn, base_delay=1.0, max_attempts=5) == "ok"
    assert sleeps == [1.0, 2.0]   # 线性:1*1, 1*2
```

`Flaky` 是个测试替身:按给定序列抛异常,耗尽后返回 `"ok"`,顺便计数实际调了几次。

## 九、扩展指南

以后要把重试扩到别的调用面,按这个清单走:

1. **先确认幂等**。调用重发等价吗?读操作天然幂等;写操作必须先保证幂等(UPSERT、去重键、幂等 token),否则别加。
2. **摸清异常类型**。新库的异常挂在哪个属性?是不是 `status_code`?有没有超时/连接的基类?给 `classify` 加对应的 duck-type 分支,而不是在调用点写一堆 `except`。
3. **决定退避策略**。限流类的走指数(给对端缓冲),一般瞬时的走线性(快速试探)。
4. **保留调用点 fallback**。`with_retry` 只管「重试到成功或确定不可恢复」,成功后的业务降级(返回空、记日志、走 HITL)仍是调用点的事。

## 总结

外部调用的错误处理,核心不是「要不要重试」,而是「**这个错误值不值得重试**」。把异常分成瞬时 / 永久两类,重试才有的放矢:

- **分类**靠 duck-type(`status_code` + 已知超时/连接类型),不耦合具体库;未知异常在幂等调用上默认瞬时兜底。
- **重试**三种策略:429 指数退避、其他瞬时线性退避、永久快速失败,加抖动防惊群。
- **接入**外科手术式:只包发请求那一行,调用点原有 fallback 全保留。
- **边界**:DB 写不入这套机制(不幂等 + 事务语义),流式和 Apify 待错误模型厘清再接。

韧性设计的成败,往往不在「加了多重试」,而在「分清了哪些该重试、哪些不该」。
