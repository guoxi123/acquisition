"""V2 图节点：check_quota(配额前置) + query_db(去重复用库存) +
call_actors(库不够则采集一轮) + llm_analysis/score_sellers/lookup_contacts + output_result（含 HITL/配额收尾）。"""

import uuid

from app.agent.v2.state import V2State
from app.utils.logger import logger

# marketplace 域名 → junglee country
_MARKETPLACE_COUNTRY = {
    "amazon.com": "US",
    "amazon.co.uk": "UK",
    "amazon.de": "DE",
    "amazon.co.jp": "JP",
}


async def _write_msg(msg_id: str, *, content: str | None = None, meta_patch: dict | None = None) -> None:
    """合并写 assistant 消息：content 覆盖；meta_patch 合并进现有 meta（保留 progress 等已有字段）。
    ORM + flag_modified，规避 JSONB 的 in-place 不检测 / Core UPDATE 整体覆盖两个问题。"""
    from sqlalchemy.orm.attributes import flag_modified

    from app.core.db import async_session
    from app.memory.models import MemoryMessage

    async with async_session() as db:
        msg = await db.get(MemoryMessage, uuid.UUID(msg_id))
        if msg is None:
            return
        if content is not None:
            msg.content = content
        if meta_patch:
            meta = dict(msg.meta or {})
            meta.update(meta_patch)
            msg.meta = meta
            flag_modified(msg, "meta")
        await db.commit()


async def update_progress(msg_id: str, step: str, message: str, status: str = "done"):
    """更新 assistant 消息的 meta.progress（追加一条进度，供 SSE 推送给前端）。

    用新 dict + flag_modified：JSONB 字段的 in-place 改动不会被 SQLAlchemy 检测，
    会导致第二次及之后的更新不生效（第一次仅因 None→dict 才侥幸写入）。
    """
    from sqlalchemy.orm.attributes import flag_modified

    from app.core.db import async_session
    from app.memory.models import MemoryMessage

    try:
        async with async_session() as db:
            msg = await db.get(MemoryMessage, uuid.UUID(msg_id))
            if msg is None:
                return
            meta = dict(msg.meta or {})
            progress = list(meta.get("progress", []))
            progress.append({"step": step, "status": status, "message": message})
            meta["progress"] = progress
            msg.meta = meta
            flag_modified(msg, "meta")
            await db.commit()
        logger.info(f"[progress] step={step} total={len(progress)} msg={message[:30]}")
    except Exception as e:
        logger.warning(f"[progress] 写入失败: {e}")


async def check_quota(state: V2State) -> dict:
    """配额前置检查：算本月剩余配额。=0（非超管）标 quota_exhausted，由条件边转到 output_result 提示升级。"""
    from sqlalchemy import select

    from app.core.config import settings
    from app.core.db import async_session
    from app.models.user import User
    from app.quota import get_quota_summary

    msg_id = state.get("assistant_msg_id")
    user_id = state.get("user_id")
    max_rounds = settings.max_fetch_rounds

    if not user_id:
        # 未注入 user_id（兼容）：不限额，走默认目标
        if msg_id:
            await update_progress(msg_id, "check_quota", "未识别用户，按默认额度处理", "done")
        return {"remaining": settings.acquired_admin_max, "quota_exhausted": False, "max_rounds": max_rounds}

    async with async_session() as db:
        user = (
            await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
        ).scalars().first()
        if user is None or user.is_super_admin:
            remaining = settings.acquired_admin_max
            quota_exhausted = False
        else:
            summary = await get_quota_summary(db, user)
            remaining = summary.get("remaining") or 0
            quota_exhausted = remaining == 0

    if msg_id:
        if quota_exhausted:
            await update_progress(msg_id, "check_quota", "本月额度已用完，需升级", "done")
        else:
            await update_progress(msg_id, "check_quota", f"本月剩余额度 {remaining} 个卖家", "done")
    logger.info(f"[check_quota] user_id={user_id} remaining={remaining} exhausted={quota_exhausted}")
    return {"remaining": remaining, "quota_exhausted": quota_exhausted, "max_rounds": max_rounds}


