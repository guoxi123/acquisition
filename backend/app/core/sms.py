"""阿里云号码认证服务（PNVS）短信验证码：httpx 调 SendSmsVerifyCode（dypnsapi）。

个人开发者免企业资质/自建签名/模板（用系统赠送签名 + 系统模板）。
RPC V1 + HMAC-SHA1 签名，免装 SDK。凭证未配置时走 mock（logger 打印验证码），便于本地开发。
"""

import base64
import hashlib
import hmac
import json
import secrets
import urllib.parse
from datetime import datetime, timezone

import httpx

from app.core.config import settings
from app.utils.logger import logger

_ENDPOINT = "https://dypnsapi.aliyuncs.com"  # 号码认证服务（区别于短信服务 dysmsapi）


def _percent_encode(value) -> str:
    """阿里云 RPC 签名用的 RFC3986 编码（特殊字符全转义，斜杠也转）。"""
    return urllib.parse.quote(str(value), safe="")


def _sign(params: dict, access_key_secret: str) -> str:
    """阿里云 RPC V1 签名：排序→编码→拼接→HMAC-SHA1→Base64。

    string_to_sign 固定用 "GET&" 前缀（即使实际用 POST 发请求，RPC V1 签名算法如此）。
    """
    sorted_items = sorted(params.items())
    canonicalized = "&".join(
        f"{_percent_encode(k)}={_percent_encode(v)}" for k, v in sorted_items
    )
    string_to_sign = "POST&" + _percent_encode("/") + "&" + _percent_encode(canonicalized)
    digest = hmac.new(
        (access_key_secret + "&").encode(),
        string_to_sign.encode(),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode()


async def send_sms_code(phone: str, code: str) -> dict:
    """用号码认证服务 SendSmsVerifyCode 发验证码（VerifyCode=后端生成的码，填入系统模板 ${code}）。

    返回 {ok, mock?, code?, error?}。凭证未配置走 mock。
    """
    if (
        not settings.aliyun_sms_access_key_id
        or not settings.aliyun_sms_access_key_secret
        or not settings.aliyun_sms_sign_name
        or not settings.aliyun_sms_template_code
    ):
        logger.info(f"[sms mock] phone={phone} 验证码={code}（未配置完整的阿里云号码认证凭证）")
        return {"ok": True, "mock": True, "code": code}

    params = {
        "AccessKeyId": settings.aliyun_sms_access_key_id,
        "Format": "JSON",
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": secrets.token_hex(16),
        "SignatureVersion": "1.0",
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "Version": "2017-05-25",
        "Action": "SendSmsVerifyCode",
        "PhoneNumber": phone,
        "SignName": settings.aliyun_sms_sign_name,
        "TemplateCode": settings.aliyun_sms_template_code,
        "TemplateParam": json.dumps({"code": code, "min": "5"}, ensure_ascii=False),
        "VerifyCode": code,
    }
    params["Signature"] = _sign(params, settings.aliyun_sms_access_key_secret)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(_ENDPOINT, data=params)
            data = resp.json()
    except Exception as e:
        logger.error(f"[sms] 请求异常: phone={phone} error={e}")
        return {"ok": False, "error": f"短信服务异常: {e}"}

    if str(data.get("Code")) != "OK":
        logger.error(f"[sms] 发送失败: phone={phone} resp={data}")
        return {"ok": False, "error": data.get("Message", "短信发送失败")}
    logger.info(f"[sms] 发送成功: phone={phone}")
    return {"ok": True}
