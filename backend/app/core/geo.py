"""地址 → 国家解析（识别中国卖家，货代核心客户）。

amazon 卖家的 business_address 形如 "410 Terry Ave, Seattle, WA, US" 或 "深圳市南山区..."。
末尾常是国家；中国地址常含中文城市或 China/CN。
"""

_CHINA_KW = (
    "china", "cn", ", cn", "prc",
    "深圳", "广州", "厦门", "义乌", "杭州", "上海", "北京", "宁波", "东莞", "佛山",
    "shenzhen", "guangzhou", "yiwu", "ningbo", "dongguan",
)

_COUNTRY_MAP = {
    "US": ("united states", "usa", ", us", "u.s.", "u.s.a."),
    "UK": ("united kingdom", "uk", "england"),
    "Germany": ("germany", "deutschland"),
    "Japan": ("japan", "日本"),
    "Canada": ("canada",),
    "France": ("france",),
    "Italy": ("italy", "italia"),
}


def parse_country(address) -> str | None:
    """从 business_address 解析国家。优先识别中国（货代核心客户）。"""
    if not address:
        return None
    addr = (address if isinstance(address, str) else str(address)).lower()
    if any(kw in addr for kw in _CHINA_KW):
        return "China"
    for country, kws in _COUNTRY_MAP.items():
        if any(kw in addr for kw in kws):
            return country
    return None
