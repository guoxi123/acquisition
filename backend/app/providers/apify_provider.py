from typing import Any

from app.core.cache import cache_get, cache_set
from app.core.config import settings
from app.providers.adapters.amazon_products import fetch_seller_products
from app.providers.adapters.amazon_seller import fetch_seller_profile
from app.providers.adapters.junglee_crawler import discover_products_by_category
from app.providers.adapters.google_search import discover_via_google
from app.providers.base import DataSourceProvider, SellerCandidate
from app.utils.logger import logger

# 延迟构造 client：token 未配时不报错，调用时才提示
_client = None


def _get_client():
    global _client
    if _client is None:
        if not settings.apify_api_token:
            raise RuntimeError("APIFY_API_TOKEN 未配置，请在 backend/.env 填入")
        from apify_client import ApifyClient

        _client = ApifyClient(settings.apify_api_token)
    return _client


async def _cached(source: str, key: str, ttl_hours: int, fetch):
    """查缓存→命中（未过期）返回；否则调 fetch→写缓存。缓存写失败不阻塞主流程。"""
    cached = await cache_get(source, key)
    if cached is not None:
        logger.debug(f"[apify] 缓存命中: {source}/{key[:30]}")
        return cached
    logger.debug(f"[apify] 缓存未命中，调 actor: {source}/{key[:30]}")
    data = await fetch()
    try:
        await cache_set(source, key, data, ttl_hours)
    except Exception:
        pass  # 缓存写失败不影响主流程
    return data


class ApifyProvider(DataSourceProvider):
    """Apify 数据 provider，三层缓存：junglee_search / amazon_seller / amazon_seller_products。

    apify-client 是同步库，所有调用用 asyncio.to_thread 包到线程池。
    """

    async def discover_sellers(
        self, category: str, market: str
    ) -> list[SellerCandidate]:
        return await discover_via_google(_get_client(), category, market)

    async def discover_products_by_category(
        self, category: str, market: str, max_items: int = 20
    ) -> list[dict[str, Any]]:
        # 品类级缓存由 V2 check_cache 节点（sellers 表）统一管；这里直接调 actor。
        return await discover_products_by_category(
            _get_client(), category, market, max_items
        )

    async def fetch_seller_profile(
        self, seller_id: str, domain: str = "amazon.com"
    ) -> dict[str, Any]:
        return await _cached(
            "amazon_seller",
            seller_id,
            settings.cache_ttl_seller_hours,
            lambda: fetch_seller_profile(_get_client(), seller_id, domain),
        )

    async def fetch_seller_products(
        self, seller_id: str, domain: str = ".com"
    ) -> list[dict[str, Any]]:
        return await _cached(
            "amazon_seller_products",
            seller_id,
            settings.cache_ttl_products_hours,
            lambda: fetch_seller_products(_get_client(), seller_id, domain),
        )
