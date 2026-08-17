"""SMS 签名 + 手机号校验单元测试。纯逻辑，不依赖外部。"""
import pytest

from app.core.sms import _percent_encode, _sign


def test_percent_encode_basic():
    """普通字符不变"""
    assert _percent_encode("abc123") == "abc123"


def test_percent_encode_special():
    """特殊字符 RFC3986 编码"""
    assert _percent_encode("/") == "%2F"
    assert _percent_encode("=") == "%3D"
    assert _percent_encode("&") == "%26"
    assert _percent_encode(" ") == "%20"


def test_percent_encode_chinese():
    """中文 → UTF-8 编码"""
    encoded = _percent_encode("阿里云")
    assert encoded  # 非空
    assert "%" in encoded  # 含编码


def test_sign_deterministic():
    """相同输入 → 相同签名"""
    params = {"Action": "SendSmsVerifyCode", "PhoneNumber": "13800000000"}
    sig1 = _sign(params, "test_secret")
    sig2 = _sign(params, "test_secret")
    assert sig1 == sig2


def test_sign_different_secret():
    """不同 secret → 不同签名"""
    params = {"Action": "SendSmsVerifyCode"}
    sig1 = _sign(params, "secret_a")
    sig2 = _sign(params, "secret_b")
    assert sig1 != sig2


def test_sign_different_params():
    """不同参数 → 不同签名"""
    sig1 = _sign({"Action": "A"}, "secret")
    sig2 = _sign({"Action": "B"}, "secret")
    assert sig1 != sig2


def test_sign_is_base64():
    """签名是 Base64 字符串（可 decode）"""
    import base64
    sig = _sign({"Action": "Test"}, "secret")
    decoded = base64.b64decode(sig)  # 不抛异常即合法 Base64
    assert len(decoded) == 20  # SHA1 = 20 bytes


def test_sign_param_order_independent():
    """参数顺序不影响签名（内部排序）"""
    sig1 = _sign({"a": "1", "b": "2"}, "secret")
    sig2 = _sign({"b": "2", "a": "1"}, "secret")
    assert sig1 == sig2
