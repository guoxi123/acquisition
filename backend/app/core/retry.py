"""统一的瞬时/永久错误分类 + 重试，覆盖 LLM 调用与外部搜索 provider。

这两类都是无状态请求-响应，重试安全。DB 不在此列：写操作非幂等（commit 超时后
无法判断是否已落库，盲目重试会重复写），且 session 出错后需 rollback + 重开，
通用重试装饰器覆盖不了。

分类约定：
- TransientError：可恢复瞬时错误（429 限流、超时、连接中断、5xx），重试有望成功。
- PermanentError：不可恢复永久错误（4xx 鉴权/参数/余额、schema 错误），重试无意义。

退避策略：
- 429 限流 → 指数退避（base_delay * 2^(n-1)），给对端缓冲恢复窗口。
- 其他瞬时 → 线性退避（base_delay * n），快速试探。
- 永久 → 不重试，原样上抛。
每次退避叠加 ±10% 抖动，避免限流下重试请求同步扎堆。
"""

import asyncio
import logging
import random
from typing import Awaitable, Callable, TypeVar

import httpx

logger = logging.getLogger(__name__)
T = TypeVar("T")


class TransientError(Exception):
    """可恢复瞬时错误。

    显式抛出时可用 rate_limited=True 标记走指数退避（默认线性）。
    """

    def __init__(self, *args, rate_limited: bool = False) -> None:
        super().__init__(*args)
        self.rate_limited = rate_limited


class PermanentError(Exception):
    """不可恢复永久错误。"""


def _status_code(exc: BaseException) -> int | None:
    """从异常对象 duck-type 取 HTTP status_code（openai.APIStatusError /
    httpx.HTTPStatusError / apify 等都把状态码挂在 .status_code 或 .response.status_code）。"""
    code = getattr(exc, "status_code", None)
    if code is None:
        code = getattr(getattr(exc, "response", None), "status_code", None)
    return code if isinstance(code, int) else None


def classify(exc: BaseException) -> tuple[bool, bool]:
    """分类异常 → (is_transient, is_rate_limited)。

    - (False, _)  永久错误，快速失败
    - (True, True) 429 限流，指数退避
    - (True, False) 其他瞬时，线性退避
    """
    if isinstance(exc, PermanentError):
        return False, False
    if isinstance(exc, TransientError):
        return True, exc.rate_limited
    # httpx 超时 / 传输层（DNS、连接被拒、TLS…）
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True, False
    # 兜底标准库超时 / 连接错误（openai.APITimeoutError 也继承 asyncio.TimeoutError）
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return True, False
    # 带 HTTP status_code：429 限流、5xx 服务端、其余 4xx 客户端
    code = _status_code(exc)
    if code is not None:
        if code == 429:
            return True, True
        if 500 <= code < 600:
            return True, False
        if 400 <= code < 500:
            return False, False
    # 未知异常：LLM/搜索都是幂等调用，重试安全，按瞬时线性退避有限兜底
    return True, False


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    label: str = "",
) -> T:
    """执行异步 fn：瞬时错误重试，永久错误快速失败。

    429 → 指数退避；其他瞬时 → 线性退避；永久 → 立即原样上抛。
    重试耗尽后把最后一次异常原样抛出（不包装，保留原始 traceback / status_code）。
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except Exception as exc:
            is_transient, rate_limited = classify(exc)
            if not is_transient or attempt >= max_attempts:
                raise
            if rate_limited:
                delay = base_delay * (2 ** (attempt - 1))
            else:
                delay = base_delay * attempt
            delay = min(delay, max_delay)
            jitter = random.uniform(0, delay * 0.1)
            logger.warning(
                "%s瞬时错误（第 %d/%d 次），%.1fs 后重试：%s",
                f"[{label}] " if label else "",
                attempt,
                max_attempts,
                delay + jitter,
                exc,
            )
            await asyncio.sleep(delay + jitter)
    # 理论不可达：循环内要么 return 要么 raise
    raise RuntimeError("with_retry 不可达出口")
