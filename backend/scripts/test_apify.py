"""Apify 连通性测试：在 backend/.env 配好 APIFY_API_TOKEN 后运行。

用法：
    cd backend
    uv run python scripts/test_apify.py

会真实调用 google-search-scraper（产生少量 Apify 费用），用于验证 token 与 Actor 可达。
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.providers.apify_provider import ApifyProvider, _get_client


async def main() -> None:
    try:
        _get_client()
    except RuntimeError as e:
        print(f"❌ {e}")
        sys.exit(1)

    provider = ApifyProvider()
    print("▶ 调用 apify/google-search-scraper 发现卖家 ...")
    candidates = await provider.discover_sellers(category="outdoor furniture", market="US")
    print(f"✓ 发现 {len(candidates)} 个候选")
    for c in candidates[:3]:
        print(f"  - {c.brand}: {c.storefront_url}")


if __name__ == "__main__":
    asyncio.run(main())
