# 亚马逊卖家获客 Agent

服务中小货代，从公开互联网挖掘潜在亚马逊卖家客户、结构化画像、按"有效客户"维度评分排序。
详见 [PLAN.md](./PLAN.md)。**完整使用文档见 [docs/USAGE.md](./docs/USAGE.md)。**

## 技术栈

- **前端**：Next.js 14 + TypeScript + Tailwind
- **后端**：FastAPI + SQLAlchemy 2.0 (async) + Alembic
- **Agent**：Anthropic SDK tool-use
- **抓取**：Apify Actors（google-search / amazon-sellers / amazon-seller-products）
- **队列**：Redis + Arq
- **存储**：PostgreSQL

## 快速开始

```bash
# 1. 启动基础设施（需先启动 Docker Desktop）
docker compose up -d

# 2. 后端（API + 异步 worker）
cd backend
cp ../.env.example .env          # 填入 APIFY_API_TOKEN / LLM_API_KEY
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload  # API http://localhost:8000
uv run arq app.tasks.worker.WorkerSettings  # 异步任务 worker（另开终端）

# 3. 前端
cd ../frontend
pnpm install
pnpm dev                             # http://localhost:3000
```

## 当前进度

- [x] W1 基础设施 + 前后端骨架 + campaigns CRUD
- [ ] W2 Agent 主链（发现→抓取→提取→评分）
- [ ] W3 批量 + 看板 + 导出
- [ ] W4 调优 + 合规护栏 + 成本统计
