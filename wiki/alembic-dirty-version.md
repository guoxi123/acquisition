# 部署后后端疯狂重启:Alembic 版本表脏了怎么办

> 部署完一访问就 502,一看容器 `Restarting (255)` 无限循环,日志在刷一个看不懂的 `overlaps` 报错。查下来是 Alembic 的版本表"脏了"——这种生产迁移事故,根因往往不在迁移脚本,而在那张记录版本号的 `alembic_version` 表。本文是一次真实排错的完整复盘,以及一个通用的修复原则。

## 一、现象:容器一直在 Restarting

部署完,访问 502。一看容器:`acquisition-backend Restarting (255)`——exit 255,反复重启。

后端日志在刷同一段错:

```
=== 数据库迁移 ===
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
ERROR [alembic.util.messaging] Requested revision 01f306de7a98 overlaps with other requested revisions d2f01a829159
FAILED: Requested revision 01f306de7a98 overlaps with other requested revisions d2f01a829159
=== 数据库迁移 ===
（无限循环）
```

entrypoint 是 `alembic upgrade head` → 失败 → 容器退出 → docker restart → 再失败……死循环。服务起不来。

## 二、错误在说什么

`Requested revision A overlaps with other requested revisions B`。

A 是 `01f306de7a98`,B 是 `d2f01a829159`。这两个 revision 是**父子关系**(`01f306` 的 `down_revision` 就是 `d2f01a`)。

Alembic 说:你要 upgrade 到的目标,有两个,而且它俩在同一条链上(一个是另一个的祖先)——这叫"重叠",拒绝执行。

可我只跑了 `alembic upgrade head`,只有一个 head 啊,哪来的"两个目标"?

## 三、Alembic 怎么工作(快速铺垫)

Alembic 靠一张表 `alembic_version` 记录"当前数据库在迁移链的哪个位置",正常**只有一行**:

```
select * from alembic_version;
 version_num
-------------
 b3c4d5e6f7a8   ← 当前版本(head)
```

`alembic upgrade head` 干的事:
1. 读 `alembic_version`,拿到"当前在哪"(current)
2. 算从 current 到 head 要跑哪些迁移
3. 依次执行那些迁移的 `upgrade()`,每跑一个更新 version 表

关键:current 可以有多个起点吗?理论上 Alembic 支持"多 current"(分叉的迁移图),但 `upgrade head` 时,它会把"所有 current → head"的路径都算出来。如果这些 current 在同一条链上(一个是另一个祖先),路径就会**重叠**——于是报错。

所以问题不在 head,在 **version 表里有多个 current,而且它们在同一条链上**。

## 四、排查:version 表果然有两行

```sql
select version_num from alembic_version;
 version_num
--------------
 d2f01a829159
 01f306de7a98
```

**两行**。`d2f01a` 是父,`01f306` 是子。两个都被记成了"当前版本"。

`upgrade head` 时,Alembic 从这两个 current 出发,各算到 head 的路径:
- 从 `01f306`(子)→ head
- 从 `d2f01a`(父)→ 必须经过 `01f306` → head

第二条路径包含了 `01f306`,和第一条重叠 → 报错。

## 五、根因:version 表是怎么"脏"的

正常 Alembic 只写一行 current。什么时候会变成两行?

几种可能:
1. **迁移跑到一半被打断**(容器 OOM、强制 kill),upgrade 事务部分提交,version 表被写了但没清理。
2. **手动 `alembic stamp` 操作错误**:有人 `stamp` 了一个 revision,又在没清掉旧的情况下 `stamp` 了另一个。
3. **数据库 restore/迁移混乱**:从别处导数据时把脏的 version 表一起导进来了。

我这台服务器,最可能是历史上某次部署/迁移中途异常,留下了一个"半残"状态:新 revision 被记进 version 表,旧的没删。

更糟的是:**version 表说在 `01f306`,但 schema 实际只到 `d2f01a`**——`01f306` 那个迁移(给 sellers 加 category 列)被记了版本号,**SQL 没真正执行**(category 列其实不存在)。

所以这是个双重不一致:version 表脏(两行)+ schema 与 version 不符(记了 01f306 但没它的列)。

## 六、修复:三步走

**第一步:看 schema 实际到哪一级**(别信 version 表):

```sql
-- 01f306 加的是 sellers.category,查它存不存在
select column_name from information_schema.columns
 where table_name='sellers' and column_name='category';
-- 结果:空 → category 列没有,说明 01f306 的 SQL 没跑
```

所以 schema 真实位置是 `d2f01a`(`01f306` 的父),不是 `01f306`。

**第二步:清 version 表成单行,指向 schema 真实位置**:

```sql
delete from alembic_version;
insert into alembic_version(version_num) values ('d2f01a829159');
```

注意:**指到 schema 实际在哪(d2f01a),不是指到 version 表谎称的 01f306**。这是关键——指错了,后面的迁移会跳过该跑的(以为已经跑过)。

**第三步:让 entrypoint 正常 upgrade head**:

重启后端,`alembic upgrade head` 这次从 `d2f01a` 出发,老老实实跑 `01f306`(补 category 列)→ ... → head。version 表回到单行 head,服务起来了。

(顺带,因为之前 `01f306` 被谎记、category 列确实没建,upgrade 这次真给补上了。如果第二步错误地把 version 指到 `01f306`,upgrade 会跳过建 category,后面代码用 category 列就报"列不存在"。)

## 七、反面教训

这次排错,有三个教训值得记住:

**1. 别乱 `alembic stamp head`**

stamp 是"我保证 schema 已经到这了,你只更新版本号"。schema 没真到就 stamp head = **让 Alembic 跳过所有未跑的迁移**,留下"版本说是 head、schema 其实落后"的隐患,后面随时炸。

stamp 只在一种情况用:**你确认 schema 已经是最新**(比如手动建过表、或从已最新实例导的数据),只补版本号。否则,清成单行指向**schema 真实位置**,然后 `upgrade head` 让它真跑。

**2. 别只信 version 表,要信 schema**

version 表会说谎(这次就是)。排查迁移问题,第一反应是**查关键表/列实际存不存在**,对照迁移文件,确定 schema 真实位置。`information_schema.columns` 是你的朋友。

**3. version 表有多行就是病态**

正常单一线性迁移链,version 表永远一行。看到多行(且不是有意做的分叉),就是在告诉你:曾经有迁移中途异常或被手动乱改。先把它修成单行再谈别的。

## 八、一个排错清单

下次遇到 Alembic 上线报错,按这个顺序查:

1. `select * from alembic_version;` —— 几行?多行就是脏。
2. 对照报错的 revision,看它们是不是父子(同链 → 重叠报错的来源)。
3. 查 schema 真实位置(关键列/表存不存在),确定该把 version 指到哪。
4. 清 version 成单行 + 指向 schema 真实位置。
5. `alembic upgrade head`,让该跑的迁移真跑一遍。
6. 千万别在没确认 schema 的情况下 `stamp head`。

## 九、总结

Alembic 很稳,但它依赖一张"说真话"的 `alembic_version` 表。一旦这张表脏了(多行 / 版本号和 schema 不符),`upgrade head` 就会以各种诡异方式失败——最典型就是这次的 `overlaps`。

修复的核心原则就一句:**清成单行,指向 schema 真实位置,然后让 upgrade 真正执行**。信 schema,别信版本号;能 upgrade 就别 stamp。

> 迁移工具是"账本",账本可以对不上库存(schema),但对不上的时候,要以库存为准去调账,而不是反过来在账本上硬填"库存满了"。