async def query_db(state: V2State) -> dict:
    """查 sellers 表：marketplace + 品类模糊匹配 + 该用户未获取过，limit=remaining。
    唯一写 state["sellers"] 的节点（覆盖语义；call_actors 不返回 sellers）。"""
    from sqlalchemy import func, select

    from app.core.db import async_session
    from app.models.seller import Seller
    from app.models.user_acquired_seller import UserAcquiredSeller

    msg_id = state.get("assistant_msg_id")
    marketplace = state.get("marketplace")
    category = (state.get("category") or "").strip().lower()
    target = state.get("target") or state.get("remaining") or 0  # 本次目标数量（用户指定或剩余配额）
    user_id = state.get("user_id")
    business_country = state.get("business_country")
    min_total_feedback = state.get("min_total_feedback")
    min_seller_score = state.get("min_seller_score")


    if msg_id:
        await update_progress(msg_id, "query_db", f"查询库存（目标 {target}）…", "running")

    if not marketplace or not category or target <= 0:
        if msg_id:
            await update_progress(msg_id, "query_db", "条件不足或无额度，0 个可复用", "done")
        return {"sellers": []}

    q = select(Seller).where(
        Seller.marketplace == marketplace,
        func.lower(Seller.category).like(f"%{category}%"),
    )
    if business_country:
        q = q.where(Seller.business_country == business_country)
    if min_total_feedback:
        q = q.where(Seller.total_feedback >= min_total_feedback)
    if min_seller_score:
        # seller_score 可能为 null（采集入库时未评分），null 也保留，严格评分筛选留给 score_sellers 之后
        q = q.where((Seller.seller_score >= min_seller_score) | (Seller.seller_score.is_(None)))
    if user_id:
        q = q.where(
            ~Seller.seller_id.in_(
                select(UserAcquiredSeller.seller_id).where(
                    UserAcquiredSeller.user_id == uuid.UUID(user_id)
                )
            )
        )
    q = q.order_by(Seller.total_feedback.desc().nullslast()).limit(target)

    async with async_session() as db:
        sellers_db = list((await db.execute(q)).scalars().all())

    # 复用原 check_cache 的 sellers→dict 转换（供 llm_analysis/score_sellers/output 读）
    sellers = [
        {
            "seller_id": s.seller_id,
            "junglee_seller": {"name": s.name},
            "detail": {
                "feedbackCount": s.total_feedback,
                "memberSince": s.member_since,
                "businessName": s.business_name,
            },
            "business_country": s.business_country,
            "products": [],
            "contacts": s.contacts or [],
        }
        for s in sellers_db
    ]
    logger.info(f"[query_db] marketplace={marketplace} category={category} 复用 {len(sellers)}/{target}")
    if msg_id:
        await update_progress(msg_id, "query_db", f"库存可复用 {len(sellers)} / 目标 {target}", "done")
    return {"sellers": sellers}


