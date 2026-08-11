"""企查查 API provider：按公司名查联系方式（phone/email）。

需配 QICHACHA_APP_KEY + QICHACHA_SECRET_KEY。
认证方式：Token = MD5(key + timespan + secretKey).toUpperCase()，放 Header。
API 文档：https://openapi.qcc.com/dataApi/2001
无 key/secret 时返回空（不报错）。
"""

import hashlib
import time

import httpx

from app.core.config import settings
from app.core.retry import with_retry


async def lookup_company(name: str) -> dict:
    """返回 {phones: [...], emails: [...]}。"""
    if not settings.qichacha_app_key or not settings.qichacha_secret_key:
        return {"phones": [], "emails": []}
    try:
        timespan = str(int(time.time()))
        token = hashlib.md5(
            f"{settings.qichacha_app_key}{timespan}{settings.qichacha_secret_key}".encode()
        ).hexdigest().upper()

        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await with_retry(lambda: client.get(
                "https://api.qichacha.com/ECIV4/GetBasicDetailsByName",
                params={
                    "key": settings.qichacha_app_key,
                    "keyword": name,
                },
                headers={
                    "Token": token,
                    "Timespan": timespan,
                    "Accept": "application/json",
                },
            ), label="qichacha")
            if "application/json" not in resp.headers.get("content-type", ""):
                print(f"[qichacha] 非JSON响应 name={name} status={resp.status_code} content-type={resp.headers.get('content-type')}")
                print(f"[qichacha] body: {resp.text[:2000]}")
                return {"phones": [], "emails": []}

            data = resp.json()
            if data.get("Status") != "200":
                print(f"[qichacha] API错误 name={name} status={data.get('Status')} message={data.get('Message')}")
                return {"phones": [], "emails": []}

            result = data.get("Result", {}) or {}
            phones = [result.get("Telephone")] if result.get("Telephone") else []
            emails = [result.get("Email")] if result.get("Email") else []
            return {
                "phones": [p for p in phones if p],
                "emails": [e for e in emails if e],
            }
    except Exception as e:
        print(f"[qichacha] 请求异常 name={name} error={e}")
        return {"phones": [], "emails": []}
