"""auth/security 单元测试：密码哈希/校验 + JWT 签发/解码。纯逻辑，不连 DB。"""
import pytest

from app.auth.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_hash_and_verify_password():
    """hash → verify 正确密码 True、错误密码 False"""
    h = hash_password("mysecret")
    assert h != "mysecret"
    assert verify_password("mysecret", h) is True
    assert verify_password("wrong", h) is False


def test_hash_unique():
    """同密码两次 hash 不同（salt）"""
    h1 = hash_password("abc")
    h2 = hash_password("abc")
    assert h1 != h2


def test_create_and_decode_token():
    """签发 → 解码 round-trip"""
    token = create_access_token({"sub": "user-123"})
    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == "user-123"


def test_decode_invalid_token():
    """无效 token → None"""
    assert decode_access_token("garbage") is None
    assert decode_access_token("") is None
