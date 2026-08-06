"""官网补联系方式测试：品牌名 → 官网 → email/phone。

用法：cd backend && uv run python scripts/test_website.py [品牌1] [品牌2] ...
默认测：PURPLE LEAF / Best Choice Products / Devoko
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.providers.website_provider import fetch_website_contacts


async def main() -> None:
    brands = sys.argv[1:] or ["PURPLE LEAF", "Best Choice Products", "Devoko"]
    for brand in brands:
        r = await fetch_website_contacts(brand)
        print(f"{brand}:")
        print(f"  website: {r.get('website')}")
        print(f"  emails:  {r.get('emails')}")
        print(f"  phones:  {r.get('phones')}")


if __name__ == "__main__":
    asyncio.run(main())
