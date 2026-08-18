# RAG 语义检索：pgvector + bge-m3 替换 LIKE 模糊匹配

> 获客系统的核心资产是 `sellers` / `products` 两张库表，但检索它们一直靠 `category LIKE '%x%'` 字符串匹配——用户说「宠物智能用品」，库存里 category 存的是 `PetSupplies` / `Electronics`，一个都匹配不上。结果是库存复用率低，大量请求触发昂贵的 Apify 付费采集。本文讲怎么给存量业务表加一层向量语义检索：选型（为什么是 pgvector + 硅基流动 bge-m3，而不是独立向量库或本地模型）、入库/查询两侧的文本对齐、embedding 生成时机（采集落库时同步生成）、降级策略（外部 API 抖动不阻断获客主流程）、存量回填的幂等设计，以及配套的测试分层。

***

## 一、问题：LIKE 匹配浪费了整个库存

获客主图的库存复用路径是这样的：

```
parse_intent 解析出 category（中文→英文翻译后，如 "pet supplies"）
    ↓
query_db: SELECT * FROM sellers
          WHERE marketplace = 'amazon.com'
            AND lower(category) LIKE '%pet supplies%'
    ↓
命中不足 → acquire_if_needed → call_actors（Apify junglee 付费采集）
```

这条链路有三个结构性问题：

**1. 品类词表对不齐。** 采集入库时 category 写的是 junglee 返回的英文面包屑（`PetSupplies`、`Home & Kitchen > Cookware`），而用户需求是自然语言。LIKE 只能命中「子串恰好出现」的情况：「宠物智能用品」翻译成 `pet smart supplies`，LIKE 匹配不到任何行。

**2. 商品文本完全没用上。** `products` 表的 `title` / `brand` / `bread_crumbs` 是最丰富的语义信号（一个卖 dog camera 的卖家，title 里写着 "Pet Camera, Dog Monitoring Camera with Treat Dispenser"），但检索层完全没碰它。

**3. query\_agent 的工具只有精确条件。** ReAct 子图里 `filter_my_sellers` 的参数是 `country` / `min_score` / `category`（ilike）——用户问「帮我找做高端厨具的卖家」，LLM 没有任何工具能表达「高端厨具」这个语义。

三者共同指向同一个解法：**给 sellers/products 建 embedding，用向量相似度做检索**——也就是把 RAG 的「检索」环节套在业务数据上（本项目不做知识库问答、不动会话记忆，只做数据检索增强）。

## 二、选型：pgvector + 托管 embedding API

### 2.1 向量存储：pgvector，不是独立向量库

候选有三类：

| 方案                  | 优点                                 | 缺点                      |
| ------------------- | ---------------------------------- | ----------------------- |
| **pgvector（PG 扩展）** | 和业务表同库，硬过滤 + 向量排序一条 SQL；事务一致；运维零新增 | 单机，超千万向量后需迁移            |
| Milvus / Qdrant     | 性能天花板高，分布式                         | 多一个有状态服务，采集/回填要维护双写一致性  |
| SQLite-vec / 内存索引   | 零依赖                                | 数据量超内存不可行，多 worker 无法共享 |

决策依据是数据量级：sellers 当前 112 行、products 几千行，即便跑两年也到不了十万级。pgvector 顺序扫描余弦距离在几千行规模 <10ms，**根本轮不到 ANN 索引发挥**。引入 Milvus 意味着 compose 多一个服务、双写一致性、备份策略重做——为用不上的性能付运维成本，典型的投机设计。

PG 侧的改动只有两处：

```yaml
# docker-compose.yml：换镜像（数据卷兼容，PG 主版本不变）
image: pgvector/pgvector:pg16   # 原 postgres:16-alpine
```

```python
# alembic 迁移（c5d6e7f8a9b0）
op.execute("CREATE EXTENSION IF NOT EXISTS vector")   # PG13+ trusted extension，DB owner 可直接建
op.execute("ALTER TABLE sellers  ADD COLUMN embedding vector(1024)")
op.execute("ALTER TABLE products ADD COLUMN embedding vector(1024)")
op.execute("CREATE INDEX ix_sellers_embedding  ON sellers  USING hnsw (embedding vector_cosine_ops)")
op.execute("CREATE INDEX ix_products_embedding ON products USING hnsw (embedding vector_cosine_ops)")
```

