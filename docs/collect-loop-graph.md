# 采集循环图:库存不够就采,够了就分析

> 获客 Agent 有个特点:用户要 N 个卖家,库存里可能只有 3 个,不够就得去 Apify 采集,采完再查,可能还不够,再采……这是个"查库 → 判断够不够 → 不够就采 → 回查库"的循环。本文讲怎么用 LangGraph 把这个循环画成一张可控的图,以及怎么防止它无限采下去。

## 一、问题:库存是动态的

用户查"美国站卖户外家具的中国卖家,找 50 个"。后端先查本地 `sellers` 表(复用库存省采集费),假设只查到 10 个。不够 50,怎么办?

- 不能直接返回 10 个(用户要 50)
- 不能无脑采(采集费钱,可能采不到)
- 要"采一轮 → 入库 → 再查 → 还不够 → 再采",直到够数或实在采不到

这是个循环,而且要有**早停条件**(否则一直采,钱烧光)。

## 二、循环图设计

```
query_db → acquire_if_needed(判断)
              ├── 够了 / 达上限 / 采空了 → llm_analysis → score → output
              └── 不够 → call_actors(采集一轮) → 回 query_db
```

三个关键点:
1. **query_db**:查本地库存(唯一写 sellers 的节点,覆盖语义)
2. **acquire_if_needed**:判断节点,只算够不够,不产生数据
3. **call_actors**:采集一轮(子 agent:采集入库 + 查联系方式),完成后**回到 query_db** 再查

循环由 `acquire_if_needed` 后的条件边驱动。

## 三、实现

判断 + 路由:

```python
def _route_acquire(state) -> str:
    """够 / 达 max_rounds / 上一轮 0 新增 → 分析;否则 → 采集。"""
    target = state.get("target") or 0           # 本次目标数量
    sellers = state.get("sellers") or []        # 已查到的
    fetch_round = state.get("fetch_round") or 0
    max_rounds = state.get("max_rounds") or 3
    last_new = state.get("last_new_count") or 0
    if (
        len(sellers) >= target                  # 够了
        or fetch_round >= max_rounds            # 达最大轮次
        or (fetch_round > 0 and last_new == 0)  # 上一轮一个新卖家都没采到(源耗尽)
    ):
        return "llm_analysis"
    return "call_actors"
```

图:

```python
builder.add_edge("query_db", "acquire_if_needed")
builder.add_conditional_edges("acquire_if_needed", _route_acquire)
builder.add_edge("call_actors", "query_db")   # 采集完回查库,再判断
```

call_actors 本身是个子 agent(采集入库 + 查联系方式),作为主图一个节点嵌入,完成后由主图边回到 query_db。

## 四、三个早停条件

无限循环是最大风险。三个条件里任一满足就停,进入分析:

1. **够数**:`len(sellers) >= target`。查到的够用户要的了,不用再采。
2. **达最大轮次**:`fetch_round >= max_rounds`(默认 3)。防止"每轮采到一点点"的温水煮青蛙,烧太多轮。
3. **源耗尽**:`fetch_round > 0 and last_new == 0`。上一轮采集**一个新卖家都没采到**,说明这个品类在这个市场已经被采空了,再采也没用。

第 3 个尤其重要。没有它,如果 Apify 对某品类返回空,Agent 会傻乎乎一轮轮采到 max_rounds。有了它,一轮空就立即停,省钱。

## 五、target:循环的"够数"基准

`target` 不是简单的"剩余配额",而是 `min(用户指定数量, 剩余配额)`(详见同系列关于 desired_count 的那篇)。用户说"找 10 个",target=10;没说数量,target=剩余配额(拿满)。

循环判断用的是 target,所以"用户要几个"和"采到够几个"是绑定的——不会出现"用户要 10 个,Agent 采了 50 个"的浪费。

## 六、call_actors 是子 agent

call_actors 本身是一个编译好的子图(采集入库 → 查联系方式),作为节点嵌入主图:

```python
def build_call_actors_subagent():
    b = StateGraph(V2State)
    b.add_node("call_actors", call_actors)       # junglee 抓产品 → 聚合卖家 → fetch 详情 → upsert
    b.add_node("lookup_contacts", lookup_contacts) # 对中国卖家查天眼查/企查查联系方式
    b.add_edge(START, "call_actors")
    b.add_edge("call_actors", "lookup_contacts")
    b.add_edge("lookup_contacts", END)
    return b.compile()

builder.add_node("call_actors", build_call_actors_subagent())
```

子 agent 完成后,主图边 `call_actors → query_db` 把控制权交回,query_db 重新查库(这时新采的已经入库),acquire_if_needed 再判断。这就是循环。

## 七、一个细节:为什么 query_db 是唯一写 sellers 的节点

call_actors 采集后 **不直接把卖家塞进 state["sellers"]**,而是入库,然后让 query_db 重新查。这样:
- sellers 的来源唯一(query_db),语义清晰(覆盖式)
- 新采的卖家和库存里的卖家走同一条路径(查库 → 转 dict),格式一致
- 避免子 agent 和主图都写 sellers 的并发/覆盖混乱

## 八、总结

"库存不够就采"是个天然的循环,LangGraph 的循环图 + 条件边正好表达它。关键是三个早停条件(够数 / 达上限 / 源耗尽),防止烧钱无限采。

设计上几个要点:
- 判断节点(acquire_if_needed)只判断不产生数据,路由靠条件边
- target = min(用户数量, 配额),绑定"采到够几个"
- 采集节点是子 agent,完成后回 query_db 重新查(单一数据源)
- 采集不直接写 state,写库,让 query_db 统一查

> 循环图不可怕,可怕的是没有早停条件。只要把"什么时候必须停"想清楚(够、到顶、采空),循环就是可控的增量采集,不是无底洞。
