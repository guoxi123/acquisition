#!/bin/bash
set -e

echo "=== 数据库迁移 ==="
cd /app
alembic upgrade head

echo "=== 启动后端（4 workers）==="
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
