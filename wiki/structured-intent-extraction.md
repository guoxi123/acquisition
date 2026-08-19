# 结构化意图提取:让 LLM 稳定输出"市场+品类+数量"

> 用户一句话找卖家:"美国站卖杯子的2个中国卖家"。这一句里藏着目标市场(美国站)、品类(杯子/cups)、数量(2)、国籍偏好(中国)。怎么让 LLM 稳定地把这些拆出来、翻译成代码能用的结构化字段?本文讲 prompt 设计 + 工程兜底,让意图识别既准又稳。

## 一、问题:一句话里挤着好几个意图维度

用户输入是自然语言,千变万化:

- "美国站卖杯子的中国卖家" → 市场=amazon.com, 品类=cups, 国籍=China
- "欧洲站户外家具" → 市场=amazon.co.uk, 品类=outdoor furniture
- "找10个卖咖啡机的" → 品类=coffee machine, 数量=10(市场缺,要追问)

后端要用这些字段去查库(marketplace + category 必填)、过滤(国籍/评分可选)、决定取几个(target)。所以得从一句话里**结构化提取**出这些维度。

## 二、方案:Pydantic schema + 结构化 LLM 调用

定义想要的结构(Pydantic):

```python
class ParsedIntent(BaseModel):
    marketplace: str | None  # amazon.com / .co.uk ...
    category: str | None      # 英文品类
    shipping_type: str | None # sea/air
    service_mode: str | None  # FBA/FBM
    business_country: str | None # China/US(国籍偏好)
    min_total_feedback: int | None  # 规模下限
    min_seller_score: int | None     # 评分下限
    desired_count: int | None        # 用户指定数量
    need_confirm: bool        # marketplace/category 缺则 True
    missing: list[str]        # 缺的中文字段名
```

用 LLM 填这个 schema。关键在 prompt。

## 三、prompt 设计:两步提取 + few-shot

### 1. 两步提取(先剥离市场,再提品类)

最坑的 case:"欧洲站卖杯子卖家"被整体当成 category(marketplace 默认 amazon.com,查不到→0 个)。

根因:LLM 没意识到"欧洲站"是市场修饰词。修法是在 prompt 里明确"**两步**":

```
第一步——识别 marketplace:句首的站点词(美国站/US→amazon.com,欧洲站/欧洲→amazon.co.uk,英国站/UK→amazon.co.uk,德国站/DE→amazon.de,日本站/JP→amazon.co.jp)。没有站点词则 None。
第二步——提取 category:把句子去掉站点词和"卖…的卖家/有哪些/找一下"等句式后,剩余的核心产品词翻译成英文。无法判断则 None。
```

把"剥离市场修饰词"这个动作讲明白,LLM 就不会把整句当品类了。

### 2. few-shot 示例

光讲规则不够,给例子最有效:

```
示例:
- "欧洲站卖杯子卖家" → marketplace=amazon.co.uk, category=cups
- "美国站户外家具的中国大卖家" → marketplace=amazon.com, category=outdoor furniture, business_country=China, min_total_feedback=1000
- "美国站找10个卖杯子的卖家" → marketplace=amazon.com, category=cups, desired_count=10
- "德国站卖咖啡机的卖家" → marketplace=amazon.de, category=coffee machine
- "卖杯子" → marketplace=None, category=cups(缺市场,need_confirm)
```

尤其"欧洲站卖杯子"那条——直接告诉 LLM 正确答案,它就学会了。

### 3. 必填字段判定

marketplace 和 category 是查库的硬条件(缺了没法查),所以 prompt 里明确:

```
marketplace 和 category 是必要字段,任一为 None 则 need_confirm=True,并在 missing 列出缺失项中文名。
其余字段(shipping/service_mode/business_country/...)缺失不算 need_confirm。
```

这样"卖杯子"(缺市场)会触发 HITL 追问,而"美国站杯子"(没说海运空运)不会。

## 四、工程兜底:override(补充重跑时注入值)

HITL 补充重跑时(用户补了 marketplace/category),endpoint 把补充值注入 state。但 parse_intent 还会跑一次 LLM——如果 LLM 又把市场识别错,岂不是白补了?

所以 parse_intent 里做 **override**:用户明确给的值优先,LLM 结果只作补充:

```python
injected_marketplace = state.get("marketplace")  # endpoint 注入的(用户补充)
injected_category = state.get("category")

marketplace = injected_marketplace or parsed.marketplace  # 注入优先
category = injected_category or parsed.category
need_confirm = parsed.need_confirm and not (marketplace and category)  # 两个都有就不追问
```

补充重跑时 marketplace/category 齐了,need_confirm=False,正常往下走。LLM 的可选字段(business_country 等)仍保留(详见同系列 desired_count 那篇)。

## 五、解析失败兜底

LLM 偶尔不按 schema 输出(返回乱码/纯文本)。`_structured_invoke` 会重试两次,还失败就返回 None。这时:

- 如果 endpoint 已注入完整 marketplace/category(补充重跑)→ 用注入值继续,不 HITL
- 否则 → 触发 HITL("无法解析查询,请明确目标市场和品类")

永远不让 LLM 的抽风导致流程卡死或报 500。

## 六、总结

结构化意图提取是 agent 的"耳朵",它准不准直接决定后面查库对不对。做到稳定,三招:

1. **prompt 两步走 + few-shot**:把容易混的拆解动作(剥离市场修饰词)讲明白,给正确例子
2. **工程 override**:用户补充的值优先于 LLM 结果,防止补充重跑时 LLM 又识别错
3. **失败兜底**:LLM 解析失败时,要么用注入值继续,要么 HITL,不卡死

> prompt 工程不是"写一段话让 LLM 试试",是"把每个容易出错的点(混词、缺字段、解析失败)都配上对应的 prompt 策略 + 工程兜底"。LLM 负责"大多数情况对",工程负责"LLM 错了也能恢复"。