**为什么索引选 HNSW 而不是 ivfflat**：ivfflat 需要先聚类，`lists` 参数依赖行数——而本项目上线时全表是 NULL（回填前），行数未知且持续增长，选错了召回率悄悄劣化。HNSW 无需训练、增量插入友好，写入稍慢但这里写入频率极低（只在采集落库时）。索引建了但查询不强依赖（见 3.2 的顺序扫描论证），算是给未来数据量增长留的安全垫。

Model 层用 `pgvector` Python 包的 SQLAlchemy 类型：

```python
# app/models/seller.py
from pgvector.sqlalchemy import Vector

embedding: Mapped[list | None] = mapped_column(Vector(1024), nullable=True)
```

### 2.2 Embedding 服务：硅基流动 bge-m3

项目 LLM 走 DeepSeek（OpenAI 兼容协议），但 **DeepSeek 不提供 embedding 端点**，必须单独选一家。约束条件：

- 国内直连（生产服务器在国内，走 OpenAI 需要代理，运维成本高）
- OpenAI 兼容 `/v1/embeddings`（复用现有 httpx 调用模式，不引新 SDK）
- 中文效果好（用户查询是中文品类词）

硅基流动的 `BAAI/bge-m3` 三项全占：免费额度大、1024 维、中英双语。配置独立于 LLM 三元组：

```python
# app/core/config.py
embedding_api_key: str = ""                                      # 走 EMBEDDING_API_KEY 环境变量
embedding_base_url: str = "https://api.siliconflow.cn/v1"
embedding_model: str = "BAAI/bge-m3"
embedding_dim: int = 1024
```

不选本地 sentence-transformers：要在容器里装 torch + 模型，镜像体积涨几百 MB、冷启动慢，换来的只是「不依赖外部 API」——而这条依赖已经被降级策略兜住了（见第四节）。

## 三、检索链路设计

### 3.1 文本构造：入库侧与查询侧必须形态对齐

embedding 的相似度只在「同一分布的文本」之间有意义。入库和查询两侧的文本构造不对齐，检索质量会静默劣化——这是 RAG 最容易踩的坑，所以文本构造函数集中在一个文件里，当成契约维护：

```python
# app/core/embedding.py
def seller_text(s) -> str:
    """卖家入库文本"""
    return " | ".join(p for p in [s.name, s.category, s.business_name] if p)

def product_text(p) -> str:
    """商品入库文本"""
    return " | ".join(p for p in [p.title, p.brand, p.bread_crumbs] if p)

def seller_query_text(category: str, marketplace: str) -> str:
    """查询侧文本：与 seller_text 形态对齐"""
    return f"{category} seller | {category} | {marketplace}"
```

查询侧故意拼成和入库侧相同的 `A | B | C` 三段形态（名字位品类词、品类位品类词、公司位站点），让查询向量落在与卖家向量相同的语义子空间里。bge-m3 是检索模型（query/passage 非对称），这种「形态对齐」不能替代模型侧的指令前缀，但在轻量场景下够用，且两侧都在一个文件里，改起来不会漂移。

### 3.2 query\_db 改造：硬过滤前置 + 向量排序

改造原则：**WHERE 里的硬条件一个不动，只换「品类过滤 + 排序」这两件事**。

```python
# app/agent/v2/nodes.py — query_db
emb = None
try:
    emb = (await embed_texts([seller_query_text(category, marketplace)]))[0]
except Exception as e:
    logger.warning(f"[query_db] embedding 失败，降级 LIKE 匹配: {e}")

q = select(Seller).where(Seller.marketplace == marketplace)
if emb is not None:
    q = q.where(Seller.embedding.isnot(None)).order_by(
        Seller.embedding.cosine_distance(emb)     # SQLAlchemy 编译为 pgvector 的 <=> 操作符
    )
else:
    q = q.where(func.lower(Seller.category).like(f"%{category}%")).order_by(
        Seller.total_feedback.desc().nullslast()
    )
# 以下 business_country / min_total_feedback / 排除已获取 等硬过滤原样保留
```

语义上的关键选择：

