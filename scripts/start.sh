#!/bin/bash
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

# 检查 .env
if [ ! -f backend/.env ]; then
  echo "⚠️  backend/.env 不存在，从模板创建..."
  cp .env.example backend/.env
  echo "  请编辑 backend/.env 填入 APIFY_API_TOKEN / LLM_API_KEY 等凭证后重新运行"
  exit 1
fi

echo "=== 1/5 启动基础设施（PostgreSQL + Redis）==="
docker compose up -d

echo ""
echo "=== 2/5 等待 PostgreSQL 就绪 ==="
for i in $(seq 1 30); do
  if docker exec acquisition-pg pg_isready -U acquisition >/dev/null 2>&1; then
    echo "✅ PostgreSQL 就绪"
    break
  fi
  echo "  等待... ($i)"
  sleep 2
done

echo ""
echo "=== 3/5 后端依赖 + 数据库迁移 ==="
cd backend
uv sync
uv run alembic upgrade head
echo "✅ 迁移完成（所有表已建）"

echo ""
echo "=== 4/5 启动后端（lifespan 自动创建超管 guoxi/guoxi）==="
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

# 等后端起来
sleep 4
if curl -s http://localhost:8000/health | grep -q "ok"; then
  echo "✅ 后端就绪 http://localhost:8000"
else
  echo "⚠️  后端可能未正常启动，检查日志：tail -f /tmp/acq_uv.log"
fi

echo ""
echo "=== 5/5 启动前端 ==="
cd "$PROJECT_DIR/frontend"
pnpm install
pnpm dev &
FRONTEND_PID=$!
sleep 3
echo "✅ 前端就绪 http://localhost:3000"

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  🚀 系统已启动"
echo "  前端:   http://localhost:3000"
echo "  后端:   http://localhost:8000"
echo "  API文档: http://localhost:8000/docs"
echo "  超管账号: guoxi / guoxi"
echo "═══════════════════════════════════════════════════════"

# Ctrl+C 退出时清理
trap "
  echo ''
  echo '正在停止...'
  kill $BACKEND_PID $FRONTEND_PID 2>/dev/null
  pkill -f 'uvicorn app.main' 2>/dev/null
  pkill -f 'next dev' 2>/dev/null
  docker compose down 2>/dev/null
  echo '已停止'
" EXIT INT TERM

wait
