"""天眼查 API provider：按公司名查联系方式（phone/email）。

需配 TIANYANCHA_API_TOKEN。API 端点/响应字段按官方文档校准：
https://open.tianyancha.com/
无 token 时返回空（不报错）。
"""

import httpx

from app.core.config import settings
from app.core.retry import with_retry


async def lookup_company(name: str) -> dict:
    """返回 {phones: [...], emails: [...]}。"""
    if not settings.tianyancha_api_token:
        return {"phones": [], "emails": []}
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await with_retry(lambda: client.get(
                "https://open.api.tianyancha.com/services/open/ic/baseinfoV2/2.0",
                params={"keyword": name},
                headers={
                    "Authorization": settings.tianyancha_api_token,
                    "User-Agent": "Mozilla/5.0 (compatible; TianYanCha-OpenAPI/1.0)",
                    "Accept": "application/json",
                },
            ), label="tianyancha")
            if "application/json" not in resp.headers.get("content-type", ""):
                print(f"[tianyancha] 非JSON响应 name={name} status={resp.status_code} content-type={resp.headers.get('content-type')}")
                print(f"[tianyancha] body: {resp.text[:2000]}")
                return {"phones": [], "emails": []}
            data = resp.json()

            if data.get("error_code") != 0:
                print(f"[tianyancha] API错误 name={name} error_code={data.get('error_code')} reason={data.get('reason')}")
                return {"phones": [], "emails": []}

            result = data.get("result", {}) or {}
            phones = [result.get("phoneNumber")] if result.get("phoneNumber") else []
            emails = [result.get("email")] if result.get("email") else []
            return {
                "phones": [p for p in phones if p],
                "emails": [e for e in emails if e],
            }
    except Exception as e:
        return {"phones": [], "emails": []}
