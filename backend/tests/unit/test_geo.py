"""geo.parse_country 单元测试：地址 → 国籍识别。纯逻辑，不连 DB。"""
import pytest

from app.core.geo import parse_country


def test_china_english():
    assert parse_country("Shenzhen, Guangdong, China") == "China"
    assert parse_country("some addr, CN") == "China"


def test_china_chinese():
    assert parse_country("深圳市南山区科技园") == "China"
    assert parse_country("义乌市小商品市场") == "China"


def test_china_city():
    assert parse_country("Yiwu, Zhejiang") == "China"
    assert parse_country("Guangzhou, GD") == "China"


def test_us():
    assert parse_country("410 Terry Ave, Seattle, WA, US") == "US"
    assert parse_country("United States") == "US"


def test_uk():
    assert parse_country("London, United Kingdom") == "UK"
    assert parse_country("Manchester, England") == "UK"


def test_germany():
    assert parse_country("Berlin, Germany") == "Germany"
    assert parse_country("München, Deutschland") == "Germany"


def test_japan():
    assert parse_country("Tokyo, Japan") == "Japan"
    assert parse_country("大阪, 日本") == "Japan"


def test_none_for_empty():
    assert parse_country(None) is None
    assert parse_country("") is None


def test_none_for_unknown():
    assert parse_country("123 Random Street") is None
    assert parse_country("Mars Colony Alpha") is None


def test_china_priority_over_country_suffix():
    """中国关键词优先于国家后缀（如 'Shenzhen, US' 仍判 China）"""
    assert parse_country("Shenzhen, US") == "China"
