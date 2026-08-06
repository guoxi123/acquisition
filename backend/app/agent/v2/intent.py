"""意图解析节点：DeepSeek 从自然语言提取 marketplace/category/shipping/service_mode。"""

from pydantic import BaseModel, Field

from app.agent.v2.state import V2State
from app.utils.logger import logger


class ParsedIntent(BaseModel):
    marketplace: str | None = Field(
        default=None, description="目标市场域名 amazon.com/.co.uk/.de，从'美国站/US'推断；无法判断为 None"
    )
    category: str | None = Field(
        default=None, description="产品品类英文，如 outdoor furniture；无法判断为 None"
    )
    shipping_type: str | None = Field(
        default=None, description="sea/air；未提及为 None"
    )
    service_mode: str | None = Field(
        default=None, description="FBA/FBM；未提及为 None"
    )
    need_confirm: bool = Field(
        default=False, description="marketplace 或 category 缺失则为 True"
    )
    missing: list[str] = Field(
        default_factory=list, description="缺失必要字段的中文名"
    )


INTENT_PROMPT = """你是货代获客的意图解析助手。从用户自然语言查询中提取：
- marketplace：目标市场（"美国站/US/美国"→amazon.com，"英国站/UK"→amazon.co.uk，"德国站/DE"→amazon.de）
- category：产品品类英文（如"户外家具"→outdoor furniture）
- shipping_type：sea/air（海运/空运），未提及 None
- service_mode：FBA/FBM，未提及 None
marketplace 和 category 是必要字段，任一缺失 need_confirm=True，并在 missing 列出缺失项中文名（如"目标市场""产品品类"）。
shipping_type/service_mode 缺失不算 need_confirm。"""


async def parse_intent(state: V2State) -> dict:
    from app.agent.orchestrator import _structured_invoke
    from app.agent.v2.nodes import update_progress

    msg_id = state.get("assistant_msg_id")
    if msg_id:
        await update_progress(msg_id, "parse_intent", "正在解析意图…", "running")

    parsed = await _structured_invoke(
        ParsedIntent,
        [
            {"role": "system", "content": INTENT_PROMPT},
            {"role": "user", "content": state.get("user_query", "")},
        ],
        "parse_intent",
    )
    if parsed is None:
        logger.warning(f"[parse_intent] 解析失败: query={state.get('user_query', '')[:50]}")
        if msg_id:
            await update_progress(msg_id, "parse_intent", "意图解析失败", "error")
        return {
            "need_human_confirm": True,
            "human_approved": False,
            "missing": ["无法解析查询，请明确目标市场和品类"],
            "errors": ["intent parse failed"],
        }
    if msg_id:
        await update_progress(msg_id, "parse_intent", f"解析完成：{parsed.marketplace or '未知市场'} / {parsed.category or '未知品类'}", "done")
    return {
        "marketplace": parsed.marketplace,
        "category": parsed.category,
        "shipping_type": parsed.shipping_type,
        "service_mode": parsed.service_mode,
        "need_human_confirm": parsed.need_confirm,
        "missing": parsed.missing,
        "human_approved": not parsed.need_confirm,
    }