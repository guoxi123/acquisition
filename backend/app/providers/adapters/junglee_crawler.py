"""junglee/amazon-crawler adapter：按品类搜索 URL 抓产品 + seller（自动发现）。

input: {country, categoryOrProductUrls: [{url: amazon搜索URL}]}
返回产品列表，每个含 seller(id/name/businessName/address)、brand、reviewsCount、
price、bestsellerRanks 等——数据足以评分，无需再调 amazon-sellers-scraper。
"""

import asyncio
from typing import Any
from urllib.parse import quote_plus

ACTOR_ID = "junglee/amazon-crawler"

# market → (搜索站点前缀, country)
MARKET_SEARCH = {
    "US": ("https://www.amazon.com/s?k=", "US"),
    "UK": ("https://www.amazon.co.uk/s?k=", "UK"),
    "DE": ("https://www.amazon.de/s?k=", "DE"),
    "JP": ("https://www.amazon.co.jp/s?k=", "JP"),
}


async def discover_products_by_category(
    client, category: str, market: str, max_items: int = 20
) -> list[dict[str, Any]]:
    search_base, country = MARKET_SEARCH.get(market, MARKET_SEARCH["US"])
    url = search_base + quote_plus(category)
    run_input = {
        "country": country,
        "categoryOrProductUrls": [{"url": url}],
        "maxItemsPerStartUrl": max_items,  # 限制抓取量，控成本
    }
    run = await asyncio.to_thread(
        lambda: client.actor(ACTOR_ID).call(run_input=run_input)
    )
    items = await asyncio.to_thread(
        lambda: list(client.dataset(run.default_dataset_id).iterate_items())
    )
    return items