async def call_actors(state: V2State) -> dict:
    """采集工具（harness 调用）：junglee 抓一批产品 → 聚合卖家 → 对差集 fetch_seller_profile+国籍 → upsert。
    不查联系方式（由 acquire_if_needed 调 lookup_contacts_for）；return sellers 供 harness 查联系方式。"""
    import asyncio

    from sqlalchemy import func, select
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.core.config import settings
    from app.core.db import async_session
    from app.core.geo import parse_country
    from app.models.product import Product
    from app.models.seller import Seller
    from app.providers.apify_provider import ApifyProvider

    marketplace = state.get("marketplace") or "amazon.com"
    country = _MARKETPLACE_COUNTRY.get(marketplace, "US")
    category = (state.get("category") or "").strip().lower() or ""
    round_no = (state.get("fetch_round") or 0) + 1
    errors: list[str] = []
    msg_id = state.get("assistant_msg_id")

    if msg_id:
        await update_progress(
            msg_id, "call_actors",
            f"第 {round_no} 轮采集（抓 {settings.fetch_batch_size} 产品）…", "running",
        )

    provider = ApifyProvider()
    try:
        products_raw = await provider.discover_products_by_category(
            category, country, max_items=settings.fetch_batch_size
        )
    except Exception as e:
        logger.error(f"[call_actors] junglee 失败: {e}")
        if msg_id:
            await update_progress(msg_id, "call_actors", f"采集失败：{e}", "error")
        return {"fetch_round": round_no, "last_new_count": 0, "errors": [f"junglee: {e}"]}

    # 聚合到卖家
    by_seller: dict[str, dict] = {}
    for p in products_raw:
        s = p.get("seller") or {}
        sid = s.get("id")
        if not sid:
            continue
        by_seller.setdefault(sid, {"seller_id": sid, "junglee_seller": s, "products": []})
        by_seller[sid]["products"].append(p)
    sellers = list(by_seller.values())

    if not sellers:
        logger.info(f"[call_actors] 第 {round_no} 轮 0 个卖家")
        if msg_id:
            await update_progress(msg_id, "call_actors", f"第 {round_no} 轮 0 个卖家", "done")
        return {"fetch_round": round_no, "last_new_count": 0, "errors": errors}

    # 差集：只对未入库 seller 调 fetch_seller_profile（已入库的画像已有，避免重复付费 actor）
    async with async_session() as db:
        existing_ids = set(
            (
                await db.execute(
                    select(Seller.seller_id).where(
                        Seller.seller_id.in_([s["seller_id"] for s in sellers])
                    )
                )
            ).scalars().all()
        )
    to_enrich = [s for s in sellers if s["seller_id"] not in existing_ids]
    logger.info(f"[call_actors] 第 {round_no} 轮: {len(sellers)} sellers, {len(to_enrich)} 新")

    # 批量获取新卖家详情（一次 actor 调用，替代逐个并发，省费用/时间）
    if to_enrich:
        try:
            details = await provider.fetch_seller_profiles(
                [s["seller_id"] for s in to_enrich], domain=marketplace
            )
        except Exception as e:
            details = []
            errors.append(f"batch seller profile: {e}")
        # actor 返回项的 sellerId 可能叫 sellerId / seller_id / id，按多键匹配
        detail_map: dict[str, dict] = {}
        for d in details:
            sid = d.get("sellerId") or d.get("seller_id") or d.get("id")
            if sid:
                detail_map[str(sid)] = d
        for s in to_enrich:
            detail = detail_map.get(s["seller_id"], {})
            s["detail"] = detail
            raw_addr = detail.get("businessAddress") or s["junglee_seller"].get("address")
            s["addr_str"] = ", ".join(raw_addr) if isinstance(raw_addr, list) else (raw_addr or "")
            s["business_country"] = parse_country(s["addr_str"])

    # upsert：只对差集（新卖家）插入，已入库的不覆盖；products 按 asin 去重写全量
    async with async_session() as db:
        for s in to_enrich:
            sid = s["seller_id"]
            detail = s.get("detail") or {}
            await db.execute(
                pg_insert(Seller)
                .values(
                    seller_id=sid,
                    name=detail.get("name") or s["junglee_seller"].get("name"),
                    category=category,
                    profile_url=detail.get("url"),
                    business_name=detail.get("businessName"),
                    business_address=s.get("addr_str"),
                    business_country=s.get("business_country"),
                    positive_rating_percent=detail.get("positiveRatingPercent"),
                    total_feedback=detail.get("feedbackCount"),
                    recent_feedback_30d=detail.get("recentFeedback30Days"),
                    member_since=detail.get("memberSince"),
                    response_time=detail.get("responseTime"),
                    marketplace=marketplace,
                )
                .on_conflict_do_update(
                    index_elements=[Seller.seller_id],
                    set_={
                        "business_country": s.get("business_country"),
                        "total_feedback": detail.get("feedbackCount"),
                        "category": category,
                    },
                )
            )
        written_ids = {s["seller_id"] for s in sellers}
        for p in products_raw:
            asin = p.get("asin")
            if not asin:
                continue
            ps = p.get("seller") or {}
            if ps.get("id") not in written_ids:
                continue
            price_obj = p.get("price")
            price_val = price_obj.get("value") if isinstance(price_obj, dict) else price_obj
            stars_obj = p.get("stars")
            stars_val = stars_obj.get("value") if isinstance(stars_obj, dict) else stars_obj
            await db.execute(
                pg_insert(Product)
                .values(
                    asin=asin,
                    title=p.get("title"),
                    url=p.get("url"),
                    brand=p.get("brand"),
                    price_value=price_val,
                    stars=stars_val,
                    reviews_count=p.get("reviewsCount"),
                    in_stock=p.get("inStock"),
                    seller_id=ps.get("id"),
                    marketplace=marketplace,
                    category=category,
                )
                .on_conflict_do_update(
                    index_elements=[Product.asin],
                    set_={
                        "price_value": price_val,
                        "reviews_count": p.get("reviewsCount"),
                        "in_stock": p.get("inStock"),
                        "fetched_at": func.now(),
                    },
                )
            )
        await db.commit()

    new_count = len(to_enrich)
    logger.info(f"[call_actors] 第 {round_no} 轮完成: 新入库 {new_count} 卖家, {len(products_raw)} 产品")
    if msg_id:
        await update_progress(
            msg_id, "call_actors",
            f"第 {round_no} 轮完成：新入库 {new_count} 个卖家", "done",
        )
    return {"fetch_round": round_no, "last_new_count": new_count, "_round_sellers": sellers, "errors": errors}