- **品类不再作为 WHERE 条件，而是变成排序依据。** LIKE 时代「匹配不匹配」是二值的；向量时代「有多相关」是连续的。`ORDER BY embedding <=> :query` 把整个库存按相关度排队，`LIMIT target` 取最相关的 N 个——品类相近但不精确相等的卖家（`Electronics` vs `pet supplies` 里的智能设备卖家）也能被召回。
- **`embedding IS NOT NULL`** **过滤**：存量未回填的行不参与语义排序（NULL 参与向量运算会报错），回填完成后该条件退化为空集。
- **为什么不用担心性能**：几千行顺序扫描算余弦距离 <10ms。HNSW 索引 + 大量 WHERE 过滤反而有「ANN 候选不足」问题——索引先取 top-K 候选再过滤，过滤掉一半后剩下的可能不够 `LIMIT target`。行数上来后如果要走索引，正确姿势是迭代索引扫描（pgvector 0.8+ 的 ordered index scan），当前规模不需要。

### 3.3 query\_agent 加语义搜索工具

skills.py 的工具注册表模式（见 `docs/skill-tool-registry.md`）在这里发挥作用：**加一个** **`@tool`** **函数 + 注册，ReAct agent 自动可用**，不动图、不动 Router：

```python
# app/agent/skills.py
@tool
async def semantic_search_sellers(user_id: str, query: str, limit: int = 10) -> list:
    """在当前用户已获取的卖家中做语义搜索（向量相似度召回）。
    用户用模糊或自然语言描述品类时调用，如"做高端厨具的卖家"；
    精确条件（国家/评分/品类关键词）优先用 filter_my_sellers。"""
    emb = (await embed_texts([query]))[0]
    # join user_acquired_sellers（只搜该用户已获取的）
    # ORDER BY embedding.cosine_distance(emb) LIMIT limit
    ...

ALL_TOOLS = [get_my_sellers, filter_my_sellers, semantic_search_sellers]
```

工具搜索范围限定在**该用户已获取的卖家**（join `user_acquired_sellers`），与 `get_my_sellers` / `filter_my_sellers` 的数据边界一致——query\_agent 是「查我的数据」的入口，不该绕过配额体系看到全库。

docstring 里写明「精确条件优先用 filter\_my\_sellers」，让 LLM 在两个工具间正确路由：精确筛选走结构化工具（确定性），模糊语义走向量工具。

### 3.4 embedding 失败的两级降级策略

外部 API 依赖必须回答「挂了怎么办」。两条链路的答案不同：

| 链路                            | 策略                                   | 理由                                             |
| ----------------------------- | ------------------------------------ | ---------------------------------------------- |
| `query_db`（获客主流程）             | 静默降级 LIKE + log warning              | 获客不能因 embedding 抖动中断；LIKE 是改造前的现状基线，最坏情况 = 改造前 |
| `semantic_search_sellers`（工具） | 返回错误文本，ReAct 自行换 `filter_my_sellers` | 工具层没有降级目标（LIKE 语义不等价），让 agent 自己路由到别的工具        |

`call_actors` 落库时的 embedding 生成同理：失败仅 log、行留 NULL，**不阻断采集**——采集是付费操作（Apify actor），为 embedding 挂掉而丢掉一轮采集数据是本末倒置。NULL 行由回填脚本兜底。

## 四、embedding 生成的三个时机

**1. 采集落库时同步生成（增量）。** `call_actors` 每轮采集约几十卖家 + 几百产品，构造文本后一次 `embed_texts` 批量调用（几百 ms），随 upsert 一起写入：

```python
# call_actors 内，upsert 之前
emb_inputs = [" | ".join(...) for s in to_enrich]     # 文本形态与 seller_text 一致
for s, e in zip(to_enrich, await embed_texts(emb_inputs)):
    seller_embs[s["seller_id"]] = e
# pg_insert(Seller).values(..., embedding=seller_embs.get(sid))
```

不做 arq 异步任务：数据量小（一批几十条）、失败可容忍（留 NULL）、少一条队列链路。「同步生成拖慢采集」的代价是几百 ms，相对 Apify actor 的十几秒可以忽略。

**2. 存量回填（一次性脚本）。** `backend/scripts/backfill_embeddings.py`，幂等设计：

```bash
cd backend && .venv/bin/python scripts/backfill_embeddings.py --table sellers
```

