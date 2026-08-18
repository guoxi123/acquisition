"""parse_intent 提参 eval：golden set 断言 LLM 提取的全字段准确性。

跑：cd backend && uv run pytest tests/agent_eval/test_parse_intent.py -v
真实 LLM 调用（非 mock），改 INTENT_PROMPT / 换模型前后跑，对比通过率。
"""

import pytest

from app.agent.v2.intent import ParsedIntent, INTENT_PROMPT, parse_intent
from app.agent.orchestrator import _structured_invoke


# (query, 期望字段)——只断言写出的键，没写的不断言（LLM 有自由度）。
# 扩充这个列表即可提升覆盖面。
CASES = [
    # 最简：站点 + 品类
    (
        "美国站卖杯子的中国卖家",
        {"marketplace": "amazon.com", "category": "cups", "business_country": "China"},
    ),
    # 欧洲站 → amazon.co.uk（此前线上 bug：识别错市场）
    (
        "欧洲站卖杯子卖家",
        {"marketplace": "amazon.co.uk", "category": "cups"},
    ),
    # 英国/德国/日本站映射
    (
        "英国站卖水杯的",
        {"marketplace": "amazon.co.uk", "category": "cups"},
    ),
    (
        "德国站 FBA 卖咖啡机的",
        {"marketplace": "amazon.de", "category": "coffee machine", "service_mode": "FBA"},
    ),
    (
        "日本站户外家具",
        {"marketplace": "amazon.co.jp", "category": "outdoor furniture"},
    ),
    # 全字段：国籍 + 规模 + 数量
    (
        "美国站找10个卖户外家具的中国大卖家",
        {
            "marketplace": "amazon.com",
            "category": "outdoor furniture",
            "business_country": "China",
            "desired_count": 10,
        },
    ),
    # 缺站点 → need_confirm
    (
        "卖杯子的",
        {"marketplace": None, "category": "cups", "need_confirm": True},
    ),
    # 缺品类 → need_confirm
    (
        "美国站的卖家",
        {"marketplace": "amazon.com", "category": None, "need_confirm": True},
    ),
    # 运输方式
    (
        "美国站海运的杯子卖家",
        {"marketplace": "amazon.com", "shipping_type": "sea"},
    ),
    # 美国卖家（非中国）
    (
        "美国站卖杯子的美国卖家",
        {"marketplace": "amazon.com", "business_country": "US"},
    ),
    # 口语化：找一下 / 帮我
    (
        "帮我找下英国站卖宠物用品的卖家",
        {"marketplace": "amazon.co.uk"},
    ),
]


@pytest.mark.parametrize("query, expected", CASES)
async def test_parse_intent_fields(query: str, expected: dict):
    """单条 query → 真实 LLM 提参 → 逐字段断言。"""
    msgs = [
        {"role": "system", "content": INTENT_PROMPT},
        {"role": "user", "content": query},
    ]
    parsed = await _structured_invoke(ParsedIntent, msgs, "eval_parse_intent")
    assert parsed is not None, "LLM 解析失败"

    wrong = []
    for k, want in expected.items():
        got = getattr(parsed, k)
        if got != want:
            wrong.append(f"{k}: 期望 {want!r}，实际 {got!r}")
    assert not wrong, f"'{query}' → " + "；".join(wrong)


async def test_parse_intent_node_minimal():
    """节点级最小冒烟：不缺字段时 human_approved=True 且不触发 HITL。"""
    state = {"user_query": "美国站卖杯子的卖家"}
    r = await parse_intent(state)
    assert r["marketplace"] == "amazon.com"
    assert r["category"] == "cups"
    assert r["need_human_confirm"] is False
    assert r["human_approved"] is True


async def test_parse_intent_node_missing_triggers_hitl():
    """缺站点 → HITL 追问。"""
    state = {"user_query": "卖杯子的"}
    r = await parse_intent(state)
    assert r["need_human_confirm"] is True
    assert "目标市场" in r["missing"]
