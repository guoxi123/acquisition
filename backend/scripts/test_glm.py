"""GLM-5.1 连通性 + structured output 验证（无需 Apify）。

验证：① GLM 调通 ② with_structured_output(SellerProfile) ③ with_structured_output(ScoreResult)。
用假卖家数据，不依赖 Apify。

用法：cd backend && uv run python scripts/test_glm.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.llm import get_llm, get_structured_llm
from app.agent.prompts import (
    EXTRACT_SYSTEM,
    EXTRACT_USER_TEMPLATE,
    SCORE_SYSTEM,
    SCORE_USER_TEMPLATE,
)
from app.agent.schemas import ScoreResult, SellerProfile

FAKE_PROFILE_RAW = {
    "seller_name": "PatioPrime",
    "rating": 4.7,
    "feedback_count": 5320,
    "business_address": "California, US",
}
FAKE_PRODUCTS = [
    {"title": "7-Piece Outdoor Patio Dining Set", "price": 299, "ratings_count": 420},
    {"title": "Folding Camping Chair", "price": 39, "ratings_count": 1800},
    {"title": "Rattan Sofa Set", "price": 549, "ratings_count": 260},
]


async def main() -> None:
    llm = get_llm()

    print("=== ① 基础调用 ===")
    resp = await llm.ainvoke("回复：GLM 连通正常")
    print("basic:", str(resp.content)[:80])

    print("\n=== ② 提取 structured output ===")
    extractor = get_structured_llm(SellerProfile)
    profile = await extractor.ainvoke(
        [
            {"role": "system", "content": EXTRACT_SYSTEM},
            {
                "role": "user",
                "content": EXTRACT_USER_TEMPLATE.format(
                    category="outdoor furniture",
                    market="US",
                    candidate="PatioPrime",
                    profile=FAKE_PROFILE_RAW,
                    products=FAKE_PRODUCTS,
                ),
            },
        ]
    )
    print("profile:", profile.model_dump())

    print("\n=== ③ 评分 structured output ===")
    scorer = get_structured_llm(ScoreResult)
    result = await scorer.ainvoke(
        [
            {"role": "system", "content": SCORE_SYSTEM},
            {
                "role": "user",
                "content": SCORE_USER_TEMPLATE.format(
                    category="outdoor furniture",
                    profile=profile.model_dump(),
                ),
            },
        ]
    )
    print(f"total={result.total_score} tier={result.tier}")
    for s in result.signals:
        print(f"  {s.dimension}: {s.score} — {s.detail}")


if __name__ == "__main__":
    asyncio.run(main())