- 游标分页（每批 200 行）扫 `WHERE embedding IS NULL`，只碰 NULL 行——重跑安全
- **无文本的行写零向量占位**：`name`/`category`/`business_name` 全空的卖家构造不出文本，永远回填不了，但不处理的话每次重跑都重复扫到它们。写零向量让它退出 NULL 集合（零向量与任何向量的余弦距离是 NaN，PG 排序时排在最后，不会污染正常结果）
- 批次失败打印行号后退出（而不是死循环重试同一批）

**3. 查询时实时生成（查询侧）。** 每次检索 embed 一条查询文本（单条，\~50ms），结果不缓存——查询文本千变万化，缓存命中率低，不值得引缓存失效逻辑。

## 五、embedding 客户端的实现细节

单文件 `app/core/embedding.py`，三个值得记录的决策：

**用 httpx 直调，不引 openai SDK / langchain embedding 抽象。** 项目已有 `with_retry`（`docs/error-retry-classification.md`）的瞬时/永久分类体系，httpx 的 `HTTPStatusError` 天然兼容；openai SDK 为了一个端点引入太重，langchain 的 Embeddings 抽象绑定了它的重试体系，和项目自己的 retry 缝不起来。

**状态码手动映射进重试分类。** `with_retry` 的 classify 只认异常对象，所以 4xx/5xx 响应要手动转成对应异常：

```python
if resp.status_code >= 400:
    if resp.status_code == 429 or resp.status_code >= 500:
        raise TransientError(..., rate_limited=(resp.status_code == 429))  # 429 指数退避，5xx 线性
    raise PermanentError(...)                                              # 其余 4xx 快速失败
```

**按 index 对齐响应。** OpenAI 兼容 embeddings 接口的 `data` 数组带 `index` 字段，规范不保证顺序。`sorted(data, key=lambda d: d["index"])` 一行防御，换来「返回顺序永远等于输入顺序」的简单心智模型——回填脚本和采集落库都依赖这个假设做 zip 对齐。

**分批。** SiliconFlow 单请求上限 64 条，`embed_texts` 内部按 64 切片多次调用后拼接，调用方完全无感。

## 六、测试：三层各自测什么

遵循项目的 unit / integration 分层（`docs/testing-and-eval.md`），新增三个测试文件：

**`tests/unit/test_embedding.py`（8 用例，mock httpx，不连网）**

- 空输入返回 `[]`；70 条分两批（64 + 6）
- **响应乱序时按 index 对齐**：mock 故意 reverse 返回顺序，断言输出仍等于输入顺序
- 5xx → 重试一次后成功；429 → `TransientError(rate_limited=True)`（指数退避路径）；401 → `PermanentError` 且只调一次（快速失败）
- 文本构造函数：None 字段跳过、查询侧格式

**`tests/unit/test_query_db_semantic.py`（2 用例，mock embed\_texts + mock session）**

- 语义路径：编译 SQL 断言含 `<=>` 操作符（`cosine_distance` 的编译产物）、`embedding IS NOT NULL`、保留 `user_acquired_sellers` 排除子查询、**不含 LIKE**
- 降级路径：embed 抛异常 → SQL 回到 `lower(category) LIKE` + `total_feedback DESC`、不含 `<=>`

这里测的是**查询构造**而不是查询执行——把 SQL 编译成字符串做断言，不连 DB、毫秒级，和 `test_skills.py` 的既有模式一致。有个小坑：SQLAlchemy 把 `Seller.embedding.cosine_distance(emb)` 编译成 `embedding <=> '[0.1, 0.1, ...]'`，断言要找 `<=>` 而不是函数名 `cosine_distance`。

**`tests/integration/test_semantic_search.py`（连真实 PG + pgvector）**

- fixture 里 `CREATE EXTENSION IF NOT EXISTS vector`（conftest 的建表流程不覆盖扩展）
- 插 3 个固定向量的卖家（first 分量 0.9 / 0.5 / 0.1，其余全零），查 `[1, 0, ...]` 断言按余弦距离升序召回
- 同一组数据走 `semantic_search_sellers` 工具（monkeypatch `embed_texts`），断言工具层返回顺序一致
- 前缀清理模式（`semtest_` 前缀 + 跑完 delete），沿用项目集成测试惯例

**CI 不受影响**：ci.yml 只跑 `tests/unit/`，pgvector 相关全在 integration（本地跑）。

