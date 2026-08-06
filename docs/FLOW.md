# 系统流程：从输入到输出

完整追踪一次获客任务：用户输入 → 异步触发 → LangGraph 五节点流转 → 带分线索入库 → 看板展示。每一步的**判断逻辑**都标出。

---

## 概览

```
用户输入(品类+市场+配额)
  │ POST /campaigns/{id}/run
  ▼
[Arq worker] run_campaign_task
  │
  ▼
[LangGraph 图]
 discover ──▶ enrich ──▶ extract ──▶ score ──▶ save
 (发现卖家)  (组织数据) (LLM提取画像) (LLM打分) (重算总分+入库)
  │
  ▼
lead / lead_signal / contact 表
  │
  ▼
线索看板（筛选/排序/详情）+ CSV 导出
```

---

## 1. 输入

**创建任务** `POST /campaigns`：

| 字段 | 说明 | 例 |
|---|---|---|
| `category` | 目标品类（amazon 搜索关键词） | `outdoor furniture` |
| `market` | 目标市场（映射到 amazon 域名） | `US` → amazon.com |
| `max_sellers` | 评分卖家数上限（控成本） | 默认 5 |
| `seller_ids` | 可选：手动指定卖家 ID（覆盖自动发现） | 一般留空 |

**触发运行** `POST /campaigns/{id}/run` → 立即返回 `{job_id, status: "running"}`（**异步，不阻塞**）：
- 建 `job` 记录（status=pending）
- `campaign.status = running`
- 把 `run_campaign_task` 推入 Redis 队列（Arq）
- 返回 `job_id`

---

## 2. worker 执行

独立进程 `arq app.tasks.worker.WorkerSettings` 从 Redis 取任务，执行 `run_campaign_task(campaign_id)`：
- `job.status = running`
- 读 campaign（category / market / seller_ids / max_sellers）
- 调 `run_agent(...)` → `build_graph().ainvoke(initial_state)` 启动 LangGraph 图

---

## 3. LangGraph 五节点

状态对象 `AgentState` 在节点间流转：`candidates → enriched → extracted → leads`。

### ① discover — 自动发现卖家

**输入**：category, market, max_sellers

**步骤**：
1. 构造 amazon 搜索 URL：`https://{域名}/s?k={category}`（US→amazon.com, UK→amazon.co.uk, DE→amazon.de, JP→amazon.co.jp）
2. 调 Apify `junglee/amazon-crawler`，input `{country, categoryOrProductUrls: [{url}]}`
3. junglee 抓搜索结果页 → 返回 N 个产品，每个含：
   - `seller{ id, name, businessName, address, phone }`
   - `brand`、`reviewsCount`、`price`、`stars`、`bestsellerRanks`、`visitStoreLink`

**判断点**（3 个过滤器）：
| 判断 | 动作 |
|---|---|
| `seller.id` 为空？ | **跳过**（Amazon 自营 / FBA 代发，非第三方卖家客户） |
| `seller.id` 已见过？ | **去重**（同一卖家的多个产品聚合成一条） |
| 去重后数量 > `max_sellers`？ | **截取前 max_sellers 个** |

**输出**：`candidates` —— 每条含 `{seller_id, brand, seller_info, products[], storefront_url}`

### ② enrich — 组织画像原料

**输入**：candidates

**步骤**：discover 已用 junglee 抓全数据，enrich 直接组织（不再调 actor）：
- `profile_raw` = seller_info（业务名 / 地址 / 电话）
- `products_raw` = 该卖家的产品聚合（评价数 / 价格 / 品类排名）

**判断点**：无（纯转发；W4 可选在此调 amazon-sellers-scraper 补 feedback 评级）

**输出**：`enriched` —— 每条含 `{profile_raw, products_raw, brand, storefront_url}`

### ③ extract — LLM 结构化提取

**输入**：enriched（每个卖家）

**步骤**：对每个卖家调 DeepSeek：
- prompt = `EXTRACT_SYSTEM`（提取画像，不编造）+ `EXTRACT_USER`（品类 / 市场 / 候选 / 原始画像 / 产品数据）
- **结构化方式**（`_structured_invoke`）：
  1. 把 `SellerProfile` 的 JSON Schema 放进 prompt 作格式参考
  2. 要求"只输出 JSON 实例"
  3. `_extract_json` 用正则从返回里提取 JSON（处理 markdown 代码块 / 前后文字）
  4. Pydantic `SellerProfile` 校验

> 不用 function_calling——曾实测 GLM 会把 tool schema 当答案复述；纯 prompt + 提取对所有 OpenAI 兼容模型都稳。

**判断点**：
| 判断 | 动作 |
|---|---|
| JSON 提取 + Pydantic 校验通过？ | 用 |
| 失败？ | **重试 1 次** |
| 仍失败？ | **跳过该卖家**（记日志，不影响其他） |