async def acquire_if_needed(state: V2State) -> dict:
    """判断节点：评估库存是否够配额（够 / 达 max_rounds / 上一轮 0 新增）。
    路由由其后条件边决定：够 → llm_analysis，不够 → call_actors 子 agent。"""
    target = state.get("target") or state.get("remaining") or 0
    sellers = state.get("sellers") or []
    fetch_round = state.get("fetch_round") or 0
    max_rounds = state.get("max_rounds") or 3
    last_new = state.get("last_new_count") or 0
    enough = (
        len(sellers) >= target
        or fetch_round >= max_rounds
        or (fetch_round > 0 and last_new == 0)
    )
    msg_id = state.get("assistant_msg_id")
    if msg_id:
        msg = "库存够，进入分析" if enough else f"库存不足（{len(sellers)}/{target}），触发采集"
        await update_progress(msg_id, "acquire_if_needed", msg, "done")
    logger.info(
        f"[acquire_if_needed] sellers={len(sellers)} target={target} "
        f"round={fetch_round} last_new={last_new} enough={enough}"
    )
    return {}


async def llm_analysis(state: V2State) -> dict:
    """DeepSeek 对 top 5 卖家生成货代视角画像（货量/国籍/活跃）。"""
    from app.agent.llm import get_llm

    msg_id = state.get("assistant_msg_id")
    if msg_id:
        await update_progress(msg_id, "llm_analysis", "正在生成卖家画像…", "running")

    sellers = (state.get("sellers") or [])[:5]
    if not sellers:
        return {}
    llm = get_llm()
    for s in sellers:
        detail = s.get("detail") or {}
        name = (s.get("junglee_seller") or {}).get("name") or s.get("seller_id")
        country = s.get("business_country") or "未知"
        feedback = detail.get("feedbackCount") or 0
        prompt = (
            f"用一句话（货代视角）描述亚马逊卖家「{name}」：feedback {feedback}，国籍 {country}。"
            "重点：货量潜力、是否中国卖家（近距离优势）、活跃度。限 80 字。"
        )
        try:
            resp = await llm.ainvoke(prompt)
            s["analysis"] = str(resp.content)[:200]
        except Exception:
            s["analysis"] = ""
    if msg_id:
        await update_progress(msg_id, "llm_analysis", "画像生成完成", "done")
    return {}


