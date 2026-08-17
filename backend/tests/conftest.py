"""pytest 全局 fixtures。

单元测试（不连 DB）：直接 import 函数 + monkeypatch。
集成测试（连真实 PG）：session-scoped engine fixture（在测试 event loop 上创建，避免跨 loop）。
"""
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import db as db_mod
from app.core.config import settings


@pytest.fixture
def make_uuid():
    """生成一个 UUID 字符串（测试用）。"""
    return lambda: str(uuid.uuid4())


@pytest.fixture(scope="session", autouse=True)
async def test_engine():
    """session-scoped：在测试 event loop 上创建新 engine + sessionmaker，
    替换 db_mod 的全局 engine（import 时创建的绑定到旧 loop）。

    pytest-asyncio session scope 确保所有测试共用同一个 event loop，
    asyncpg 连接池绑定到该 loop，避免 'attached to a different loop'。
    """
    # 先 dispose 旧 engine（import 时创建的）
    await db_mod.engine.dispose()

    # 在测试 loop 上创建新 engine
    new_engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    new_session = async_sessionmaker(new_engine, class_=AsyncSession, expire_on_commit=False)

    # 替换全局引用（app.core.db.engine / async_session 被各处 import）
    old_engine = db_mod.engine
    old_session = db_mod.async_session
    db_mod.engine = new_engine
    db_mod.async_session = new_session

    yield

    await new_engine.dispose()
    # 恢复（可选，测试结束后进程退出）
    db_mod.engine = old_engine
    db_mod.async_session = old_session
