"""embed_texts 单测：mock httpx，验证空输入 / 分批 / 4xx-5xx 分类 / 文本构造。"""

from types import SimpleNamespace

import httpx
import pytest

from app.core import embedding as emb_mod
from app.core.embedding import embed_texts, product_text, seller_query_text, seller_text


def _mock_response(status: int, body: dict) -> httpx.Response:
    import json

    return httpx.Response(
        status,
        content=json.dumps(body).encode(),
        request=httpx.Request("POST", "https://x/embeddings"),
    )


@pytest.mark.asyncio
async def test_empty_input(monkeypatch):
    assert await embed_texts([]) == []


@pytest.mark.asyncio
async def test_batch_and_order(monkeypatch):
    """超 64 条分两批；结果按输入顺序对齐（服务端乱序返回也要排回）。"""
    calls = []

    async def fake_post(self, url, **kwargs):
        calls.append(kwargs["json"]["input"])
        start = (len(calls) - 1) * 64
        input_list = kwargs["json"]["input"]
        data = [
            {"index": i, "embedding": [float(start + i)]} for i in range(len(input_list))
        ]
        data.reverse()  # 乱序返回，验证 _embed_batch 按 index 对齐
        return _mock_response(200, {"data": data})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    out = await embed_texts([f"t{i}" for i in range(70)])
    assert len(calls) == 2
    assert len(calls[0]) == 64 and len(calls[1]) == 6
    assert [e[0] for e in out] == [float(i) for i in range(70)]


@pytest.mark.asyncio
async def test_500_retries_then_ok(monkeypatch):
    """5xx → TransientError 走重试；第 2 次成功。"""
    state = {"n": 0}

    async def fake_post(self, url, **kwargs):
        state["n"] += 1
        if state["n"] == 1:
            return _mock_response(500, {"error": "boom"})
        return _mock_response(200, {"data": [{"index": 0, "embedding": [1.0]}]})

    async def no_sleep(delay):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr("asyncio.sleep", no_sleep)
    out = await embed_texts(["hello"])
    assert out == [[1.0]] and state["n"] == 2


@pytest.mark.asyncio
async def test_429_rate_limited(monkeypatch):
    """429 → TransientError(rate_limited=True) → 指数退避重试路径。"""
    from app.core.retry import TransientError

    async def fake_post(self, url, **kwargs):
        return _mock_response(429, {"error": "rate"})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    with pytest.raises(TransientError) as exc_info:
        await embed_texts(["hello"])
    assert exc_info.value.rate_limited is True


@pytest.mark.asyncio
async def test_401_permanent_no_retry(monkeypatch):
    """4xx（非 429/5xx）→ PermanentError 快速失败。"""
    from app.core.retry import PermanentError

    calls = {"n": 0}

    async def fake_post(self, url, **kwargs):
        calls["n"] += 1
        return _mock_response(401, {"error": "bad key"})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    with pytest.raises(PermanentError):
        await embed_texts(["hello"])
    assert calls["n"] == 1


def test_text_constructors():
    s = SimpleNamespace(name="ACME", category="Pet Supplies", business_name=None)
    assert seller_text(s) == "ACME | Pet Supplies"
    p = SimpleNamespace(title="Dog Bowl", brand="ACME", bread_crumbs="Home > Kitchen")
    assert product_text(p) == "Dog Bowl | ACME | Home > Kitchen"
    assert seller_query_text("pet supplies", "amazon.com") == "pet supplies seller | pet supplies | amazon.com"
