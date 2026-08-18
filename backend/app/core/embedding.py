"""Embedding 客户端 + 入库文本构造（RAG 语义检索用）。

直调 OpenAI 兼容 /embeddings（硅基流动 bge-m3），不引 openai SDK：
单一端点 httpx 足够，且能复用 core.retry.with_retry 的瞬时/永久分类。
"""

import logging

import httpx

from app.core.config import settings
from app.core.retry import with_retry

logger = logging.getLogger(__name__)

_BATCH_SIZE = 64  # SiliconFlow 单请求上限


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量生成 embedding，返回顺序与输入一致。空输入返回 []。

    429/5xx/超时由 with_retry 重试；4xx（鉴权/参数错）快速失败上抛。
    """
    if not texts:
        return []
    out: list[list[float]] = []
    async with httpx.AsyncClient(timeout=30) as client:
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i : i + _BATCH_SIZE]
            out.extend(await _embed_batch(client, batch))
    return out


async def _embed_batch(client: httpx.AsyncClient, batch: list[str]) -> list[list[float]]:
    payload = {"model": settings.embedding_model, "input": batch}

    def resp_code(exc: BaseException) -> int | None:
        return getattr(exc, "status_code", None)

    async def call() -> list[list[float]]:
        resp = await client.post(
            f"{settings.embedding_base_url}/embeddings",
            headers={"Authorization": f"Bearer {settings.embedding_api_key}"},
            json=payload,
        )
        # 4xx 手动分类：429/5xx 瞬时，其余永久（retry.classify 只认异常对象）
        if resp.status_code >= 400:
            body = resp.text[:200]
            if resp.status_code == 429 or resp.status_code >= 500:
                from app.core.retry import TransientError

                raise TransientError(
                    f"embedding API {resp.status_code}: {body}",
                    rate_limited=(resp.status_code == 429),
                )
            from app.core.retry import PermanentError

            raise PermanentError(f"embedding API {resp.status_code}: {body}")
        data = resp.json()["data"]
        # 按 index 对齐，防服务端乱序
        ordered = sorted(data, key=lambda d: d["index"])
        return [d["embedding"] for d in ordered]

    return await with_retry(call, label="embeddings")


def seller_text(s) -> str:
    """卖家入库文本：name/category/business_name 拼接（None 跳过）。"""
    parts = [s.name, s.category, s.business_name]
    return " | ".join(p for p in parts if p)


def product_text(p) -> str:
    """商品入库文本：title/brand/bread_crumbs 拼接（None 跳过）。"""
    parts = [p.title, p.brand, p.bread_crumbs]
    return " | ".join(p for p in parts if p)


def seller_query_text(category: str, marketplace: str) -> str:
    """查询侧文本：与 seller_text 形态对齐，让语义空间一致。"""
    return f"{category} seller | {category} | {marketplace}"
