# 把本地数据同步到生产:PostgreSQL 给我挖的四个坑

> 任务很简单:本地库 305 行数据(sellers + products)搬到生产。听起来一句 `pg_dump | psql` 的事。结果在"本地 Docker pg → 生产 Docker pg、pg16、外键约束、schema 还有历史遗留不一致"的真实环境里,连环踩了四个坑。每个单独都不难,叠在一起、现象还都相似,够磨人一阵。本文逐个复盘 + 最后给一套能用的同步流程。

## 一、任务很简单

本地 Postgres 里有 106 个 sellers、199 个 products(测试采的数据),生产库是空的。任务:把这 305 行数据从本地搬到生产,让线上查询能复用库存。

听起来就是一句 `pg_dump | psql` 的事。结果踩了四个坑,每个都够单独写一段。

环境有点特殊:本地和生产的 Postgres 都跑在 Docker 里(`postgres:16-alpine`),pg_dump 是 16,psql 也是 16。这个"16"是第一个坑的伏笔。

## 二、坑 1:pg_dump 16 的 \restrict,容器 psql 不认

标准导出:

```sh
docker exec acquisition-pg pg_dump -U acquisition -d acquisition \
  --data-only -t sellers -t products > /tmp/sp.sql
```

到生产导入:

```sh
docker cp sp.sql acquisition-pg:/tmp/
docker exec acquisition-pg psql -U acquisition -d acquisition -f /tmp/sp.sql
```

报错:

```
psql:/tmp/sp.sql:1: error: backslash commands are restricted; only \unrestrict is allowed
ERROR: syntax error at or near "B0F1MZBQ47"
```

`B0F1MZBQ47` 是某条 product 的 seller_id——它被当 SQL 解析了。

原因:pg_dump **16** 在输出开头加了一行 `\restrict <token>`,这是个反泄漏保护(防止 dump 输出被恶意用于提权)。但 psql 用 `-f` 读文件时,处于"受限模式",遇到 `\restrict` 就拒——然后后面的 `COPY ... FROM stdin` 没正常启动,COPY 的数据行被当成一条条 SQL,于是 `B0F1MZBQ47`(数据)报 syntax error。

**pg_dump 16 的新指令,撞上 psql -f 的受限模式。**

修:导入前 grep 掉这两行:

```sh
grep -vE '^\\(un)?restrict' /tmp/sp.sql | docker exec -i acquisition-pg psql ...
```

## 三、坑 2:COPY FROM stdin,在 docker exec 管道里失效

坑 1 改用 stdin 喂(`docker exec -i psql < file` 或管道)绕过 `-f` 的限制。结果新错:

```
invalid command \N
invalid command \N
ERROR: syntax error at or near "B0F1MZBQ47"
```

`\N` 是 COPY 格式里的 NULL 标记。psql 把它当 backslash 命令了——说明 **COPY FROM stdin 根本没进"读数据"模式**。

为什么?COPY ... FROM stdin 要从 **psql 的 stdin** 读数据。但在 `docker exec -i psql < file` 这种"管道喂脚本"的场景,psql 把整个输入当脚本逐行处理,COPY FROM stdin 的数据读取机制和这种喂法对不上,数据行被当普通 SQL 行解析。

(单机直接 `psql < dump.sql` 一般没事;但在 docker exec 管道 + COPY 大量数据,这坑就冒出来了。)

结论:**COPY 格式的 dump,在 docker exec 管道里不可靠**。换 `--inserts`,生成 INSERT 语句,就没 COPY/`\N` 这套了。

## 四、坑 3:--inserts 按字母序,外键拦你

换 `--inserts` 导出:

```sh
docker exec acquisition-pg pg_dump ... --data-only --inserts -t sellers -t products > sp.sql
```

过滤掉 `\restrict`,导入。又报:

```
ERROR: insert or update on table "products" violates foreign key constraint
       "products_seller_id_fkey"
DETAIL: Key (seller_id)=(A3D7VI8D1P5MY) is not present in table "sellers".
```

