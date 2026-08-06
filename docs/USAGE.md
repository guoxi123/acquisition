# 亚马逊卖家获客 Agent — 使用文档

服务中小货代（freight forwarder）：**输入「品类 + 市场」，Agent 自动发现潜在亚马逊卖家、抓取公开画像、用 LLM 评分、输出带分线索**。支持异步执行、线索看板、CSV 导出。

> **当前版本：W1 + W2 + W3 完成。** 完整链路（自动发现 → 评分 → 入库）+ 异步 + 看板 + 导出就绪。剩余 W4：评分调优、限速/成本统计、鉴权。

---

## 1. 它能做什么

- **自动发现**：输入品类+市场 → 自动搜索并提取第三方卖家（过滤 Amazon 自营）
- **抓取画像 + 评分**：每个卖家的业务信息 + 产品数据 → LLM 按 5 维度评分
- **异步执行**：`/run` 立即返回 job_id，worker 后台执行，不阻塞
- **线索看板**：筛选（档位）/ 排序 / 详情（含评分证据）
- **CSV 导出**：一键导出线索
- **配额控制**：`max_sellers` 限制每次评分的卖家数（控成本）

## 2. 前置条件

| 依赖 | 版本 |
|---|---|
| Python | ≥ 3.12（uv 管理） |
| uv | ≥ 0.11 |
| Node.js | ≥ 22 + pnpm ≥ 10 |
| Docker | ≥ 29 |

**外部账号**：Apify token（https://apify.com）、DeepSeek key（https://platform.deepseek.com）。

## 3. 安装

```bash
docker compose up -d            # postgres(5433) + redis(6379)
cd backend
cp ../.env.example .env         # 填 token（见第 4 节）
uv sync
uv run alembic upgrade head
cd ../frontend && pnpm install
```

> postgres 用 **5433** 端口（避开主机 5432 的本地 PostgreSQL）。

## 4. 配置（backend/.env）

```ini
DATABASE_URL=postgresql+asyncpg://acquisition:acquisition_dev@localhost:5433/acquisition
DATABASE_URL_SYNC=postgresql+psycopg2://acquisition:acquisition_dev@localhost:5433/acquisition
REDIS_URL=redis://localhost:6379/0

APIFY_API_TOKEN=你的_apify_token

# LLM（OpenAI 兼容，默认 DeepSeek）
LLM_API_KEY=你的_deepseek_key
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat

APP_ENV=development
CORS_ORIGINS=http://localhost:3000
```

## 5. 启动（3 个进程）

```bash
docker compose up -d
cd backend && uv run arq app.tasks.worker.WorkerSettings   # ① 异步任务 worker
cd backend && uv run uvicorn app.main:app --reload          # ② API  http://localhost:8000
cd frontend && pnpm dev                                     # ③ 前端 http://localhost:3000
```

- API 文档：http://localhost:8000/docs
- **worker 必须单独起**，否则 `/run` 的任务不会执行（campaign 会卡在 running）。

## 6. 使用流程（前端，最简单）

1. 打开 http://localhost:3000，用表单创建任务（名称 + 品类 + 市场，配额默认 5）。
2. 在任务列表点该任务的 **「运行」** 按钮 → 状态变为 `running…`，前端自动轮询。
3. 等待状态变为 `done`（约 1–4 分钟，取决于配额）。
4. 点任务名进 **线索看板**（`/campaigns/{id}`）：按档位筛选、按分排序、点「下载 CSV」导出。

### 纯 API 用法

```bash
# 创建
curl -X POST http://localhost:8000/campaigns -H "Content-Type: application/json" \
  -d '{"name":"户外家具","category":"outdoor furniture","market":"US","max_sellers":10}'

# 异步运行（立即返回 job_id）
curl -X POST http://localhost:8000/campaigns/<id>/run
# → {"job_id":"...","status":"running"}

# 轮询状态
curl http://localhost:8000/campaigns/<id>     # 看 status 字段

# 看线索
curl http://localhost:8000/campaigns/<id>/leads?tier=recommend&order=desc
```

## 7. API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查 |
| POST | `/campaigns` | 创建任务（category + market + max_sellers） |
| GET | `/campaigns` / `/{id}` | 列表 / 详情（含 status） |
| POST | `/campaigns/{id}/run` | **异步**触发，返回 job_id |
| GET | `/campaigns/{id}/leads` | 线索列表（`?tier=` `&order=desc\|asc`） |
| GET | `/leads/{id}` | 线索详情（signals + contacts） |
| GET | `/campaigns/{id}/leads/export` | CSV 导出 |

## 8. 评分模型

5 维度（0–100），**后端按固定权重加权**（不采用模型自算总分）：

| 维度 | 权重 |
|---|---|
| scale（规模/货量） | 35% |
| category_match（品类匹配） | 25% |
| shipping（发货特征） | 20% |
| reach（可触达性） | 15% |
| growth（增长信号） | 5% |

档位：`>=70` recommend / `40–70` watch / `<40` skip。各维度分值 + 证据存 `lead_signal`。

## 9. 诊断脚本（backend/scripts/）

| 脚本 | 用途 | 费用 |
|---|---|---|
| `smoke_agent.py` | 图编译 + save_node 入库（无需 token） | 否 |
| `test_apify.py` | Apify 连通 | 是（少量） |
| `test_glm.py` | LLM 连通 + 结构化输出 | 是（少量） |
| `run_e2e.py` | 完整自动发现链路 | 是 |

## 10. 当前限制

1. **worker 需手动起**（第 5 节进程①），否则任务不执行。
2. **配额**：`max_sellers` 控制评分卖家数；junglee 搜索页抓取量固定（~100–300 产品），去重后约 20–60 卖家，按 `max_sellers` 截取。
3. **LLM**：默认 DeepSeek；换别的 OpenAI 兼容模型只改 `.env` 的 `LLM_*`。
4. **评分权重**是 MVP 拍的，改 `backend/app/agent/schemas.py` 的 `SIGNAL_DIMENSIONS`，W4 用人工标注回归调优。
5. **无鉴权**：API 无 auth，W4 加。

## 11. 常见问题

**Q: `/run` 后 campaign 一直 running？** A: worker 没起。`cd backend && uv run arq app.tasks.worker.WorkerSettings`。

**Q: `role "acquisition" does not exist`？** A: 主机 5432 被占，确认用 5433。

**Q: LLM 报 401/余额？** A: 检查 `LLM_API_KEY`；DeepSeek base_url 若 404 试去掉 `/v1`。

**Q: 发现的卖家少？** A: jungle 抓搜索页，换更具体的 `category` 关键词，或调大 `max_sellers`。

## 12. 架构

```
[Next.js :3000]  ──HTTP──▶  [FastAPI :8000]  ──enqueue──▶  [Arq worker]
  看板/创建/run                   │ campaigns/leads API            │ run_campaign_task
                                  │                          ▼
                                  │            [LangGraph] discover→enrich→extract→score→save
                                  │                │  Apify junglee │ DeepSeek
                                  ▼                ▼
                          [PostgreSQL :5433]  [Redis :6379]
```

**技术栈**：FastAPI + SQLAlchemy(async) + Alembic + LangGraph + langchain-openai(DeepSeek) + apify-client + Arq + PostgreSQL + Next.js 16。

---

开发计划 [PLAN.md](../PLAN.md)，快速开始 [README.md](../README.md)。
