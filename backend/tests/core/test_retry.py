"""retry 工具单测：异常分类矩阵 + 退避策略（429 指数 / 其他瞬时线性 / 永久快速失败）。

跑：cd backend && uv run pytest tests/core/test_retry.py -v
纯逻辑验证，不依赖网络 / LLM。退避时长通过 monkeypatch sleep + 抖动置零做精确断言。
"""

import asyncio

import httpx
import pytest

from app.core import retry
from app.core.retry import PermanentError, TransientError, classify, with_retry


def _exc_with_status(code: int) -> Exception:
    """造一个带 status_code 属性的异常（模拟 openai.APIStatusError / httpx.HTTPStatusError）。"""
    e = Exception(f"status {code}")
    e.status_code = code
    return e


# ---------------- classify 矩阵 ----------------

@pytest.mark.parametrize(
    "exc, expected",
    [
        (PermanentError("x"), (False, False)),
        (TransientError("x"), (True, False)),                       # 默认线性
        (TransientError("x", rate_limited=True), (True, True)),     # 显式限流 → 指数
        (httpx.ReadTimeout("t"), (True, False)),                    # TimeoutException
        (httpx.ConnectError("refused"), (True, False)),             # TransportError
        (asyncio.TimeoutError(), (True, False)),
        (ConnectionError(), (True, False)),
        (_exc_with_status(429), (True, True)),                      # 限流 → 指数
        (_exc_with_status(500), (True, False)),                     # 5xx → 线性
        (_exc_with_status(503), (True, False)),
        (_exc_with_status(401), (False, False)),                    # 4xx → 永久
        (_exc_with_status(404), (False, False)),
        (ValueError("未知"), (True, False)),                        # 未知 → 瞬时兜底
    ],
)
def test_classify(exc, expected):
    assert classify(exc) == expected


# ---------------- with_retry 行为 ----------------

class Flaky:
    """按 errors 顺序抛异常，耗尽后返回 "ok"。"""

    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "ok"


@pytest.fixture
def sleeps(monkeypatch):
    """抹掉真实 sleep 与抖动，记录每次退避时长。"""
    delays: list[float] = []

    async def _fake_sleep(d):
        delays.append(d)

    monkeypatch.setattr(retry.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(retry.random, "uniform", lambda _a, _b: 0.0)
    return delays


async def test_permanent_fails_fast(sleeps):
    fn = Flaky([PermanentError("boom")])
    with pytest.raises(PermanentError):
        await with_retry(fn, base_delay=1.0)
    assert fn.calls == 1          # 永久错误不重试
    assert sleeps == []           # 也没有退避


async def test_transient_then_success(sleeps):
    fn = Flaky([httpx.ReadTimeout("t")])
    assert await with_retry(fn, base_delay=1.0, max_attempts=3) == "ok"
    assert fn.calls == 2
    assert sleeps == [1.0]        # 1 次重试，线性退避 base*1


async def test_rate_limit_uses_exponential(sleeps):
    fn = Flaky([_exc_with_status(429), _exc_with_status(429)])
    assert await with_retry(fn, base_delay=1.0, max_attempts=5) == "ok"
    assert fn.calls == 3
    assert sleeps == [1.0, 2.0]   # 指数：2^0, 2^1


async def test_other_transient_uses_linear(sleeps):
    fn = Flaky([httpx.ConnectError("c"), httpx.ConnectError("c")])
    assert await with_retry(fn, base_delay=1.0, max_attempts=5) == "ok"
    assert fn.calls == 3
    assert sleeps == [1.0, 2.0]   # 线性：1*1, 1*2


async def test_exhausted_reraises_original(sleeps):
    fn = Flaky([_exc_with_status(503), _exc_with_status(503), _exc_with_status(503)])
    with pytest.raises(Exception) as ei:
        await with_retry(fn, base_delay=1.0, max_attempts=3)
    assert fn.calls == 3          # 跑满 max_attempts
    assert sleeps == [1.0, 2.0]
    assert ei.value.status_code == 503   # 原始异常原样上抛，不包装
