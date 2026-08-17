"""协作式取消单元测试：check_cancel 检测 cancelled 标志 → raise CancelledByUser。

不连真实 DB：monkeypatch DB get 返回 mock msg。
"""
import uuid

import pytest

from app.agent.v2.nodes import CancelledByUser, check_cancel


class _MockMsg:
    def __init__(self, meta):
        self.meta = meta


class _MockSession:
    def __init__(self, msg):
        self._msg = msg

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, _model, _id):
        return self._msg


@pytest.mark.asyncio
async def test_check_cancel_raises_when_cancelled(monkeypatch):
    """meta.cancelled=True → raise CancelledByUser"""
    mock_msg = _MockMsg({"cancelled": True})
    monkeypatch.setattr("app.core.db.async_session", lambda: _MockSession(mock_msg))
    with pytest.raises(CancelledByUser):
        await check_cancel(str(uuid.uuid4()))


@pytest.mark.asyncio
async def test_check_cancel_passes_when_not_cancelled(monkeypatch):
    """meta 无 cancelled → 正常返回"""
    mock_msg = _MockMsg({"done": False})
    monkeypatch.setattr("app.core.db.async_session", lambda: _MockSession(mock_msg))
    await check_cancel(str(uuid.uuid4()))


@pytest.mark.asyncio
async def test_check_cancel_passes_when_msg_none(monkeypatch):
    """消息不存在 → 正常返回"""
    monkeypatch.setattr("app.core.db.async_session", lambda: _MockSession(None))
    await check_cancel(str(uuid.uuid4()))


@pytest.mark.asyncio
async def test_check_cancel_passes_when_no_msg_id():
    """msg_id 为空 → 正常返回"""
    await check_cancel("")
    await check_cancel(None)
