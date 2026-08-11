"""意图解析节点：DeepSeek 从自然语言提取 marketplace/category/shipping/service_mode。"""

from pydantic import BaseModel, Field

from app.agent.v2.state import V2State
from app.utils.logger import logger


class ParsedIntent(BaseModel):
    marketplace: str | None = Field(
        default=None,
        description="目标市场域名：美国站/US→amazon.com，欧洲站/欧洲→amazon.co.uk，英国站/UK→amazon.co.uk，德国站/DE→amazon.de，日本站/JP→amazon.co.jp；无站点词为 None",
    )
    category: str | None = Field(
        default=None,
        description="核心产品品类英文（去掉句首站点词与'卖…卖家'句式后剩余的产品词），如 cups/outdoor furniture；无法判断为 None",
    )
    shipping_type: str | None = Field(
        default=None, description="sea/air；未提及为 None"
    )
    service_mode: str | None = Field(
        default=None, description="FBA/FBM；未提及为 None"
    )
    business_country: str | None = Field(
        default=None,
        description="卖家国籍意图：China/US/DE；从'中国卖家/美国卖家'推断，未提及 None",
    )
    min_total_feedback: int | None = Field(
        default=None,
        description="规模下限：从'大卖家/大规模/货量多'推断 feedback 数下限（大≈1000，中≈300），未提及 None",
    )
    min_seller_score: int | None = Field(
        default=None,
        description="评分下限（0-100）：从'优质/评分高/好评多'推断，未提及 None",
    )
    desired_count: int | None = Field(
        default=None,
        description="用户想获取的卖家数量，从'N个/N家/N个卖家'（如'找10个''20家'）推断；未提及 None",
    )
    need_confirm: bool = Field(
        default=False, description="marketplace 或 category 缺失则为 True"
    )
    missing: list[str] = Field(
        default_factory=list, description="缺失必要字段的中文名"
    )


INTENT_PROMPT = """你是货代获客的意图解析助手。从用户自然语言查询中按以下两步提取：
第一步——识别 marketplace：句首的站点词（美国站/US→amazon.com，欧洲站/欧洲→amazon.co.uk，英国站/UK→amazon.co.uk，德国站/DE→amazon.de，日本站/JP→amazon.co.jp）。没有站点词则 marketplace=None。
第二步——提取 category：把句子去掉站点词和“卖…的卖家/有哪些/找一下”等句式后，剩余的核心产品词翻译成英文（如“杯子”→cups，“户外家具”→outdoor furniture，“咖啡机”→coffee machine）。无法判断则 category=None。

其他字段：
- shipping_type：sea/air（海运/空运），未提及 None
- service_mode：FBA/FBM，未提及 None
- business_country：卖家国籍意图（“中国卖家”→China，“美国卖家”→US，“德国卖家”→DE），未提及 None
- min_total_feedback：规模下限，从“大卖家/大规模/货量多/活跃”推断 feedback 数下限（大卖家≈1000，中卖家≈300），未提及 None
- min_seller_score：评分下限（0-100），从“优质/评分高/好评多”推断，未提及 None
- desired_count：用户想获取的卖家数量，从“N个/N家/N个卖家”（如“找10个”“20家”）推断；未提及 None

示例：
- “欧洲站卖杯子卖家” → marketplace=amazon.co.uk, category=cups
- “美国站户外家具的中国大卖家” → marketplace=amazon.com, category=outdoor furniture, business_country=China, min_total_feedback=1000
- “美国站找10个卖杯子的卖家” → marketplace=amazon.com, category=cups, desired_count=10
- “德国站卖咖啡机的卖家” → marketplace=amazon.de, category=coffee machine
- “卖杯子” → marketplace=None, category=cups（缺市场，need_confirm）

marketplace 和 category 是必要字段，任一为 None 则 need_confirm=True，并在 missing 列出缺失项中文名（“目标市场”“产品品类”）。
若用户省略了 marketplace/category，但【历史对话/摘要里明确提过】（如之前一直查美国站），可从历史推断补全、不标 need_confirm。
注意：当前输入明确指定的字段优先于历史（用户这次说“英国站”就尊重当前，别从历史覆盖）。其余字段缺失不算 need_confirm。"""


async def parse_intent(state: V2State) -> dict:
    from app.agent.orchestrator import _structured_invoke
    from app.agent.v2.nodes import update_progress

    msg_id = state.get("assistant_msg_id")
    if msg_id:
        await update_progress(msg_id, "parse_intent", "正在解析意图…", "running")

    # 结合会话历史：续问时从上下文推断省略的字段（如之前的市场/品类），减少不必要的 HITL 追问
    from app.memory import agent as memory_agent

    sys_content = INTENT_PROMPT
    history_msgs: list = []
    if state.get("session_id"):
        ctx = await memory_agent.get_context(state["session_id"])
        if ctx["summaries"]:
            sys_content += "\n\n【之前对话摘要】\n" + "\n\n".join(s["content"] for s in ctx["summaries"])
        for m in ctx["recent_messages"]:
            if m["role"] == "user":
                history_msgs.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant" and m["content"]:  # 过滤空占位
                history_msgs.append({"role": "assistant", "content": m["content"]})
    # history 末条含当前 user_query；无历史（session_id 缺失）则补一条
    base_msgs = [{"role": "system", "content": sys_content}] + history_msgs
    if not history_msgs:
        base_msgs.append({"role": "user", "content": state.get("user_query", "")})

    parsed = await _structured_invoke(ParsedIntent, base_msgs, "parse_intent")

    # 补充重跑时 endpoint 注入的 marketplace/category 优先（用户已确认）
    injected_marketplace = state.get("marketplace")
    injected_category = state.get("category")

    if parsed is None:
        logger.warning(f"[parse_intent] 解析失败: query={state.get('user_query', '')[:50]}")
        # LLM 失败但已注入完整值（补充重跑）→ 照常继续；否则 HITL
        if injected_marketplace and injected_category:
            if msg_id:
                await update_progress(msg_id, "parse_intent", f"使用补充值继续：{injected_marketplace} / {injected_category}", "done")
            return {
                "marketplace": injected_marketplace,
                "category": injected_category,
                "need_human_confirm": False,
                "human_approved": True,
            }
        if msg_id:
            await update_progress(msg_id, "parse_intent", "意图解析失败", "error")
        return {
            "need_human_confirm": True,
            "human_approved": False,
            "missing": ["无法解析查询，请明确目标市场和品类"],
            "errors": ["intent parse failed"],
        }

    # 注入值覆盖 marketplace/category；其余取 LLM 结果（保留 business_country 等可选筛选）
    marketplace = injected_marketplace or parsed.marketplace
    category = injected_category or parsed.category
    need_confirm = parsed.need_confirm and not (marketplace and category)

    # 目标数量 = min(用户输入, 剩余配额)；未指定数量则取剩余配额（默认拿满额度）
    remaining = state.get("remaining") or 0
    desired = parsed.desired_count
    target = min(desired, remaining) if desired else remaining

    if msg_id:
        await update_progress(msg_id, "parse_intent", f"解析完成：{marketplace or '未知市场'} / {category or '未知品类'} / 目标 {target} 个", "done")
    return {
        "marketplace": marketplace,
        "category": category,
        "shipping_type": parsed.shipping_type,
        "service_mode": parsed.service_mode,
        "business_country": parsed.business_country,
        "min_total_feedback": parsed.min_total_feedback,
        "min_seller_score": parsed.min_seller_score,
        "desired_count": desired,
        "target": target,
        "need_human_confirm": need_confirm,
        "missing": parsed.missing,
        "human_approved": not need_confirm,
    }