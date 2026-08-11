"""query_agent 的可扩展工具集（skills）。

加一个查询功能 = 写一个 @tool 函数 + 加进 ALL_TOOLS，query_agent 自动可用，
不改 classify、不改图。每个 tool 都是纯函数（查库 → 返回结构化数据）。
"""

import uuid

from langchain_core.tools import tool
from sqlalchemy import select

from app.core.db import async_session
from app.models.seller import Seller
from app.models.user_acquired_seller import UserAcquiredSeller


def _seller_to_dict(s: Seller, acquired_at=None, seller_score=None) -> dict:
    """统一的 seller 序列化（前端表格 + LLM 回答共用）。

    seller_score 来自 user_acquired_sellers 的获取时快照（采集时算出、grant 时写入）；
    未传则回退 sellers.seller_score。
    """
    return {
        "seller_id": s.seller_id,
        "name": s.name,
        "business_country": s.business_country,
        "total_feedback": s.total_feedback,
        "member_since": s.member_since,
        "seller_score": seller_score if seller_score is not None else s.seller_score,
        "contacts": s.contacts or [],
        "acquired_at": acquired_at.isoformat() if acquired_at else None,
    }


@tool
async def get_my_sellers(user_id: str, limit: int = 100) -> list:
    """查看当前用户已获取过的所有亚马逊卖家（含国籍、评分、联系方式、获取时间）。
    用户问“我的卖家 / 我获取的 / 已获取的 / 收藏的卖家”时调用本工具。
    参数 user_id 由系统从登录态注入，不要向用户询问。
    """
    async with async_session() as db:
        rows = (
            await db.execute(
                select(Seller, UserAcquiredSeller.acquired_at, UserAcquiredSeller.seller_score)
                .join(UserAcquiredSeller, UserAcquiredSeller.seller_id == Seller.seller_id)
                .where(UserAcquiredSeller.user_id == uuid.UUID(user_id))
                .order_by(UserAcquiredSeller.acquired_at.desc())
                .limit(limit)
            )
        ).all()
    return [
        _seller_to_dict(s, acquired_at, seller_score=score)
        for s, acquired_at, score in rows
    ]


def _filter_sellers_query(
    user_id: str,
    country: str = "",
    min_score: int = 0,
    category: str = "",
    sort_by: str = "score",
    limit: int = 100,
):
    """构造「按条件筛选已获取卖家」的查询（纯函数，便于单测排序分支）。

    评分取 user_acquired_sellers.seller_score（获取时快照），不用 sellers.seller_score。
    """
    q = (
        select(Seller, UserAcquiredSeller.seller_score)
        .join(UserAcquiredSeller, UserAcquiredSeller.seller_id == Seller.seller_id)
        .where(UserAcquiredSeller.user_id == uuid.UUID(user_id))
    )
    if country:
        q = q.where(Seller.business_country == country)
    if min_score:
        q = q.where(UserAcquiredSeller.seller_score >= min_score)
    if category:
        q = q.where(Seller.category.ilike(f"%{category}%"))
    # sort_by=feedback 按反馈量倒序（用户说「按反馈数量/反馈量从高到低」）；其余按评分倒序
    order_col = Seller.total_feedback if sort_by == "feedback" else UserAcquiredSeller.seller_score
    return q.order_by(order_col.desc().nullslast()).limit(limit)


@tool
async def filter_my_sellers(
    user_id: str,
    country: str = "",
    min_score: int = 0,
    category: str = "",
    sort_by: str = "score",
    limit: int = 100,
) -> list:
    """按条件筛选当前用户已获取的亚马逊卖家（默认按评分倒序）。
    用户问"我获取的【中国】卖家 / 【高分】卖家 / 【某品类】的卖家"等带条件的查询时调用；
    用户要求"按反馈数量 / 反馈量从高到低排序"时设 sort_by=feedback。
    参数：country 国籍(如 China/US/DE)、min_score 评分下限(0-100)、category 品类关键词、
    sort_by 排序字段 score(评分,默认) 或 feedback(反馈数量)，均倒序(高到低)；均可选；user_id 系统注入。
    """
    async with async_session() as db:
        rows = (
            await db.execute(
                _filter_sellers_query(user_id, country, min_score, category, sort_by, limit)
            )
        ).all()
    return [_seller_to_dict(s, seller_score=score) for s, score in rows]


# 工具注册表：query_agent 用这里的全部工具。新功能追加进来即可。
ALL_TOOLS = [get_my_sellers, filter_my_sellers]
