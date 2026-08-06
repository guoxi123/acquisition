"""V2 Agent 状态（自然语言 + HITL + 三源数据）。"""

from typing import TypedDict


class V2State(TypedDict):
    # 用户输入与解析
    user_query: str
    marketplace: str | None  # amazon.com / .co.uk ...
    category: str | None
    shipping_type: str | None  # sea / air
    service_mode: str | None  # FBA / FBM
    business_country: str | None  # 卖家国籍意图（China/US...），query_db 过滤用
    min_total_feedback: int | None  # 规模下限（feedback 数），query_db 过滤用
    min_seller_score: int | None  # 评分下限（0-100），query_db 过滤用
    # 流程控制
    need_human_confirm: bool
    missing: list[str]
    human_approved: bool
    cache_hit: bool
    # 数据
    products: list[dict]
    sellers: list[dict]
    scored_sellers: list[dict]
    assistant_msg_id: str  # 流式回复的占位消息 ID（output_result 边生成边 UPDATE content）
    final_result: dict
    errors: list[str]
    user_id: str | None  # 配额按用户记账（chat / chat_stream 注入，output_result 用）
    # 配额前置检查 + 采集循环
    remaining: int | None  # 本月剩余配额（check_quota 算，query_db LIMIT 用）
    quota_exhausted: bool  # 配额耗尽（check_quota 设，output_result 早分支用）
    fetch_round: int  # 采集轮次计数
    last_new_count: int  # 上一轮真正新增 seller 数（0→源耗尽，早停用）
    max_rounds: int  # 采集最大轮次
    _round_sellers: list[dict]  # call_actors → lookup_contacts 传递本轮新入库卖家（子 agent 内）