## 七、部署清单

按顺序：

```bash
# 1. 换镜像重建 PG（数据卷兼容，业务数据不丢）
docker compose up -d postgres          # pgvector/pgvector:pg16

# 2. 迁移（加扩展 + 列 + 索引）
cd backend
DATABASE_URL_SYNC="postgresql+psycopg2://acquisition:acquisition_dev@localhost:5433/acquisition" \
  .venv/bin/python -m alembic upgrade head

# 3. 配 key（backend/.env；生产同步进 compose env）
echo "EMBEDDING_API_KEY=sk-xxx" >> .env

# 4. 回填存量（幂等，可重跑）
.venv/bin/python scripts/backfill_embeddings.py --table sellers
.venv/bin/python scripts/backfill_embeddings.py --table products   # products 检索暂未上线，可先不跑

# 5. 验证
docker exec acquisition-pg psql -U acquisition -d acquisition \
  -c "SELECT count(*) FROM sellers WHERE embedding IS NULL"   # 应为 0（无文本行为零向量）
```

生产部署注意：`deploy.sh` 的健康检查不感知 embedding 状态；回填在生产低峰跑（112 行秒级完成，无压力）。生产 compose 的 PG 镜像也要同步换（`docker-compose.prod.yml` 已改），重建容器即可、数据卷不动。

## 八、验证效果

- **功能验证**：跑一次「找宠物用品卖家」获客流程，观察日志——`[query_db]` 不再出现「降级 LIKE 匹配」warning 即走了语义排序；库存复用数（`复用 N / 目标 M`）应显著高于改造前（此前中文品类几乎必然 0 命中触发采集）。
- **query 场景**：问「帮我找做高端厨具的卖家」，确认 ReAct 调了 `semantic_search_sellers` 而不是硬套 `filter_my_sellers`。
- **回归**：全量 pytest 115 passed（unit 65 + integration 50）。

## 九、遗留与后续

- **products 检索未接**：embedding 列和回填脚本都支持 products，但 query\_db / 工具层还没用「按商品找卖家」的检索路径（先聚合到卖家再算距离）。等「从商品语义反查卖家」的需求出现再加，避免投机设计。
- **无文本行写零向量**是个妥协——更干净的做法是 nullable 标记 + 查询排除，但为零星几行增加复杂度不值。零向量在余弦距离里是 NaN、排序永远垫底，行为正确。
- **召回质量没有量化评估**：语义检索的效果（改了之后库存复用率提升了多少）目前只能看日志观测。如果后续要量化，可以仿 `tests/agent_eval/` 的模式做一组「查询词 → 期望命中卖家」的 golden set，用真实 embedding 跑召回率。

***

**改动文件清单**：

| 文件                                                                                                                       | 改动                                                                                                      |
| ------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------- |
| `backend/app/core/config.py`                                                                                             | +4 个 embedding 配置项                                                                                      |
| `backend/app/core/embedding.py`                                                                                          | 新建：embed\_texts（httpx + with\_retry + 分批 + index 对齐）、seller\_text / product\_text / seller\_query\_text |
| `backend/app/models/seller.py` / `product.py`                                                                            | +embedding `Vector(1024)` 列                                                                             |
| `backend/alembic/versions/c5d6e7f8a9b0_add_embedding_columns.py`                                                         | 扩展 + 列 + HNSW 索引                                                                                        |
| `backend/app/agent/v2/nodes.py`                                                                                          | query\_db 语义排序（降级 LIKE）；call\_actors 落库同步生成 embedding                                                   |
| `backend/app/agent/skills.py`                                                                                            | +semantic\_search\_sellers 工具，注册进 ALL\_TOOLS                                                            |
| `backend/scripts/backfill_embeddings.py`                                                                                 | 新建：存量回填（幂等 + 零向量占位）                                                                                     |
| `docker-compose.yml` / `docker-compose.prod.yml`                                                                         | postgres:16-alpine → pgvector/pgvector:pg16                                                             |
| `backend/requirements.txt`                                                                                               | +pgvector                                                                                               |
| `backend/tests/unit/test_embedding.py` / `test_query_db_semantic.py`、`backend/tests/integration/test_semantic_search.py` | 新建测试                                                                                                    |

