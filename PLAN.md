# 亚马逊卖家获客 Agent — 开发计划

> 服务对象：中小货代（freight forwarder）。
> 核心价值：从公开互联网自动挖掘潜在亚马逊卖家客户、提取结构化画像、按"有效客户"维度评分排序，输出可跟进的线索清单。
> MVP 聚焦：**线索挖掘 + 评分**（不含自动触达 / CRM 跟进）。

---

## 1. 定位与合规红线

**产品定位**：货代输入「目标品类 + 目标市场」，Agent 自动发现潜在亚马逊卖家 → 抓取公开信息 → 结构化画像 → 打分排序 → 线索看板 + 导出。

**合规红线（产品长期可跑的前提）**：

- **不爬亚马逊站内受保护数据**。只用卖家公开店铺页 / 品牌名 / 公开商品列表；遵守 robots.txt 与频率限制。亚马逊 ToS 禁止自动化抓取，违规会导致 IP 被封甚至法律风险。
- **不存储个人敏感信息**。联系方式只保留企业公开的商务对接信息（官网公开邮箱、企业座机），不存自然人隐私数据（PIPL / GDPR）。
- **海关数据只用合法渠道**（公开或付费授权 API），不碰灰色提单数据。
- 当前只做「挖掘 + 评分」，不做自动触达外呼，规避骚扰风险；未来若加触达必须做 opt-out。

---

## 2. MVP 范围与成功标准

**范围内（2–4 周）**：

- 创建获客任务（品类 + 市场 + 约束）
- Agent 自动发现 → 抓取 → 结构化 → 评分 → 去重入库
- 线索看板（列表 / 筛选 / 排序 / 详情）+ 导出 CSV

**范围外**：自动触达、CRM 跟进、计费、多租户、海关数据深度接入（留接口）。

**成功标准（可验证）**：

1. 给定品类（如「户外家具」+「美国站」），10 分钟内产出 ≥30 条带评分线索。
2. 抽样 50 条，评分与人工判断的「值得跟进」一致性 ≥70%（Top 10 命中率优先）。
3. 单条线索综合成本（Apify + LLM）可预估、可控制。
4. 全程数据来源可追溯（每条线索能点回原始公开 URL）。

---

## 3. 系统架构

```
[Next.js 前端] ──HTTP──▶ [FastAPI] ──▶ [Redis 任务队列: Arq]
   看板/任务/导出        │  API + Agent 编排        │ 异步: 发现/抓取/评分
                        │                          ▼
                        │            [Anthropic SDK tool-use 循环] → [LangGraph StateGraph + 智谱 GLM-5.1]
                        │            节点: discover / fetch / extract / score
                        │                          │
                        ▼                          ▼
                   [PostgreSQL] ◀── 结构化线索 + 评分信号 + 来源
                   （+ pgvector 去重，可选）
                                ▲
                                │ 调用
                   [Apify Actors] ← google-search / amazon-sellers / amazon-seller-products
```

**分层理由**：搜索与 LLM 调用是慢且不可靠的 I/O，必须放异步队列；前端只管展示与发起任务；Agent 编排集中在 FastAPI 单进程，便于调试，MVP 不拆独立 agent 微服务。抓取全部交给 Apify Actors，省掉自维护代理 / 反爬 / Playwright 的工作。

---

## 4. Agent 工作流 + 评分模型

### 工作流（5 步，LangGraph StateGraph 编排，每步一个节点）

| 步骤 | 动作 | 用的工具 | 产出 |
|---|---|---|---|
| 1. 发现 | 按品类+市场生成搜索策略，调搜索 | `discover_sellers` | 候选卖家 URL / 品牌名 |
| 2. 画像 | 抓卖家公开页（店铺 / 评级） | `fetch_seller_profile` | 卖家画像 |
| 2b. 货量 | 抓卖家产品列表 | `fetch_seller_products` | SKU 数 / 品类 / 估算销量 |
| 2c. 官网 | 抓官网 about / 工商页 | `fetch_page`（httpx 轻量） | 公司信息 + 商务联系方式 |
| 3. 提取 | LLM 把非结构化页面 → 统一画像 schema | `extract_seller_profile` | 卖家画像 JSON |
| 4. 评分 | 规则 + LLM 综合打分 | `score_lead` | 各维度分 + 总分 + 证据 |
| 5. 去重入库 | 按品牌 / 域名 / 公司主体去重 | `dedupe_and_save` | 入库线索 |

**编排选择**：用 **LangGraph StateGraph** 编排，节点 = discover / enrich / extract / score / dedupe，状态在图里显式流转（便于观测/重试/分支）。**LLM 用智谱 GLM-5.1**（OpenAI 兼容接口，经 `langchain-openai` 的 `ChatOpenAI` 指向 `https://open.bigmodel.cn/api/paas/v4/` 接入），结构化提取与评分用其 function calling。

### 评分模型（"有效客户"= 货量潜力 × 匹配度 × 可触达性）

| 维度 | 信号来源 | 权重(MVP) |
|---|---|---|
| 规模 / 货量潜力 | 评价数、BSR、SKU 数、估算月销 | 35% |
| 品类匹配 | 是否命中货代擅长品类 / 航线 | 25% |
| 发货特征 | 是否 FBA、是否中国 / 目标国发货 | 20% |
| 可触达性 | 是否有公开商务联系方式 | 15% |
| 增长信号 | 新品上架频率、评分趋势 | 5% |

总分 0–100，输出分「推荐跟进 / 观察 / 暂缓」三档。权重 MVP 先拍，靠人工标注样本回归调优。

