"""官网补联系方式：品牌名 → 搜官网 → 抓 about/contact → 提取 email/phone。

不依赖 Apify（httpx + DuckDuckGo HTML 搜索），补 amazon 不公开的邮箱/电话。
"""

import re
from urllib.parse import unquote, urlparse

import httpx

from app.core.retry import with_retry

# TLD 必须 ≥2 位字母，排除 core@2.5.3 这类版本号
EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(r"\+?\d[\d\s\-().]{7,}\d")
UDDG_RE = re.compile(r"uddg=([^&\"]+)")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

# 搜索结果排除（非卖家官网）
_EXCLUDE = (
    "amazon.", "facebook.", "instagram.", "twitter.", "x.com", "youtube.",
    "duckduckgo", "wikipedia.", "linkedin.", "pinterest.", "reddit.",
    "alibaba.", "aliexpress.", "ebay.", "walmart.", "target.",
)

# email 噪音（占位 / 技术库 / 监控平台，非真实商务邮箱）
_EMAIL_NOISE = (
    "example", "sentry", "cloudflare", "wixpress", "googlemail",
    "domain.", "your@", "noreply", "no-reply",
    "abc@", "xyz@", "@xyz.com", "@example",
    "core@", "lib@", ".js", "sample", "demo", "test@",
    "foo@", "bar@", "sentry.io", "png", "jpg", "webp",
    "schema", "webpack", "bootstrap", "jquery", "react",
)


async def find_website(brand: str) -> str | None:
    """DuckDuckGo 搜品牌官网，返回第一个非电商/社交的结果 URL。"""
    q = f"{brand} official website"
    async with httpx.AsyncClient(headers=HEADERS, timeout=15, follow_redirects=True) as client:
        try:
            r = await with_retry(
                lambda: client.get("https://html.duckduckgo.com/html/", params={"q": q}),
                label="ddg_search",
            )
        except Exception:
            return None
    for m in UDDG_RE.findall(r.text):
        url = unquote(m)
        if any(x in url for x in _EXCLUDE):
            continue
        return url
    return None


async def _extract_from(client: httpx.AsyncClient, url: str) -> tuple[set[str], set[str]]:
    emails: set[str] = set()
    phones: set[str] = set()
    try:
        r = await with_retry(lambda: client.get(url), label="website_extract")
    except Exception:
        return emails, phones
    for e in EMAIL_RE.findall(r.text):
        el = e.lower()
        if not any(n in el for n in _EMAIL_NOISE):
            emails.add(el)
    for p in PHONE_RE.findall(r.text):
        digits = re.sub(r"\D", "", p)  # 过滤版本号等短串
        if len(digits) >= 10:
            phones.add(p.strip())
    return emails, phones


async def extract_contacts(url: str) -> dict:
    """抓官网首页 + about/contact 页，提取 email/phone（官网域名 email 优先）。"""
    base = url.rstrip("/")
    official_domain = urlparse(url).netloc.replace("www.", "")
    emails: set[str] = set()
    phones: set[str] = set()
    async with httpx.AsyncClient(headers=HEADERS, timeout=10, follow_redirects=True) as client:
        for path in ("/", "/about", "/about-us", "/contact", "/contact-us", "/pages/contact-us"):
            e, p = await _extract_from(client, base + path)
            emails |= e
            phones |= p
    official = sorted([e for e in emails if official_domain in e])
    others = sorted([e for e in emails if official_domain not in e])
    return {"emails": (official + others)[:3], "phones": sorted(phones)[:3]}


async def fetch_website_contacts(brand: str) -> dict:
    """品牌名 → 官网 email/phone。返回 {website, emails, phones}。"""
    url = await find_website(brand)
    if not url:
        return {"website": None, "emails": [], "phones": []}
    result = await extract_contacts(url)
    return {"website": url, **result}
