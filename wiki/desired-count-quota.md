# "找 10 个"怎么办:意图里的数量 + 配额取最小

> 用户说"美国站卖杯子的 2 个中国卖家",这个"2"怎么处理?默认情况(用户没说数量)又该取几个?这涉及意图识别(提取数量)、业务规则(不能超过剩余配额)、SQL(影响 LIMIT)。本文讲一个 `desired_count` + `target = min(desired, remaining)` 的设计,把"用户想要多少"和"系统能给多少"理清楚。

## 一、问题:数量是个语义,不是数字

用户输入里的数量,有两种情况:

- **指定了**:"找 10 个""20 家" → 用户明确要 N 个
- **没指定**:"美国站卖杯子的中国卖家" → 用户没说,默认行为是什么?

默认行为有两种选择:
- 给一个固定数(如 10)——但免费用户配额可能就 10,初级用户 200,固定数不合理
- **给满剩余配额**——用户没说就尽量满足,拿满他能拿的

我选后者(拿满配额)。于是规则是:**用户指定了就取指定数,没指定就取剩余配额**。但还有上限:不管用户要多少,**不能超过他本月剩余的配额**(配额是硬约束)。

## 二、意图识别 desired_count

先让意图识别把数量提出来(详见同系列《结构化意图提取》)。在 ParsedIntent 加一个字段:

```python
desired_count: int | None = Field(
    default=None,
    description="用户想获取的卖家数量,从'N个/N家/N个卖家'(如'找10个''20家')推断;未提及 None",
)
```

prompt 里加一条 + few-shot 示例:

```
- desired_count:用户想获取的卖家数量,从"N个/N家/N个卖家"推断;未提及 None
...
- "美国站找10个卖杯子的卖家" → marketplace=amazon.com, category=cups, desired_count=10
```

实测:"找10个"→10、"20家"→20、没提→None。识别准确。

## 三、target = min(desired, 剩余配额)

拿到 desired_count 后,要算"本次到底取几个"(target)。规则:

- desired 有值:`target = min(desired, remaining)`(用户要的 vs 配额上限,取小)
- desired 为 None:`target = remaining`(默认拿满配额)

```python
# parse_intent 里(check_quota 已算出 remaining)
remaining = state.get("remaining") or 0
desired = parsed.desired_count
target = min(desired, remaining) if desired else remaining
```

几个 case:

| 用户输入 | remaining | desired | target | 含义 |
|---|---|---|---|---|
| 找 10 个 | 50 | 10 | 10 | 够,取用户要的 |
| 找 10 个 | 5 | 10 | 5 | 配额不够,取配额 |
| 找 10 个 | 0 | 10 | 0 | 配额耗尽(实际被 check_quota 拦去升级提示) |
| (没说数量) | 50 | None | 50 | 默认拿满 |

关键:`min(desired, remaining)` —— **用户要的和系统能给的,取小**。用户永远拿不到超过配额的。

## 四、target 用在哪

target 决定整个采集流程"取几个":

1. **query_db 的 LIMIT**:`.limit(target)` —— 库存查询最多取 target 个
2. **acquire_if_needed 的"够不够"判断**:`len(sellers) >= target` —— 够 target 就停采集循环(详见《采集循环图》)
3. **output_result 的"源耗尽"判断**:`len(result) < target` → 标记 source_exhausted(该品类全部展示完了)

所有"目标数量"语义的地方,都用 target,不用 remaining(remaining 是配额上限,不是本次目标)。

## 五、一个易错点:`min` 的条件

最初的实现写错过:

```python
# 错的
target = min(desired, remaining) if (desired and remaining) else (desired or remaining)
```

问题在 `desired and remaining`:当 `remaining=0`(配额耗尽)时,`desired and 0` 是 falsy,走 else 分支 `desired or 0 = desired`。于是 `desired=10, remaining=0` 算出 target=10——但配额都耗尽了还取 10?错。

正确写法:

```python
target = min(desired, remaining) if desired else remaining
```

只要 desired 有值,就一律 `min(desired, remaining)`(不管 remaining 是几);desired 为 None 才取 remaining。这样 `remaining=0` 时 target=0(配额耗尽,取 0),正确。

## 六、配额耗尽的拦截

其实配额耗尽(remaining=0)时,根本到不了 parse_intent——`check_quota` 节点检测到 `remaining=0`,条件边直接把请求导去 output_result 写"额度用完,升级"提示。所以 target=0 这个 case 在正常流程里走不到采集。

但代码仍要算对(防御性),因为用户可能改图(比如调试时让配额耗尽也走 parse_intent),target 算错就会 query_db 用错的 LIMIT。

## 七、总结

"用户要几个"这个看似简单的语义,涉及三层:

1. **意图层**:desired_count(用户说了取说了的,没说取 None)
2. **规则层**:target = min(desired, remaining)(用户要的 vs 配额,取小;没说要拿满配额)
3. **执行层**:LIMIT / 够量判断 / 源耗尽判断,全用 target

一个字段(desired_count)+ 一个 min,把"用户意图"和"业务约束(配额)"干净地结合,且每个环节(query_db / acquire / output)用统一的 target,语义一致。

> 凡是用户输入里有"数量"语义的 agent,都要想清楚三层:意图怎么提取、怎么和配额/限额取小、下游 SQL/判断怎么用。别让"找 10 个"变成"查 100 个"或者"查 0 个"。
