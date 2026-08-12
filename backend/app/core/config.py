from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置：优先环境变量，其次 backend/.env。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 数据库 / 队列
    database_url: str = "postgresql+asyncpg://acquisition:acquisition_dev@localhost:5433/acquisition"
    redis_url: str = "redis://localhost:6379/0"

    # 外部服务凭证
    apify_api_token: str = ""

    # LLM（OpenAI 兼容接入，默认 DeepSeek；换别的 LLM 只改 .env）
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"

    # 应用
    app_env: str = "development"
    cors_origins: str = "http://localhost:3000"

    # JWT 认证
    jwt_secret: str = "dev-secret-change-me"
    jwt_expire_minutes: int = 1440  # 24h

    # 工商查询（天眼查/企查查 API，按官方文档校准端点）
    tianyancha_api_token: str = "fbec9b5c-aedb-43e6-b721-7a5fb95c72e7"
    qichacha_app_key: str = "MTfQV4LkSCKCNX4b6kVx6GeZQf9GdNQbYPF5bX80kNoibT19"
    qichacha_secret_key: str = ""  # 企查查 SecretKey，需到开放平台控制台获取

    # Apify 数据缓存 TTL（小时）；命中免调 actor，省费用
    cache_ttl_junglee_hours: int = 168  # 7 天
    cache_ttl_seller_hours: int = 336  # 14 天
    cache_ttl_products_hours: int = 168  # 7 天
    # enrich 预筛：junglee 产品 reviewsCount 总和低于此值则跳过 enrich（省 amazon-sellers/products 调用）
    enrich_min_reviews: int = 100

    # V2 采集循环参数
    fetch_batch_size: int = 50 # 每轮抓取产品数（harness 循环累积填满配额）
    acquired_admin_max: int = 50  # 超管每次查询返回的卖家数（超管不限额度，走相同流程）
    max_fetch_rounds: int = 3    # 库不够时采集最大轮次

    # 阿里云短信（注册验证码）；凭证走 backend/.env，勿提交真实值。未配置则走 mock（日志打印码）
    aliyun_sms_access_key_id: str = ""
    aliyun_sms_access_key_secret: str = ""
    aliyun_sms_sign_name: str = ""
    aliyun_sms_template_code: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
