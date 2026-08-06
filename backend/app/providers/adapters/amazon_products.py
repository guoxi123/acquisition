"""amazon-seller-products adapter：用 sellerId 抓卖家全产品（货量/品类信号）。

input 参考 easyparser/amazon-seller-products: {seller_id, domain, max_pages}
domain 用 easyparser 格式（带点）：.com / .co.uk / .de ...
"""

import asyncio
from typing import Any

ACTOR_ID = "easyparser/amazon-seller-products"


async def fetch_seller_products(
    client, seller_id: str, domain: str = ".com"
) -> list[dict[str, Any]]:
    run_input = {"seller_id": seller_id, "domain": domain, "max_pages": 1}
    run = await asyncio.to_thread(
        lambda: client.actor(ACTOR_ID).call(run_input=run_input)
    )
    items = await asyncio.to_thread(
        lambda: list(client.dataset(run.default_dataset_id).iterate_items())
    )
    return items
