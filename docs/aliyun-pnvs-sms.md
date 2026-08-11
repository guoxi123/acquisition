# 阿里云号码认证(PNVS)短信:个人开发者免资质发验证码

> 注册功能要发短信验证码,但个人开发者没有企业营业执照。阿里云的"短信服务 SMS"要企业资质才能申请签名,这条路堵死。本文讲另一条路:号码认证服务(PNVS)里的"短信认证",个人实名就能用,免申请签名/模板,系统赠送。配上 HMAC-SHA1 手写签名,连 SDK 都不用装。

## 一、问题:个人开发者发不了短信?

项目要加手机号注册,需要短信验证码。阿里云发短信,第一反应是"短信服务(SMS, dysmsapi)"。但 SMS 申请签名,个人资质审核很严(基本要营业执照),个人开发者走不通。

搜索后发现阿里云还有个产品叫**号码认证服务(PNVS, dypnsapi)**,里面有个"短信认证"子功能,**个人实名认证用户就能直接调 API 发验证码,免申请签名和模板**——平台提供系统赠送的签名 + 标准验证码模板。

## 二、PNVS 短信认证 vs SMS 短信服务

| 维度 | 短信服务 SMS | 号码认证 PNVS(短信认证) |
|---|---|---|
| 个人资质 | 签名申请难(基本要企业) | **个人实名即可** |
| 签名/模板 | 自行申请审核 | **系统赠送,免申请** |
| 接口 | SendSms | SendSmsVerifyCode |
| 适用 | 企业批量营销/通知 | 个人/小项目验证码 |

对个人开发者的注册验证码场景,PNVS 短信认证是更务实的选择。

## 三、SendSmsVerifyCode 接口

核心接口 `SendSmsVerifyCode`,必填三个参数:
- `PhoneNumber`:手机号
- `SignName`:签名名(控制台获取的**系统赠送签名**,不能自定义)
- `TemplateCode`:模板 CODE(系统赠送的验证码模板)

还有一个 `VerifyCode`:**后端生成的验证码**,填入系统模板的 `${code}` 占位符(系统模板内容类似"您的验证码是 ${code}")。

注意:用自定义 VerifyCode 时,阿里云的 `CheckSmsVerifyCode` 接口不支持校验(它只校验系统自动生成的码)。所以**验证码的存储 + 校验要自己实现**(后端存表,注册时比对)。

## 四、接入:HMAC-SHA1 手写签名,免 SDK

官方示例用 SDK(`alibabacloud_dypnsapi20170525`),但要装一堆依赖。其实 PNVS 是阿里云 RPC V1 协议,签名算法是固定的 HMAC-SHA1,**手写几十行就能调,不用装 SDK**。

```python
import base64, hashlib, hmac, secrets, urllib.parse
from datetime import datetime, timezone

def _percent_encode(value):
    """阿里云 RPC 签名的 RFC3986 编码(斜杠也转义)。"""
    return urllib.parse.quote(str(value), safe="")

def _sign(params: dict, access_key_secret: str) -> str:
    """RPC V1 签名:排序→编码→拼接→HMAC-SHA1→Base64。"""
    sorted_items = sorted(params.items())
    canonicalized = "&".join(f"{_percent_encode(k)}={_percent_encode(v)}" for k, v in sorted_items)
    string_to_sign = "GET&" + _percent_encode("/") + "&" + _percent_encode(canonicalized)
    digest = hmac.new((access_key_secret + "&").encode(), string_to_sign.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()
```

关键点:`string_to_sign` 固定用 `"GET&"` 前缀——**即使你用 POST 发请求,RPC V1 的签名算法里 string_to_sign 还是要用 GET**。这是阿里云 RPC 的历史设计,踩过一次(用 POST& 签名,签名校验不过)。

组装请求:

```python
async def send_sms_code(phone: str, code: str) -> dict:
    params = {
        "AccessKeyId": settings.aliyun_sms_access_key_id,
        "Format": "JSON", "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": secrets.token_hex(16),
        "SignatureVersion": "1.0",
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "Version": "2017-05-25", "Action": "SendSmsVerifyCode",
        "PhoneNumber": phone, "SignName": settings.aliyun_sms_sign_name,
        "TemplateCode": settings.aliyun_sms_template_code,
        "VerifyCode": code,
    }
    params["Signature"] = _sign(params, settings.aliyun_sms_access_key_secret)
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post("https://dypnsapi.aliyuncs.com", data=params)
    data = resp.json()
    return {"ok": str(data.get("Code")) == "OK", ...}
```

注意 endpoint 是 `dypnsapi.aliyuncs.com`(号码认证服务),**不是** `dysmsapi.aliyuncs.com`(短信服务)。两个产品,两个域名。

## 五、mock 降级:凭证没配时不报错

开发本地经常没配阿里云凭证(或不想真发短信花钱)。加个 mock 降级:凭证没配全 → 不调 API,直接日志打印验证码,返回 `mock: true`:

```python
if not settings.aliyun_sms_sign_name:   # 赠送签名没配 → mock
    logger.info(f"[sms mock] phone={phone} 验证码={code}（未配置完整的阿里云号码认证凭证）")
    return {"ok": True, "mock": True, "code": code}
```

这样本地开发注册流程能跑通(验证码从日志看),生产配上真凭证自动切真发。前端也可以根据 `mock` 字段提示"开发模式:验证码见后端日志"。

## 六、配套:验证码存储 + 校验 + 防刷

PNVS 只管"发",校验要自己做:

- **存储**:`sms_codes` 表(phone, code, expires_at, used),5 分钟 TTL
- **发码防刷**:同一手机号 60s 内不能重发(查最新一条 created_at)
- **注册校验**:注册时查有效的、未用过的、未过期的码,匹配则 mark used + 建用户

```python
# 发码:60s 防刷
latest = 查该手机号最新一条 sms_code
if latest and (now - latest.created_at) < 60s:
    raise HTTPException(429, "验证码发送过于频繁,请 60 秒后再试")

# 注册:校验码
record = 查 phone+code 且 used=False 且 未过期
if record is None:
    raise HTTPException(400, "验证码错误或已过期")
record.used = True
# 建用户...
```

## 七、总结

个人开发者发短信验证码,别死磕"短信服务 SMS"(要企业资质)。号码认证服务 PNVS 的"短信认证"是更友好的路:个人实名即可、系统赠送签名模板、免审核。

技术要点:
- 接口 `SendSmsVerifyCode`,必填 phone/SignName(赠送)/TemplateCode(赠送)/VerifyCode(自生成)
- 签名 RPC V1 HMAC-SHA1,手写免 SDK(string_to_sign 用 GET& 前缀)
- 用自定义 VerifyCode → 校验自己做(sms_codes 表 + 5min TTL)
- 凭证没配 → mock 降级(日志打印码),开发生产两不误

> 没企业资质不代表发不了短信。阿里云针对个人开发者留了 PNVS 这扇门,配上系统赠送签名和手写签名,几十行代码就能跑通注册验证码。比折腾 SMS 资质省心得多。