async def score_sellers(state: V2State) -> dict:
    """规则评分：feedback 体量(0-70) + 中国卖家加分(20) + base(10)。"""
    sellers = state.get("sellers", [])
    logger.info(f"[score_sellers] 评分 {len(sellers)} 个卖家")

    msg_id = state.get("assistant_msg_id")
    if msg_id:
        await update_progress(msg_id, "score_sellers", "正在评分排序…", "running")

    for s in sellers:
        feedback = (s.get("detail") or {}).get("feedbackCount") or 0
        score = min(70, feedback / 100)
        if s.get("business_country") == "China":
            score += 20
        score += 10
        s["seller_score"] = min(100, int(score))
    sellers.sort(key=lambda x: x.get("seller_score", 0), reverse=True)
    if msg_id:
        await update_progress(msg_id, "score_sellers", f"评分完成，top1={sellers[0].get('seller_score') if sellers else '?'}", "done")
    return {"scored_sellers": sellers}


async def lookup_contacts_for(sellers: list[dict]) -> int:
    """对一批 sellers 查天眼查+企查查联系方式（只 China + 跳过已有 contacts），写 sellers 表。
    返回查到联系方式的卖家数。acquire_if_needed 与 lookup_contacts 节点共用。"""
    from sqlalchemy import update as sa_update

    from app.core.db import async_session
    from app.models.seller import Seller
    from app.providers import qichacha, tianyancha

    found = 0
    for s in sellers:
        sid = s.get("seller_id")
        company = (s.get("detail") or {}).get("businessName") or (
            s.get("junglee_seller") or {}
        ).get("name")
        # 只查中国卖家（天眼查/企查查只对中国公司有效）
        if s.get("business_country") != "China" or not company or not sid:
            s.setdefault("contacts", [])
            continue
        if s.get("contacts"):
            continue  # 已有跳过
        ty = await tianyancha.lookup_company(company)
        qc = await qichacha.lookup_company(company)
        contacts = []
        for p in ty.get("phones", []):
            contacts.append({"source": "天眼查", "type": "phone", "value": p})
        for e in ty.get("emails", []):
            contacts.append({"source": "天眼查", "type": "email", "value": e})
        for p in qc.get("phones", []):
            contacts.append({"source": "企查查", "type": "phone", "value": p})
        for e in qc.get("emails", []):
            contacts.append({"source": "企查查", "type": "email", "value": e})
        s["contacts"] = contacts
        if contacts:
            found += 1
            async with async_session() as db:
                await db.execute(
                    sa_update(Seller)
                    .where(Seller.seller_id == sid)
                    .values(contacts=contacts)
                )
                await db.commit()
    return found


async def lookup_contacts(state: V2State) -> dict:
    """子 agent 内节点：对本轮新入库卖家查联系方式（call_actors → lookup_contacts）。"""
    round_sellers = state.get("_round_sellers") or []
    if not round_sellers:
        return {}
    msg_id = state.get("assistant_msg_id")
    china_n = sum(1 for s in round_sellers if s.get("business_country") == "China")
    if msg_id and china_n:
        await update_progress(msg_id, "lookup_contacts", f"查询 {china_n} 个中国卖家联系方式…", "running")
    found = await lookup_contacts_for(round_sellers)
    if msg_id:
        await update_progress(msg_id, "lookup_contacts", f"完成，{found} 个有联系方式", "done")
    return {}


