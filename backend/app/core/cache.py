"""Apify 数据缓存：命中（未过期）免调 actor，省费用。

cache_get 命中返回 data，否则 None；cache_set 用 PG upsert 覆盖同 source+key。
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.db import async_session
from app.models.cache import ApifyCache


async def cache_get(source: str, key: str):
    """命中且未过期返回 data，否则 None。"""
    async with async_session() as db:
        row = (
            await db.execute(
                select(ApifyCache).where(
                    ApifyCache.source == source,
                    ApifyCache.cache_key == key,
                    ApifyCache.expires_at > datetime.now(timezone.utc),
                )
            )
        ).scalars().first()
        return row.data if row else None


async def cache_set(source: str, key: str, data, ttl_hours: int) -> None:
    """upsert（同 source+key 覆盖），expires_at = now + ttl。失败不阻塞主流程。"""
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=ttl_hours)
    async with async_session() as db:
        stmt = (
            pg_insert(ApifyCache)
            .values(source=source, cache_key=key, data=data, expires_at=expires)
            .on_conflict_do_update(
                constraint="uq_apify_cache_source_key",
                set_={"data": data, "expires_at": expires, "fetched_at": now},
            )
        )
        await db.execute(stmt)
        await db.commit()
