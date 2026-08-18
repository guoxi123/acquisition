"""score_sellers 规则评分 eval：纯规则（无 LLM），断言打分排序逻辑符合货代优先级。

跑：cd backend && .venv/bin/python -m pytest tests/agent_eval/test_score_sellers.py -v
放 agent_eval 是因为它验证的是「评分策略是否符合业务预期」，改评分公式前后跑。
"""

import pytest

from app.agent.v2.nodes import score_sellers


def _mk(seller_id: str, feedback: int, country: str | None) -> dict:
    return {
        "seller_id": seller_id,
        "detail": {"feedbackCount": feedback},
        "business_country": country,
    }


async def test_china_bonus_beats_non_china():
    """中国卖家 +20 加分应排在同体量非中国卖家前。"""
    sellers = [
        _mk("us_big", 3000, "US"),      # 10 + 30 = 40
        _mk("cn_mid", 2000, "China"),   # 10 + 20 + 20 = 50
    ]
    r = await score_sellers({"sellers": sellers})
    ids = [s["seller_id"] for s in r["scored_sellers"]]
    assert ids[0] == "cn_mid", "同量级下中国卖家应优先（近距离优势）"


async def test_feedback_cap_and_order():
    """feedback 权重 0-70 封顶；大体量在前。"""
    sellers = [
        _mk("small", 100, "US"),        # 10 + 1 = 11
        _mk("large", 20000, "US"),      # 10 + 70(cap) = 80
        _mk("mid", 5000, "US"),         # 10 + 50 = 60
    ]
    r = await score_sellers({"sellers": sellers})
    ids = [s["seller_id"] for s in r["scored_sellers"]]
    assert ids == ["large", "mid", "small"]


async def test_score_bounds():
    """评分落在 [10, 100]。"""
    sellers = [
        _mk("zero", 0, None),
        _mk("huge", 999999, "China"),
    ]
    r = await score_sellers({"sellers": sellers})
    for s in r["scored_sellers"]:
        assert 10 <= s["seller_score"] <= 100


async def test_empty_sellers():
    """空列表不炸。"""
    r = await score_sellers({"sellers": []})
    assert r["scored_sellers"] == []
