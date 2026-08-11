"""filter_my_sellers 排序分支单测：sort_by=feedback → ORDER BY total_feedback DESC。

跑：cd backend && uv run pytest tests/agent/test_skills.py -v
纯查询构造验证（编译 SQL 看 ORDER BY），不起 DB、不依赖 LLM。
"""

import uuid

from app.agent.skills import _filter_sellers_query

UID = str(uuid.uuid4())


def _order_by_clause(sort_by: str) -> str:
    """构造查询并取出 ORDER BY 子句文本（不含 LIMIT）。"""
    q = _filter_sellers_query(UID, sort_by=sort_by)
    sql = str(q.compile(compile_kwargs={"literal_binds": True}))
    return sql.split("ORDER BY")[1].split("LIMIT")[0].strip()


def test_sort_by_feedback_uses_total_feedback():
    clause = _order_by_clause("feedback")
    assert "total_feedback" in clause
    assert "DESC" in clause


def test_sort_by_score_uses_seller_score():
    clause = _order_by_clause("score")
    assert "seller_score" in clause
    assert "DESC" in clause
    assert "total_feedback" not in clause


def test_sort_by_unknown_falls_back_to_score():
    # LLM 传了非法值：安全回退评分，不报错、不误按 feedback
    clause = _order_by_clause("whatever")
    assert "seller_score" in clause
