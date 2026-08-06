"""端到端：真实 Apify + GLM 跑完整获客链路（discover→enrich→extract→score→save）。

会产生 Apify 调用费用。用法：
  cd backend && uv run python scripts/run_e2e.py "<category>" "<market>"
默认：outdoor furniture / US
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.orchestrator import run_agent
from app.core.db import async_session
from app.models.campaign import Campaign


async def main() -> None:
    category = sys.argv[1] if len(sys.argv) > 1 else "outdoor furniture"
    market = sys.argv[2] if len(sys.argv) > 2 else "US"

    async with async_session() as db:
        c = Campaign(name=f"e2e-{category}", category=category, market=market)
        db.add(c)
        await db.commit()
        await db.refresh(c)
        cid = str(c.id)

    print(f"campaign {cid} ({category}/{market}), running agent ...")
    r = await run_agent(cid, category, market)
    print("candidates:", len(r.get("candidates", [])))
    print("enriched:", len(r.get("enriched", [])))
    print("extracted:", len(r.get("extracted", [])))
    print("leads:", r.get("leads", []))
    errs = r.get("errors", [])
    if errs:
        print("errors:", errs)


if __name__ == "__main__":
    asyncio.run(main())