products 引用 sellers(`seller_id` 外键),但 **products 先被 INSERT 了**——因为 `--inserts` 按表名字母序排,`products`(p) 在 `sellers`(s) 前面。products 插的时候 sellers 还没数据,外键拦腰截断。

修:导入时临时禁用外键触发器。Postgres 的招是 `SET session_replication_role = replica`——这个角色下,外键(靠触发器实现)不检查,INSERT 顺序随便:

```sh
docker exec acquisition-pg psql ... \
  -c "SET session_replication_role=replica" \
  -f /tmp/sp.sql \
  -c "SET session_replication_role=origin"
```

(同一 psql 会话里 -c/-f 顺序执行,replica 设置对 -f 整个文件生效,导完再切回 origin。)

## 五、坑 4:schema 不一致,数据导进去了但少一列

坑 3 解决,导入跑了,验证:`sellers 0, products 0`。一行没进去。

回头看,中途其实报过一个:

```
psql:/tmp/sp.sql:24: ERROR: INSERT has more expressions than target columns
```

本地 sellers 有 `category` 列,INSERT 语句写了 18 个值;生产 sellers 只有 17 列(没 category)——**两边 schema 不一致**。

为什么生产缺列?又是个陈年事故:某次 Alembic 迁移 version 表脏,`01f306`(加 category)被记了版本号但 SQL 没真跑(详见踩坑系列另一篇《部署后后端疯狂重启:Alembic 版本表脏了怎么办》)。

修:手动把缺的列补上,再导:

```sql
alter table sellers add column if not exists category varchar;
```

补完列,重跑 `--inserts + 禁外键` 导入,`sellers 106, products 199`,齐了。

## 六、最终方案:组合拳

把四个坑的修法叠起来,就是一套能用的同步流程:

```sh
# 1. 本地导出(--inserts 避免 COPY 在管道的坑)
docker exec acquisition-pg pg_dump -U acquisition -d acquisition \
  --data-only --inserts -t sellers -t products > /tmp/sp.sql

# 2. 传到生产
scp /tmp/sp.sql root@server:/tmp/

# 3. 生产导入:过滤 \restrict + 禁外键 + 导完恢复
ssh root@server 'bash -s' << 'EOF'
  grep -vE '^\\(un)?restrict' /tmp/sp.sql > /tmp/sp_clean.sql
  docker cp /tmp/sp_clean.sql acquisition-pg:/tmp/sp.sql
  docker exec acquisition-pg psql -U acquisition -d acquisition \
    -c "SET session_replication_role=replica" \
    -f /tmp/sp.sql \
    -c "SET session_replication_role=origin"
  # 验证
  docker exec acquisition-pg psql -U acquisition -d acquisition -c \
    "select 'sellers',count(*) from sellers union all select 'products',count(*) from products;"
EOF
```

前提:两边 schema 已对齐(缺列先补)。

## 七、踩坑清单(下次别再踩)

1. **pg_dump 16 的 `\restrict`**:导入前 `grep -v` 过滤,或用比 16 老的客户端导出。
2. **COPY FROM stdin 在 docker 管道**:别用 COPY 格式 dump 走管道,改 `--inserts`。
3. **--inserts 字母序 + 外键**:导入时 `SET session_replication_role=replica` 临时禁外键,导完切回。
4. **schema 不一致**:导前对齐两边列(查 `information_schema.columns`),别等 INSERT 报"列数不匹配"才发现。

## 八、总结

`pg_dump | psql` 在理想环境里一句命令的事,到真实环境(Docker pg、pg16、外键、schema 历史遗留不一致)里,连环四个坑。

每个坑单独都不难,难的是它们叠在一起、现象相似(都是"数据没进去"、报错都带个莫名奇妙的标识符),容易让人误判。

最后的解法也没什么高深:用 `--inserts` 避 COPY、过滤 `\restrict`、禁外键、对齐 schema——都是朴素的工程动作。

> 数据同步的本质是"把 A 的状态复制到 B"。坑从来不在"复制",在"A 和 B 其实不一样":pg 版本不一样、执行环境不一样(docker 管道)、约束不一样(外键)、schema 不一样(缺列)。同步前先把这几样对齐,比记命令重要。
