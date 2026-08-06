"""google-search-scraper adapter：发现候选亚马逊卖家。

run_input 参考 https://apify.com/apify/google-search-scraper
"""

import asyncio

from app.providers.base import SellerCandidate

ACTOR_ID = "apify/google-search-scraper"


async def discover_via_google(client, category: str, market: str) -> list[SellerCandidate]:
    query = f"amazon {category} seller storefront"
    run_input = {
        "queries": query,
        "maxPagesPerQuery": 1,
        "resultsPerPage": 10,
    }
    run = await asyncio.to_thread(
        lambda: client.actor(ACTOR_ID).call(run_input=run_input)
    )
    # apify-client 3.x：call() 返回 Run model，经 client.dataset(run.default_dataset_id) 取结果
    items = await asyncio.to_thread(
        lambda: list(client.dataset(run.default_dataset_id).iterate_items())
    )

    candidates: list[SellerCandidate] = []
    for item in items:
        # 每个 item 是一个 query 的结果，organicResults 是 organic 结果数组
        for r in item.get("organicResults", []):
            url = r.get("url") or ""
            if "amazon" in url:
                candidates.append(
                    SellerCandidate(
                        storefront_url=url or None,
                        brand=r.get("title"),
                        source_query=query,
                        raw=r,
                    )
                )
    return candidates
