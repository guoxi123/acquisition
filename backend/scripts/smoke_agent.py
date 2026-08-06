"""Agent 编排冒烟测试（无需 Apify / 智谱 token）。

验证：① LangGraph 图编译 ② save_node 入库逻辑（lead / signal / contact）。
不调用 LLM 与 Apify，用假候选数据走最后入库一环。

用法：cd backend && uv run python scripts/smoke_agent.py
"""

import asyncio
import os
import sys

# 让 scripts/ 下的脚本能 import app 包（cwd=backend 时也兼容）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.agent.orchestrator import build_graph, save_node
from app.core.db import async_session
from app.models.campaign import Campaign
from app.models.lead import Contact, Lead, LeadSignal


async def main() -> None:
    # ① 图编译
    graph = build_graph()
    print("graph OK, nodes:", list(graph.get_graph().nodes))

    # ② 造一个 campaign + 假线索，跑 save_node
    async with async_session() as db:
        c = Campaign(name="smoke-test", category="outdoor furniture", market="US")
        db.add(c)
        await db.commit()
        await db.refresh(c)
        campaign_id = str(c.id)

    state = {
        "campaign_id": campaign_id,
        "category": "outdoor furniture",
        "market": "US",
        "leads": [
            {
                "storefront_url": "https://www.amazon.com/sp?seller=SMOKE",
                "brand": "SmokeBrand",
                "profile": {
                    "brand": "SmokeBrand",
                    "company": "SmokeCo",
                    "rating": 4.5,
                    "review_count": 200,
                    "sku_count": 30,
                    "is_fba": True,
                    "business_email": "sales@smoke.test",
                    "website": "https://smoke.test",
                },
                "score": {
                    "total_score": 78.5,
                    "tier": "recommend",
                    "signals": [
                        {"dimension": "scale", "score": 80, "detail": "200 评论/30 SKU"},
                        {"dimension": "reach", "score": 90, "detail": "有公开邮箱"},
                    ],
                    "reasoning": "规模中等、可触达",
                },
            }
        ],
    }
    res = await save_node(state)
    print("save_node ->", res["leads"])

    # ③ 查库确认 lead / signal / contact 都入库
    async with async_session() as db:
        lead = (
            await db.execute(select(Lead).where(Lead.campaign_id == c.id))
        ).scalars().first()
        sigs = (
            await db.execute(select(LeadSignal).where(LeadSignal.lead_id == lead.id))
        ).scalars().all()
        contacts = (
            await db.execute(select(Contact).where(Contact.lead_id == lead.id))
        ).scalars().all()
    print(f"db: leads=1 signals={len(sigs)} contacts={len(contacts)}")
    print(f"lead tier={lead.score_tier} total={lead.total_score}")


if __name__ == "__main__":
    asyncio.run(main())
