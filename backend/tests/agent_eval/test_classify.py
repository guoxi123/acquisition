"""classify_intent 意图分类 eval：跑测试集看意图分流准确率。

跑：cd backend && uv run pytest tests/agent_eval/test_classify.py -v
每个 case 一次真实 LLM 调用（测真实准确率，非 mock）；建议手动/定期跑，不进常规 CI。
"""

import pytest

from app.agent.v2.nodes import classify_intent

# (query, 期望 intent)——覆盖三类 + 口语/边界。扩充这个列表即可提升覆盖面。
CASES = [
    # acquisition：找新卖家（需采集）
    ("美国站卖杯子的中国卖家", "acquisition"),
    ("找一下英国站卖水杯的卖家", "acquisition"),
    ("德国站 FBA 卖咖啡机的", "acquisition"),
    ("欧洲站户外家具", "acquisition"),
    # query：查已获取/库存数据
    ("查看我获取的所有卖家", "query"),
    ("我收藏的卖家有哪些", "query"),
    ("帮我统计一下我获取的中国卖家", "query"),
    ("我之前查过的卖家", "query"),
    # chat：闲聊/身份
    ("你是谁", "chat"),
    ("你能做什么", "chat"),
    ("你好", "chat"),
    ("谢谢", "chat"),
]


@pytest.mark.parametrize("query, expected", CASES)
async def test_classify_intent(query: str, expected: str):
    r = await classify_intent({"user_query": query})
    assert r["intent"] == expected, f"'{query}' 期望 {expected}，实际 {r['intent']}"
