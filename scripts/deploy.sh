#!/bin/bash
set -e

# ===== 配置（按需修改）=====
SERVER_USER="root"                    # SSH 用户
SERVER_HOST="39.107.230.204"          # 服务器 IP/域名
SERVER_PATH="/opt/acquisition"        # 服务器部署目录
SSH_KEY="~/.ssh/ecs.pem"             # SSH 密钥路径
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "═══════════════════════════════════════════════════"
echo "  部署到 ${SERVER_USER}@${SERVER_HOST}:${SERVER_PATH}"
echo "═══════════════════════════════════════════════════"

# 0. 部署前测试门槛：后端全量 pytest + 前端 tsc，任一失败立即中止
echo "=== 0/4 部署前测试 ==="
echo "→ 后端 pytest（全量，含 integration）"
cd "${PROJECT_DIR}/backend"
.venv/bin/python -m pytest tests/ -q
echo "→ 前端类型检查 tsc"
cd "${PROJECT_DIR}/frontend"
pnpm exec tsc --noEmit
cd "${PROJECT_DIR}"
echo "✅ 测试全部通过"

# 1. 同步代码（排除本地开发文件，用 tar+ssh 避免远程无 rsync 的问题）
echo "=== 1/4 同步代码到服务器 ==="
cd "${PROJECT_DIR}"
COPYFILE_DISABLE=1 tar czf - \
  --exclude='.git' \
  --exclude='node_modules' \
  --exclude='.next' \
  --exclude='__pycache__' \
  --exclude='.venv' \
  --exclude='.env' \
  --exclude='*.pyc' \
  --exclude='.DS_Store' \
  --exclude='._*' \
  --exclude='logs' \
  --exclude='*.log' \
  . \
  | ssh -i ${SSH_KEY} "${SERVER_USER}@${SERVER_HOST}" "
    mkdir -p ${SERVER_PATH}
    cd ${SERVER_PATH}
    # 清理旧文件（保留 .env.prod 和 .gitignore）
    find . -maxdepth 1 ! -name '.' ! -name '.env.prod' ! -name '.gitignore' -exec rm -rf {} +
    tar xzf -
  "
cd - > /dev/null

echo "✅ 代码已同步"

# 2. .env.prod（本地有则上传，无则提示创建）
echo ""
echo "=== 2/4 配置 .env.prod ==="
if [ -f "${PROJECT_DIR}/.env.prod" ]; then
  scp -i ${SSH_KEY} "${PROJECT_DIR}/.env.prod" "${SERVER_USER}@${SERVER_HOST}:${SERVER_PATH}/.env.prod"
  echo "✅ 本地 .env.prod 已上传"
else
  echo "⚠️ 本地无 .env.prod，首次请在本地创建："
  echo "   cp .env.prod.example .env.prod && vim .env.prod"
  echo "   编辑后重跑 ./scripts/deploy.sh"
  exit 1
fi

# 3. 构建 + 启动
echo ""
echo "=== 3/4 构建 & 启动 ==="
ssh -i ${SSH_KEY} "${SERVER_USER}@${SERVER_HOST}" "
  cd ${SERVER_PATH}
  # docker compose 默认读 .env，将 .env.prod 链接为 .env
  cp .env.prod .env
  docker compose -f docker-compose.prod.yml up -d --build
  echo '等待服务启动...'
  sleep 10
"

# 4. 健康检查
echo ""
echo "=== 4/4 健康检查 ==="
ssh -i ${SSH_KEY} "${SERVER_USER}@${SERVER_HOST}" "
  cd ${SERVER_PATH}

  # 检查容器
  echo '容器状态:'
  docker compose -f docker-compose.prod.yml ps --format 'table {{.Name}}\t{{.Status}}'

  # 检查后端
  echo ''
  echo -n '后端: '
  if curl -s http://localhost:80/health | grep -q ok; then
    echo '✅ OK'
  else
    echo '❌ 未响应'
    echo '=== 后端日志 ==='
    docker compose -f docker-compose.prod.yml logs backend --tail=30
    echo '❌ 部署失败：后端未响应'
    exit 1
  fi

  # 检查超管
  echo -n '超管: '
  TOKEN=\$(curl -s -X POST http://localhost:80/api/auth/login \
    -H 'Content-Type: application/json' \
    -d '{\"username\":\"guoxi\",\"password\":\"guoxi\"}' | grep -o '\"token\"' || echo '')
  if [ -n \"\$TOKEN\" ]; then
    echo '✅ guoxi 登录成功'
  else
    echo '⚠️  超管可能未创建（检查后端日志）'
  fi
"

echo ""
echo "═══════════════════════════════════════════════════"
echo "  🚀 部署完成"
echo "  访问: http://${SERVER_HOST}"
echo "  登录: guoxi / guoxi"
echo "═══════════════════════════════════════════════════"