**输出**：`extracted` —— 每条含结构化 `profile{ brand, company, category_main, rating, review_count, sku_count, is_fba, business_email, website, notes }`

### ④ score — LLM 打分

**输入**：extracted（每个卖家的画像）

**步骤**：对每个卖家调 DeepSeek：
- prompt = `SCORE_SYSTEM`（5 维度定义 + 权重 + **必须覆盖全部 5 维度**）+ `SCORE_USER`（品类 / 画像）
- DeepSeek 对每个维度独立打 0–100 分，附 `detail` 推理
- 结构化方式同 extract（纯 prompt + JSON 提取 + `ScoreResult` 校验）

**判断点**：同 extract（重试 / 跳过）

**输出**：`leads` —— 每条含 `score{ signals[5], total_score, tier, reasoning }`

> ⚠️ 这里的 `total_score` 和 `tier` 是**模型给的，下一步后端会丢弃重算**。

### ⑤ save — 重算总分 + 档位 + 入库 ★ 打分核心

**输入**：leads（含模型评分）

**步骤**（对每个 lead）：

**A. 后端重算总分**（不信任模型的 total）：
```
total = round(
    scale×0.35 + category_match×0.25 + shipping×0.20 + reach×0.15 + growth×0.05
, 1)
```
取 `signals` 里各维度的分，按**固定权重**加权。

**B. 档位判定**（按重算后的 total）：
| total | tier |
|---|---|
| `>= 70` | `recommend`（推荐跟进） |
| `40 – 69.9` | `watch`（观察） |
| `< 40` | `skip`（暂缓） |

**C. 入库**（一个事务，全成功才 commit）：
- `lead` 表：brand, company, storefront_url, market, profile(JSON), **total_score(重算)**, **score_tier(判定)**, status=scored
- `lead_signal` 表：5 条（每维度：dimension, score, evidence_url, detail）—— 评分证据可追溯
- `contact` 表：`business_email`→email，`website`→website（如有）

**输出**：leads 摘要（brand / total / tier / url）

---

## 4. 任务收尾

- 成功：`campaign.status = done`，`job.status = done`，`job.result = {leads: N}`，`finished_at = now`
- 失败（任一未捕获异常）：`campaign.status = failed`，`job.status = failed`，`job.error = 异常信息`

---

## 5. 输出与展示

**数据落地**（PostgreSQL）：
| 表 | 内容 |
|---|---|
| `lead` | 一条线索：卖家 + 总分 + 档位 + 画像 + storefront_url |
| `lead_signal` | 该线索的 5 维度分值 + 证据 + 推理 |
| `contact` | 公开联系方式（email / website） |

**前端看板** `/campaigns/{id}`：
- 列表（品牌 / 公司 / 总分 / 档位 / 店铺链接），按分降序
- 筛选：全部 / recommend / watch / skip
- 排序：分高→低 / 分低→高
- 详情：`GET /leads/{id}` 返回画像 + signals + contacts
- 导出：`GET /campaigns/{id}/leads/export` → CSV

**轮询**：首页 `CampaignActions` 每 4s 查 `GET /campaigns/{id}.status`，`running → done/failed` 时停。

---

## 打分模型详解

### 5 个维度（信号 → 分值 0–100，由 DeepSeek 基于画像 + 产品数据推理）

| 维度 | 看什么信号 | 权重 |
|---|---|---|
| `scale` 规模/货量潜力 | 评价数、SKU 数、品类排名、销量 | **35%** |
| `category_match` 品类匹配 | 是否命中货代擅长品类、价格段是否适合整柜/拼箱 | **25%** |
| `shipping` 发货特征 | 是否 FBA、产品体积、发货频次潜力 | **20%** |
| `reach` 可触达性 | 有无公开邮箱/官网/电话 | **15%** |
| `growth` 增长信号 | 评分趋势、新品上架、口碑 | **5%** |

### 为什么后端重算总分（不用模型的）

1. **一致性**：模型偶发把分简单相加（如 5 维度各 80 → total 400），权重固定保证可比。
2. **可调**：改 `backend/app/agent/schemas.py` 的 `SIGNAL_DIMENSIONS` 即可调权重，不用重训模型。
3. **档位可靠**：tier 由重算 total 确定性判定，不依赖模型自报。

### 调优路径（W4）

权重目前是 MVP 拍的。W4 用 50 条人工标注样本回归：对比模型分档与人工判断「值得跟进」的一致性，反推最优权重，改 `SIGNAL_DIMENSIONS`。

---

## 异常处理汇总

| 步骤 | 失败情形 | 处理 |
|---|---|---|
| discover | Apify 调用失败 | `errors` 记录，candidates 空，流程继续（leads 空） |
| extract / score | LLM 解析失败 | 重试 1 次 → 跳过该卖家，其他继续 |
| save | 入库异常 | 整事务回滚，该 lead 不入库 |
| worker | 未捕获异常 | campaign=failed，job.error 记录，前端轮询到 failed |
