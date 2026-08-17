"""路由条件单元测试：_route_acquire 的够/不够/上限/0新增/首轮判定。纯逻辑，不依赖 DB。"""
from app.agent.v2.graph import _route_acquire


def test_route_enough():
    """sellers >= remaining → llm_analysis"""
    assert _route_acquire({"sellers": [1, 2, 3], "remaining": 3, "fetch_round": 0}) == "llm_analysis"


def test_route_not_enough():
    """sellers < remaining → call_actors"""
    assert _route_acquire({"sellers": [1], "remaining": 3, "fetch_round": 0}) == "call_actors"


def test_route_max_rounds():
    """达 max_rounds → llm_analysis（即使不够）"""
    assert _route_acquire({"sellers": [1], "remaining": 3, "fetch_round": 3, "max_rounds": 3}) == "llm_analysis"


def test_route_zero_new():
    """上一轮 0 新增（源耗尽）→ llm_analysis"""
    assert _route_acquire({"sellers": [1], "remaining": 3, "fetch_round": 1, "last_new_count": 0}) == "llm_analysis"


def test_route_first_round_zero_not_exhausted():
    """首轮 last_new=0 不算耗尽（还没采集过）→ call_actors"""
    assert _route_acquire({"sellers": [], "remaining": 3, "fetch_round": 0, "last_new_count": 0}) == "call_actors"


def test_route_target_field():
    """有 target 字段时用 target 而非 remaining"""
    assert _route_acquire({"sellers": [1, 2], "target": 2, "remaining": 10, "fetch_round": 0}) == "llm_analysis"
    assert _route_acquire({"sellers": [1], "target": 5, "remaining": 10, "fetch_round": 0}) == "call_actors"
