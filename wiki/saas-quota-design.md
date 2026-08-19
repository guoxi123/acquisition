# SaaS 配额:月度额度、永久去重、防超发

> 获客 Agent 是个 SaaS:免费用户每月能拿 10 个卖家,初级 200 个,超管不限。这背后要解决三个问题:怎么按月计数、怎么防止同一个卖家被重复扣额、怎么防并发超发。本文讲一套基于三张表 + 行锁的配额设计。

## 一、问题:配额要管三件事

1. **按月计数**:每月给用户 N 个额度,用了多少、剩多少。
2. **永久去重**:用户上个月拿过的卖家,这个月再查到不该再扣额度(同一个卖家只算一次)。
3. **防超发**:并发请求下,不能两个请求都以为"还剩 1 个"然后各发 1 个(超发)。

## 二、三张表

```
users                  用户表,含 plan(free/basic/super)
sellers                卖家表(全局,所有用户共享)
user_acquired_sellers  用户-卖家关联表(user_id, seller_id, acquired_at)——永久去重 + 月度计数来源
```

`user_acquired_sellers` 是核心:**它记录"每个用户拿过哪些卖家"**,是永久去重的真相表,也是月度配额计数的来源(`acquired_at` 带索引,按月 count)。

## 三、grant_sellers:切配额(行锁防超发)

查询出结果后,要决定"给用户看哪些"。这是 `grant_sellers` 的职责:

```python
async def grant_sellers(db, user_id, candidates):
    # candidates = 按评分排好的候选卖家列表

    # 1) 永久去重集合:用户已经拿过的
    already = set(查询 user_acquired_sellers 里 user_id 的所有 seller_id)
    new_ids = [sid for sid in cand_ids if sid not in already]

    # 2) 算本月已用(锁内)
    month_start = 本月1号0点
    used = select(count(*)).where(user_id, acquired_at >= month_start)
    remaining = max(0, quota - used)

    # 3) 按评分序切:new_ids 里取前 remaining 个
    granted_new = new_ids[:remaining]

    # 4) 原子插入(ON CONFLICT DO NOTHING,纵深防御)
    if granted_new:
        insert into user_acquired_sellers (user_id, seller_id) values (...) on conflict do nothing
    commit  # 释放行锁

    # 5) 展示集合 = 旧(已获取) + 新(本次发),保持原评分序
    return display_list
```

**防超发的关键**:步骤 2-4 在一个数据库事务里(对 user 的行锁或 user_acquired_sellers 的约束),并发请求串行化。两个请求同时进来,第二个等第一个 commit 后再读 `used`,不会都看到"剩 1"。

## 四、永久去重:user_acquired_sellers

`already` 集合来自 `user_acquired_sellers`(不限本月,是**所有历史**)。意思是:

- 用户 1 月拿了卖家 A
- 2 月再查,查到卖家 A → A 在 already 里 → **不重复扣额度**,但仍展示给用户(因为 A 是他拿过的)

所以"展示集合" = 用户拿过的全部(旧 + 新),但"扣额度"只算本次新发的。这符合直觉:同一个卖家不该让用户花两次额度。

`(user_id, seller_id)` 有唯一约束,`ON CONFLICT DO NOTHING` 是最后的兜底——即使逻辑漏了,数据库也不会插入重复。

## 五、超管也落库

最初的设计是"超管不限额度,直接返回所有候选,不落 user_acquired_sellers"。后来改成**超管也落库**(只是不限额度数):

```python
if user.is_super_admin:
    # 不限额,但同样 insert(去重)
    if new_ids:
        insert ... on conflict do nothing
    commit
    return cand_ids, {"unlimited": True, "new_granted": len(new_ids)}
```

为什么?因为超管也是用户,他的"已获取"集合也要维护——否则超管查过的卖家,普通用户(或超管换账号)查到时去重就不对。**去重逻辑对所有角色一致**,只是配额上限不同。这避免了"超管走一套逻辑、普通用户走另一套"的分裂。

## 六、月度配额计数

"本月已用"的计算:

```python
month_start = _utc_month_start()  # 本月1号0点 UTC
used = select(count(*)).where(
    user_acquired_sellers.user_id == user_id,
    user_acquired_sellers.acquired_at >= month_start,
).scalar()
remaining = max(0, quota - used)
```

`acquired_at` 有索引,按月 count 快。月初自动重置(used 归零),不用跑定时任务。

## 七、返回的配额 meta

grant_sellers 返回一个 meta,前端用来显示额度/升级提示:

```python
{
    "plan": "free", "quota": 10, "used": 7,
    "remaining_after": 3,         # 本次之后剩多少
    "new_granted": 3,             # 本次新发几个
    "new_skipped": 5,             # 因额度不够被跳过的
    "exhausted": False,           # 额度用完
    "upgrade_available": True,    # 有跳过 → 可升级
}
```

前端据此显示"还有 N 个卖家因额度未展示,升级可解锁"。

## 八、总结

SaaS 配额,三张表 + 一个行锁搞定:

- `user_acquired_sellers`:永久去重 + 月度计数来源(`acquired_at` 带索引)
- `grant_sellers`:事务内"算剩余 → 切配额 → 原子插入",行锁防并发超发
- `(user_id, seller_id)` 唯一约束 + ON CONFLICT DO NOTHING 兜底
- 超管也落库(去重一致),只是不限额度

关键原则:**去重对所有角色一致(永久),配额按角色上限切(月度),防超发靠事务行锁**。

> 配额系统最容易踩的坑是"去重和计数混在一起"和"并发超发"。把"永久拿过哪些"和"本月用了几个"分清楚(一个全量集合、一个月度 count),再用事务行锁兜住并发,配额就稳了。