---

## 5. 数据源与合规（Apify 抓取层）

| 数据源 | 用途 | MVP | 方式 |
|---|---|---|---|
| Google Search（Apify） | 发现卖家 | ✅ | `apify/google-search-scraper` |
| 亚马逊卖家信息（Apify） | 画像 + 评级 | ✅ | `automation-lab/amazon-sellers-scraper` |
| 亚马逊卖家产品（Apify） | 货量 / 品类信号 | ✅ | `easyparser/amazon-seller-products` |
| 官网 about / 工商页 | 公司信息 + 联系方式 | ✅ | httpx 轻量抓取（公开页） |
| 企业工商 API / 海关 | 主体核验 / 发货特征 | ⚠️ 接口预留 | `DataSourceProvider` 抽象 |

**合规**：Apify 代处理代理轮换 / 反爬 / 限速，被封风险显著下降；但亚马逊 ToS 边界不变（仍抓公开页，执行方变 Apify），需遵守各 Actor 使用条款与亚马逊数据使用限制。

---

## 6. 技术栈与依赖

**前端**：Next.js 14 (App Router) + TypeScript + Tailwind + shadcn/ui + TanStack Query
**后端**：FastAPI + Pydantic v2 + SQLAlchemy 2.0 (async) + Alembic
**Agent**：**LangGraph**（StateGraph 编排）+ `langchain-openai` 的 ChatOpenAI 指向**智谱 GLM-5.1**（OpenAI 兼容，`https://open.bigmodel.cn/api/paas/v4/`，模型 `glm-5.1`）；提取走快模型、评分推理走强模型以控成本
**队列**：Redis + **Arq**（比 Celery 轻，async 原生）
**存储**：PostgreSQL（+ pgvector 扩展做去重，不引入独立向量库）
**抓取**：**`apify-client`**（主力，调 3 个 Actor）+ `httpx` / `selectolax`（官网 / 工商轻量页）；Playwright 仅在 Apify 覆盖不到且需 JS 渲染时备用

---

## 7. 目录结构

```
acquisition/
├── CLAUDE.md
├── PLAN.md
├── docker-compose.yml           # postgres + redis
├── .env.example
├── backend/
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py              # FastAPI 入口
│   │   ├── api/                 # campaigns / leads / jobs / export
│   │   ├── agent/               # orchestrator / tools / prompts / schemas
│   │   ├── providers/           # 数据源抽象 + Apify adapter
│   │   │   ├── base.py
│   │   │   ├── apify_provider.py
│   │   │   ├── http_provider.py
│   │   │   └── adapters/        # 每个 Actor 一个 adapter，隔离 schema 变更
│   │   ├── models/              # SQLAlchemy 模型
│   │   ├── schemas/             # Pydantic DTO
│   │   ├── tasks/               # Arq 异步任务
│   │   ├── core/                # config / db / 限速 / 合规护栏
│   │   └── tests/
│   ├── alembic/
│   └── scripts/test_apify.py    # Apify 连通性测试
└── frontend/
    ├── package.json
    ├── app/                     # campaigns / leads / leads/[id]
    ├── components/
    └── lib/api.ts
```

**核心表**：`campaign`（获客任务）、`lead`（线索 + 画像 JSON + 总分）、`lead_signal`（各维度分值 + 证据 URL）、`contact`（企业公开联系方式）、`job`（异步任务状态）。

---

## 8. 分阶段实施

| 周 | 交付 | 验证 |
|---|---|---|
| W1 基础设施 | 仓库结构、docker-compose、DB 模型 + 迁移、FastAPI 基础路由、前端壳、Apify provider 骨架 | `docker compose up` 起来，能创建 campaign 并落库 |
| W2 Agent 主链 | LangGraph 编排跑通：发现→抓取→提取→单维度评分；单条线索端到端 | 给定品类，跑出 1 条结构化带分线索，来源可点回 |
| W3 批量+看板 | Arq 异步批量、完整评分模型、去重、看板 + 筛选 + CSV 导出 | 10 分钟产出 ≥30 条线索，看板可用 |
| W4 调优+护栏 | 50 条人工标注回归、权重调优、限速 / 合规护栏、成本统计 | 一致性 ≥70%、成本可量化、限速生效 |

---

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| 数据源被封 | ⬇️ Apify 代处理代理 / 限速；httpx 抓取走限速 + 缓存 |
| Apify 成本超支 | 任务级预算硬上限（Actor 调用次数 / 费用阈值即停）+ 结果缓存去重 + 限并发 |
| Actor 下线 / schema 变更 | 每个 Actor 独立 adapter 隔离；选维护活跃的官方 / 高星 Actor；关键 Actor 备备选 |
| 评分不准 | 第 4 周人工标注集回归基线，权重迭代有据 |
| LLM 成本失控 | 模型分级（提取走便宜模型）、抓取结果缓存、批处理限并发 |
| 合规踩线 | `core/compliance.py` 统一护栏（robots 校验、字段白名单、限速），所有 provider 必经 |

---

## 10. 成本模型

```
单条线索成本 ≈ Apify(google-search + sellers + seller-products) + LLM(提取 + 评分)
```

MVP 控制手段：

- 任务级**预算硬上限**（达到 Actor 调用次数 / 费用阈值即停）。
- 抓取结果**按卖家去重缓存**，同一卖家不重复跑 Actor。
- 并发受限（Arq worker 数 × Apify 并发额度）。
- 记录每次 Actor 调用的结果数与计费单位，W4 汇总成单条成本报表。