async def output_result(state: V2State) -> dict:
    """LLM 流式生成回复（500ms batch flush 写 DB）+ sellers meta + query_log。"""
    import time
    import uuid

    from sqlalchemy import update

    from app.agent.llm import get_llm
    from app.core.db import async_session
    from app.memory.models import MemoryMessage
    from app.models.query_log import QueryLog

    msg_id = state.get("assistant_msg_id")
    # 信息不全（HITL）→ 写补充提示收尾，前端 SSE 看到 need_input 后重发补充 marketplace/category
    if state.get("need_human_confirm"):
        missing = state.get("missing") or ["目标市场", "产品品类"]
        if msg_id:
            await _write_msg(
                msg_id,
                content=f"请补充：{', '.join(missing)}",
                meta_patch={
                    "sellers": [],
                    "done": True,
                    "need_input": True,
                    "missing": missing,
                    "marketplace": state.get("marketplace"),
                    "category": state.get("category"),
                },
            )
            await update_progress(msg_id, "output_result", f"信息不全，等待补充：{', '.join(missing)}", "done")
        return {"final_result": {"need_input": True, "missing": missing}}
    # 配额耗尽 → 只写收尾 meta，跳过 LLM/grant（让 SSE 正常收尾、前端显示升级提示）
    if state.get("quota_exhausted"):
        if msg_id:
            await _write_msg(
                msg_id,
                content="您本月的卖家获取额度已用完，升级套餐可解锁更多额度。",
                meta_patch={
                    "sellers": [],
                    "done": True,
                    "quota_exhausted": True,
                    "upgrade_available": True,
                },
            )
            await update_progress(msg_id, "output_result", "额度已用完，提示升级", "done")
        return {"final_result": {"sellers": [], "count": 0, "quota_exhausted": True}}

    sellers = state.get("scored_sellers", [])
    user_id = state.get("user_id")
    quota_meta: dict = {}
    if user_id:
        # 配额发放：独立短事务（行锁防并发超发），不与 LLM/meta 写入重叠
        from app.quota import grant_sellers

        async with async_session() as db:
            display_ids, quota_meta = await grant_sellers(
                db, uuid.UUID(user_id), sellers
            )
        keep = set(display_ids)
        sellers = [s for s in sellers if s.get("seller_id") in keep]
    result = [
        {
            "seller_id": s.get("seller_id"),
            "name": (s.get("junglee_seller") or {}).get("name"),
            "business_country": s.get("business_country"),
            "total_feedback": (s.get("detail") or {}).get("feedbackCount"),
            "member_since": (s.get("detail") or {}).get("memberSince"),
            "seller_score": s.get("seller_score"),
            "product_count": len(s.get("products", [])),
            "analysis": s.get("analysis"),
            "contacts": s.get("contacts", []),
        }
        for s in sellers
    ]

    msg_id = state.get("assistant_msg_id")
    # 库存 + 采集仍不足目标数量 → 标记源耗尽（前端提示「该品类已全部展示」）
    target = state.get("target") or state.get("remaining") or 0
    if target and len(result) < target:
        quota_meta["source_exhausted"] = True
    if msg_id:
        await update_progress(msg_id, "output_result", f"正在生成最终分析，共 {len(result)} 个卖家…", "running")

    # LLM 流式生成回复 + 500ms batch flush 写 DB
    reply = ""
    if msg_id:
        top5 = [
            {
                "name": r["name"],
                "country": r["business_country"],
                "feedback": r["total_feedback"],
                "score": r["seller_score"],
            }
            for r in result[:5]
        ]
        prompt = (
            f"你是货代获客助手。查询品类「{state.get('category')}」市场「{state.get('marketplace')}」，"
            f"找到 {len(result)} 个卖家。Top5: {top5}。"
            "用简洁中文回复（结果概述 + 推荐理由），限 200 字。"
        )
        llm = get_llm()
        accumulated = ""
        buffer: list[str] = []
        last_flush = time.monotonic()
        async for chunk in llm.astream(prompt):
            text = chunk.content
            accumulated += text
            buffer.append(text)
            if time.monotonic() - last_flush > 0.5:
                async with async_session() as db:
                    await db.execute(
                        update(MemoryMessage)
                        .where(MemoryMessage.message_id == uuid.UUID(msg_id))
                        .values(content=accumulated)
                    )
                    await db.commit()
                buffer.clear()
                last_flush = time.monotonic()
        reply = accumulated

    # 最终 flush（content 完成 + meta 空 sellers, done=False；合并写保留 progress）
    if msg_id:
        await _write_msg(msg_id, content=reply, meta_patch={"sellers": [], "done": False})

        # 逐个 seller 写 meta（前端表格流式逐行渲染）
        import asyncio as _aio

        for i, _seller in enumerate(result):
            await _write_msg(msg_id, meta_patch={"sellers": result[: i + 1], "done": False})
            await _aio.sleep(0.3)

        # 最终 done
        await _write_msg(msg_id, meta_patch={"sellers": result, "done": True, **quota_meta})

    # query_log
    try:
        async with async_session() as db:
            db.add(
                QueryLog(
                    user_query=state.get("user_query"),
                    parsed_marketplace=state.get("marketplace"),
                    parsed_category=state.get("category"),
                    result_count=len(sellers),
                    cache_hit=state.get("cache_hit", False),
                )
            )
            await db.commit()
    except Exception:
        pass

    if msg_id:
        await update_progress(msg_id, "output_result", "分析完成", "done")

    return {"final_result": {"sellers": result, "count": len(result), "reply": reply, "quota": quota_meta}}


