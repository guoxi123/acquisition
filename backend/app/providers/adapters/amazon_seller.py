"""amazon-sellers-scraper adapter：用 sellerId 抓卖家画像（业务名/地址/评级）。

input 参考 actor example: {sellerIds, domain, maxResults, proxyConfiguration}
注意：amazon-seller-products（货量）adapter 暂未接，W3 补。
"""

import asyncio
from typing import Any

ACTOR_ID = "automation-lab/amazon-sellers-scraper"


async def fetch_seller_profile(
    client, seller_id: str, domain: str = "amazon.com"
) -> dict[str, Any]:
    """单个卖家详情（兼容）。批量场景用 fetch_seller_profiles。"""
    run_input = {
        "sellerIds": [seller_id],
        "domain": domain,
        "maxResults": 1,
        "proxyConfiguration": {
            "useApifyProxy": True,
            "apifyProxyGroups": ["RESIDENTIAL"],
        },
    }
    run = await asyncio.to_thread(
        lambda: client.actor(ACTOR_ID).call(run_input=run_input)
    )
    items = await asyncio.to_thread(
        lambda: list(client.dataset(run.default_dataset_id).iterate_items())
    )
    return items[0] if items else {}


async def fetch_seller_profiles(
    client, seller_ids: list[str], domain: str = "amazon.com"
) -> list[dict[str, Any]]:
    """批量获取卖家详情：actor 支持一次传多个 sellerId，单次调用省费用/时间。"""
    if not seller_ids:
        return []
    run_input = {
        "sellerIds": seller_ids,
        "domain": domain,
        "maxResults": len(seller_ids),
        "proxyConfiguration": {
            "useApifyProxy": True,
            "apifyProxyGroups": ["RESIDENTIAL"],
        },
    }
    run = await asyncio.to_thread(
        lambda: client.actor(ACTOR_ID).call(run_input=run_input)
    )
    return await asyncio.to_thread(
        lambda: list(client.dataset(run.default_dataset_id).iterate_items())
    )
