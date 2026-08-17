"""sms send_sms_code 单元测试：mock httpx，覆盖 mock 模式 + 阿里云成功/失败。

不连真实阿里云。
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.core import sms as sms_mod


@pytest.mark.asyncio
async def test_send_mock_when_no_credentials(monkeypatch):
    """凭证未配置 → mock 模式（返回 ok + code）"""
    monkeypatch.setattr(sms_mod.settings, "aliyun_sms_access_key_id", "")
    monkeypatch.setattr(sms_mod.settings, "aliyun_sms_access_key_secret", "")
    result = await sms_mod.send_sms_code("13800000000", "123456")
    assert result["ok"] is True
    assert result["mock"] is True
    assert result["code"] == "123456"


@pytest.mark.asyncio
async def test_send_success_with_credentials(monkeypatch):
    """凭证配置了 + 阿里云返回 Code=OK → 成功"""
    monkeypatch.setattr(sms_mod.settings, "aliyun_sms_access_key_id", "test_ak")
    monkeypatch.setattr(sms_mod.settings, "aliyun_sms_access_key_secret", "test_sk")
    monkeypatch.setattr(sms_mod.settings, "aliyun_sms_template_code", "SMS_123")

    class _MockResp:
        def json(self):
            return {"Code": "OK", "Message": "OK"}

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=_MockResp())

    with patch("app.core.sms.httpx.AsyncClient", return_value=mock_client):
        result = await sms_mod.send_sms_code("13800000000", "654321")
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_send_fail_aliyun_error(monkeypatch):
    """凭证配置了 + 阿里云返回错误 → ok=False"""
    monkeypatch.setattr(sms_mod.settings, "aliyun_sms_access_key_id", "test_ak")
    monkeypatch.setattr(sms_mod.settings, "aliyun_sms_access_key_secret", "test_sk")
    monkeypatch.setattr(sms_mod.settings, "aliyun_sms_template_code", "SMS_123")

    class _MockResp:
        def json(self):
            return {"Code": "isv.BUSINESS_LIMIT_CONTROL", "Message": "频率太高"}

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=_MockResp())

    with patch("app.core.sms.httpx.AsyncClient", return_value=mock_client):
        result = await sms_mod.send_sms_code("13800000000", "000000")
    assert result["ok"] is False
    assert "频率" in result["error"]


@pytest.mark.asyncio
async def test_send_network_error(monkeypatch):
    """凭证配置了 + 网络异常 → ok=False"""
    monkeypatch.setattr(sms_mod.settings, "aliyun_sms_access_key_id", "test_ak")
    monkeypatch.setattr(sms_mod.settings, "aliyun_sms_access_key_secret", "test_sk")
    monkeypatch.setattr(sms_mod.settings, "aliyun_sms_template_code", "SMS_123")

    with patch("app.core.sms.httpx.AsyncClient", side_effect=Exception("timeout")):
        result = await sms_mod.send_sms_code("13800000000", "999999")
    assert result["ok"] is False
    assert "timeout" in result["error"]
