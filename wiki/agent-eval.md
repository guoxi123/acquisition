# Agent eval:给意图分类配测试集

> LLM 应用的一个老大难:改了 prompt,怎么知道是变好还是变差?传统软件有单元测试,agent 靠"手动试几个"根本不够。本文讲怎么用 pytest + 真实 LLM 给 agent 的"入口分类"配一套测试集,让每次改动都有客观的准确率指标,不再盲改。

## 一、问题:改 prompt 像盲人摸象

agent 的核心逻辑大量依赖 LLM(意图分类、结构化提取、tool 选择)。LLM 输出有随机性,改一句 prompt:

- 可能让 90% 的 case 变好,10% 变差
- 可能在你的测试输入上变好,在别的输入上变差
- 你不测就不知道

"手动试 3 个感觉不错就上线" = 赌博。需要一套**回归测试**:一组标注好的输入 + 期望输出,每次改完跑一遍,准确率涨了还是跌了一目了然。

## 二、为什么不能 mock LLM

一个直觉:测试里把 LLM mock 掉(返回固定值),不就能跑了?

不行。mock LLM 测的是"给定 LLM 输出,你的代码逻辑对不对",**测不到 LLM 本身的准确率**。而 agent 退化,恰恰是"prompt 改了 → LLM 对同样输入给出了不同(更差)的结果"。这必须用**真实 LLM** 跑。

所以 agent eval 的原则:**用真实模型跑测试集**,不 mock。代价是慢一点、费点 token,但能反映真实表现。

## 三、方案:pytest + 真实 LLM 测试集

### 1. 测试集

一组 `(输入, 期望输出)`。意图分类的测试集,覆盖三类 + 口语变体:

```python
CASES = [
    # acquisition(找新卖家)
    ("美国站卖杯子的中国卖家", "acquisition"),
    ("找一下英国站卖水杯的卖家", "acquisition"),
    ("德国站 FBA 卖咖啡机的", "acquisition"),
    ("欧洲站户外家具", "acquisition"),
    # query(查已获取)
    ("查看我获取的所有卖家", "query"),
    ("我收藏的卖家有哪些", "query"),
    ("帮我统计一下我获取的中国卖家", "query"),
    ("我之前查过的卖家", "query"),
    # chat(闲聊)
    ("你是谁", "chat"),
    ("你能做什么", "chat"),
    ("你好", "chat"),
    ("谢谢", "chat"),
]
```

12 个 case,三类各 4 个,覆盖常见说法 + 容易混的("帮我统计"是 query 不是 acquisition,"欧洲站户外家具"是 acquisition)。

### 2. 参数化测试

pytest 的 `parametrize` 把每个 case 变成独立测试:

```python
@pytest.mark.parametrize("query, expected", CASES)
async def test_classify_intent(query, expected):
    r = await classify_intent({"user_query": query})
    assert r["intent"] == expected, f"'{query}' 期望 {expected},实际 {r['intent']}"
```

跑起来每个 case 单独 PASSED/FAILED,一眼看到哪个挂了。

## 四、配置:pytest-asyncio

classify_intent 是 async,要 `pytest-asyncio`。pyproject 配 `asyncio_mode = "auto"`(async def test 自动当 async 测试):

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
pythonpath = ["."]
```

跑:

```sh
cd backend && uv run pytest tests/agent_eval/test_classify.py -v
```

## 五、结果与意义

第一次跑:12/12 通过(100%)。说明当前 classify 三分类在常见说法上稳。

但 eval 的价值不在"这次过了",在**之后的每次改动**:

- 加了个意图类(比如"导出")?跑 eval,看新类和旧类有没有互相干扰。
- 改了 prompt 措辞?跑 eval,看是不是某些 case 从对变错。
- 换了 LLM 模型?跑 eval,看新模型在同样的测试集上表现如何。

没有 eval,这些改动全是"手动试两个,感觉还行,上线"。有了 eval,准确率从 12/12 掉到 10/12,立即红灯,逼你定位是哪个 case 退化、为什么。

## 六、扩展:不止 classify

classify 是 agent 的入口(分错了一切都错),最先配 eval。但 eval 的思路可以扩到整个 agent:

1. **tool 选择 eval**:给 query_agent 一组查询,断言它调了正确的 tool(或至少调对了类型)。测的是"LLM 在多工具场景下的选择能力"。
2. **端到端 eval**:几个典型查询 → 断言结果含期望的关键字段/卖家数。测的是"整条链路"。
3. **回归 eval**:线上真实的 bad case(用户反馈查错了)加进测试集,防同一个坑踩两次。

不必一次性全做,先从最关键的入口(classify)开始,逐步补。

## 七、一个取舍:eval 不进常规 CI

真实 LLM 跑 eval:慢(每个 case 一次 LLM 调用,12 个 case 十几秒)、费 token、还有网络依赖。所以**不建议塞进每次 push 的 CI**(会让 CI 又慢又贵)。

更适合的是:
- 改 prompt / 意图逻辑时,**手动跑一遍**
- 发版前,跑一遍全量 eval 当 gate
- 定期(每周/每次模型升级)跑,监控准确率漂移

## 八、总结

Agent eval 的核心:**用真实 LLM 跑一组标注好的测试集,客观衡量"改完是变好还是变差"**。

最小实现:pytest + pytest-asyncio + parametrize,一个 CASES 列表 + 一个 async test 函数。从最关键的入口(classify)开始,逐步扩到 tool 选择和端到端。

> 没有 eval 的 agent 开发,是"凭感觉改 prompt"。有 eval,是"改完看准确率"。前者是玄学,后者是工程。哪怕只有 12 个 case,也比手动试强太多。