async def classify_intent(state: V2State) -> dict:
    """前置意图判定：获客(acquisition)→主 agent；其他(chat)→direct_llm 直接回复。"""
    from typing import Literal

    from pydantic import BaseModel, Field

    from app.agent.orchestrator import _structured_invoke

    class IntentClass(BaseModel):
        intent: Literal["acquisition", "chat"] = Field(default="acquisition")

    classify_prompt = """判断用户查询的意图类别：
- acquisition：找亚马逊卖家/获客相关（出现目标市场如“美国站/欧洲站”、品类、找卖家、FBA/FBM、联系方式、中国卖家、采集、评分等）
- chat：其他（问候、闲聊、问你是谁/能做什么、求助、与找卖家无关）
只返回 intent 字段（acquisition 或 chat）。"""

    msg_id = state.get("assistant_msg_id")
    if msg_id:
        await update_progress(msg_id, "classify_intent", "判断意图…", "running")
    result = await _structured_invoke(
        IntentClass,
        [
            {"role": "system", "content": classify_prompt},
            {"role": "user", "content": state.get("user_query", "")},
        ],
        "classify_intent",
    )
    intent = result.intent if result else "acquisition"  # 解析失败默认走获客主流程
    if msg_id:
        await update_progress(msg_id, "classify_intent", f"意图：{'获客' if intent == 'acquisition' else '咨询'}", "done")
    return {"intent": intent}


# 非获客意图直接回复的系统提示词：设定身份/功能/解决问题
DIRECT_SYSTEM_PROMPT = """你是「获客 Agent」，面向国际货代行业的 AI 亚马逊卖家获客助手。

【你是谁】
帮货代/销售团队找亚马逊卖家的 AI 助手。

【有什么功能】
按「目标市场 + 品类」找潜在亚马逊卖家，提供卖家国籍、AI 评分、联系方式（电话/邮箱），自动识别中国卖家并智能排序。

【解决什么问题】
传统获客靠手动翻 Amazon、逐个搜联系方式，找一个客户要 30 分钟；你让用户一句话锁定目标卖家，把机械搜索交给 AI，销售专注谈单。

【回复原则】
- 获客类需求：引导用户输入「目标市场 + 品类」，如「美国站卖户外家具的中国卖家」。
- 其他问题：简短友好作答，并自然引导回获客功能。
- 回复限 150 字以内。"""


async def direct_llm(state: V2State) -> dict:
    """非获客意图：LLM 按系统提示词直接作答，流式写 content 后收尾，不查库/不采集。"""
    import time
    import uuid as _uuid

    from sqlalchemy import update

    from app.agent.llm import get_llm
    from app.core.db import async_session
    from app.memory.models import MemoryMessage

    msg_id = state.get("assistant_msg_id")
    if msg_id:
        await update_progress(msg_id, "direct_llm", "正在回复…", "running")

    reply = ""
    if msg_id:
        llm = get_llm()
        accumulated = ""
        last_flush = time.monotonic()
        async for chunk in llm.astream(
            [
                {"role": "system", "content": DIRECT_SYSTEM_PROMPT},
                {"role": "user", "content": state.get("user_query", "")},
            ]
        ):
            accumulated += chunk.content
            if time.monotonic() - last_flush > 0.5:
                async with async_session() as db:
                    await db.execute(
                        update(MemoryMessage)
                        .where(MemoryMessage.message_id == _uuid.UUID(msg_id))
                        .values(content=accumulated)
                    )
                    await db.commit()
                last_flush = time.monotonic()
        reply = accumulated
        await _write_msg(msg_id, content=reply, meta_patch={"done": True})
    if msg_id:
        await update_progress(msg_id, "direct_llm", "已回复", "done")
    return {"final_result": {"reply": reply, "direct": True}}
