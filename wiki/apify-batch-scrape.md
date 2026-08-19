# Apify 批量采集降本:一次 actor 调用拿 N 个

> 采集亚马逊卖家用的是 Apify(按 actor 调用次数/结果量计费)。最初实现是"每个卖家调一次 actor 拿详情",一轮 50 个卖家就是 50 次调用——又慢又贵。本文讲怎么改成"一次 actor 调用传 N 个 sellerId 批量拿",把采集成本和耗时砍下来。

## 一、问题:逐个采集,按调用计费太贵

获客流程里,`call_actors` 节点要从 Apify 采集卖家详情(国籍、feedback、联系方式等)。最初的实现:

```python
sem = asyncio.Semaphore(4)  # 并发 4
async def _enrich_one(s):
    async with sem:
        detail = await provider.fetch_seller_profile(s["seller_id"], domain=marketplace)
        ...
await asyncio.gather(*[_enrich_one(s) for s in to_enrich])
```

每个待 enrich 的卖家,调一次 `fetch_seller_profile` → 一次 Apify actor 调用。一轮 50 个卖家 = 50 次 actor 调用。Apify 按调用/结果量计费,50 次的成本和耗时都很可观。

而且 actor 本身支持传**多个** sellerId——只是我最初按"单个"的思路用了。

## 二、思路:actor 支持批量,一次拿 N 个

Apify 的 `automation-lab/amazon-sellers-scraper` actor,`sellerIds` 参数本来就支持传数组。改成"一次传所有待 enrich 的 sellerId,一次调用拿全部详情":

```python
# 之前:N 次 actor
for s in to_enrich:
    detail = await fetch_seller_profile(s["seller_id"])  # 每次 1 个

# 之后:1 次 actor
details = await fetch_seller_profiles([s["seller_id"] for s in to_enrich])  # 一次 N 个
```

N 次 actor 调用 → 1 次。成本和耗时都大幅下降。

## 三、实现:fetch_seller_profiles

adapter 加一个批量函数:

```python
async def fetch_seller_profiles(client, seller_ids: list[str], domain: str = "amazon.com") -> list[dict]:
    """批量获取卖家详情:actor 支持一次传多个 sellerId,单次调用省费用/时间。"""
    if not seller_ids:
        return []
    run_input = {
        "sellerIds": seller_ids,          # 数组,一次传多个
        "domain": domain,
        "maxResults": len(seller_ids),
        "proxyConfiguration": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
    }
    run = await asyncio.to_thread(lambda: client.actor(ACTOR_ID).call(run_input=run_input))
    return await asyncio.to_thread(lambda: list(client.dataset(run.default_dataset_id).iterate_items()))
```

provider 层暴露:

```python
async def fetch_seller_profiles(self, seller_ids: list[str], domain: str = ".com") -> list[dict]:
    return await fetch_seller_profiles(_get_client(), seller_ids, domain)
```

call_actors 节点改成调批量:

```python
if to_enrich:
    details = await provider.fetch_seller_profiles([s["seller_id"] for s in to_enrich], domain=marketplace)
    # actor 返回项的 sellerId 可能叫 sellerId / seller_id / id,按多键匹配
    detail_map = {}
    for d in details:
        sid = d.get("sellerId") or d.get("seller_id") or d.get("id")
        if sid:
            detail_map[str(sid)] = d
    for s in to_enrich:
        detail = detail_map.get(s["seller_id"], {})
        s["detail"] = detail
        ...
```

注意 actor 返回的字段名可能不统一(sellerId / seller_id / id),按多键匹配兜底。

## 四、差集:只 enrich 新卖家

批量采集前,先算差集——只对"库里还没有的"卖家调 actor,已入库的不重复 enrich(省费用):

```python
async with async_session() as db:
    existing_ids = set(查 sellers 表里已有的 seller_id)
to_enrich = [s for s in sellers if s["seller_id"] not in existing_ids]
```

这样即使一轮聚合到 100 个卖家,其中 80 个已入库,只对 20 个新的调 actor。库存越积累,采集越省。

## 五、缓存层(可选,进一步省)

provider 层之前有按 seller_id 的缓存(fetch_seller_profile 命中缓存直接返回,免调 actor)。批量版改为"每次直接调 actor"(因为批量本身已经省了,缓存反而增加复杂度 + 数据时效问题)。

但 discover_products(抓产品列表)和 fetch_seller_products(抓卖家产品)这两步仍保留缓存——产品列表变化慢,缓存命中省得多。seller 详情(评分/feedback/国籍)相对稳定,后续也可加回缓存。

权衡:**缓存省的是"重复查同一个"的钱,批量省的是"一次查多个"的钱**,两者不冲突,可以叠加。但别过度设计,先批量(收益最大),缓存按需加。

## 六、upsert:只插新的,不覆盖旧的

采集到的卖家入库,用 `ON CONFLICT DO UPDATE`(PostgreSQL upsert):

```python
pg_insert(Seller).values(seller_id=sid, name=..., ...).on_conflict_do_update(
    index_elements=[Seller.seller_id],
    set_={"business_country": ..., "total_feedback": ..., "category": ...},  # 只更新这几个
)
```

注意 `set_` 只列"要更新的字段"——已入库卖家的某些字段(比如手动改过的)不被覆盖。这是"只插新的 + 选择性更新"的策略,防止采集把已有数据冲掉。

## 七、总结

Apify 按调用计费,把"N 次单个调用"改成"1 次批量调用",是最直接的降本手段:

- actor 本来就支持 sellerIds 数组,改成批量即可
- 配合差集(只 enrich 库里没有的),库存积累后采集越来越少
- upsert 选择性更新,不覆盖已有数据

核心就一句:**能批量的别逐个**。Apify actor 大多支持批量输入,改成批量是免费的性能/成本优化。

> 数据采集的成本,不在"采多少数据",在"调多少次 API"。把 N 次单个调用合并成 1 次批量,成本和耗时同时降一个数量级。这是用 Apify(以及大多数按调用计费的采集服务)最值得先做的优化。
