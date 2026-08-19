# Docker Compose 生产部署:多服务 + 持久化 + 一键脚本

> 一个全栈项目(FastAPI + Next.js + Postgres + Redis + Nginx)要部署到一台云服务器,怎么组织?本文讲一套用 Docker Compose 的生产部署方案:多服务编排、数据/日志持久化、环境隔离、以及一个用 tar+ssh 同步代码 + 健康检查的部署脚本。

## 一、问题:全栈部署要管几样东西

全栈项目上线,至少五个服务:

- **postgres**:数据库
- **redis**:缓存/队列
- **backend**(FastAPI):API + Agent
- **frontend**(Next.js):SSR 页面
- **nginx**:反向代理(把 80 转到 frontend/backend)

要管的事:服务编排 + 依赖顺序、数据持久化(PG 数据、日志)、环境变量(生产凭证)、代码同步、启动后健康检查。

## 二、服务拓扑

```
nginx (:80)
  ├── / → frontend (:3000)
  └── /api → backend (:8000)
backend → postgres (:5432) + redis (:6379)
```

nginx 对外,frontend 和 backend 不直接暴露端口(内部网络通信)。所有服务在一个 compose 网络里,用服务名互相访问。

## 三、compose 配置要点

### 1. 依赖顺序(depends\_on + condition)

backend 依赖 pg healthy + redis started:

```yaml
backend:
  depends_on:
    postgres:
      condition: service_healthy
    redis:
      condition: service_started
```

pg 有 healthcheck(`pg_isready`),backend 等它真就绪了才起(否则 backend 启动时连不上库)。

### 2. 数据持久化(volume)

PG 数据要持久化(容器重建不丢):

```yaml
postgres:
  volumes:
    - pgdata:/var/lib/postgresql/data
volumes:
  pgdata:   # named volume,docker 管理
```

backend 日志/trace 也持久化到宿主机(bind mount,方便直接 grep):

```yaml
backend:
  volumes:
    - ./logs:/app/logs   # trace/日志落到宿主机,容器重建不丢
```

(这个 volume 是踩了坑才加的——详见同系列《多 Worker 下的"幽灵取消"》,trace 一度只在容器内,重建就丢。)

### 3. 环境变量(env\_file + environment)

敏感凭证(数据库密码、API key)放 `.env.prod`,compose 用 `env_file` 注入;非敏感的运行时配置(DB 指向容器服务名)写死在 `environment`:

```yaml
backend:
  env_file: .env.prod
  environment:
    DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}
    REDIS_URL: redis://redis:6379/0
    APP_ENV: production
```

注意 `@postgres:5432`——容器内用**服务名** postgres 连,不是 localhost。

### 4. 端口

只 nginx 对外:

```yaml
nginx:
  ports:
    - "80:80"   # 宿主机 80 → 容器 80
```

frontend/backend 不映射端口(不暴露)。

## 四、.env.prod

生产凭证集中放 `.env.prod`(不进 git,.gitignore 忽略):

```
POSTGRES_USER=acquisition
POSTGRES_PASSWORD=真实密码
POSTGRES_DB=acquisition
LLM_API_KEY=sk-...
APIFY_API_TOKEN=apify-...
ALIYUN_SMS_SIGN_NAME=xxx
...
```

compose 读 `.env.prod`(env\_file) + `environment` 里的变量,一起注入容器。部署时本地 `.env.prod` scp 到服务器。

## 五、deploy.sh:一键部署脚本

不用 CI/CD(小项目不值),写个脚本:`tar 打包代码 → ssh 传到服务器 → 解压 → docker compose up --build → 健康检查`。

```bash
# 1. 同步代码(tar+ssh,避免服务器没装 rsync)
tar czf - --exclude='.git' --exclude='node_modules' --exclude='.venv' --exclude='logs' . \
  | ssh root@server "cd /opt/acquisition && tar xzf -"

# 2. 上传 .env.prod
scp .env.prod root@server:/opt/acquisition/.env.prod

# 3. 构建 + 启动
ssh root@server "cd /opt/acquisition && cp .env.prod .env && docker compose -f docker-compose.prod.yml up -d --build"

# 4. 健康检查(等启动 + curl health + 登录测试)
ssh root@server "
  curl -s http://localhost:80/health | grep -q ok || (echo '后端未响应'; docker compose logs backend; exit 1)
"
```

几个细节:

- `--exclude='logs'`:不把本地日志传上去(日志是运行时产物)
- tar+ssh:服务器没 rsync 也能用
- 健康检查跑后端日志 tail 帮助排错
- 端口改了(80)记得脚本里的 health URL 也改

## 六、踩过的坑

1. **logs 没持久化**:backend 日志默认在容器内,容器重建丢,而且宿主机看不到排查难。加 `./logs:/app/logs` volume 解决。
2. **alembic 迁移失败导致后端重启循环**:entrypoint 跑 `alembic upgrade head`,迁移失败容器退出,docker restart 反复(详见《部署后后端疯狂重启:Alembic 版本表脏了》)。
3. **前端启动慢 → 502**:Next.js build 完才监听 3000,部署后立即访问 nginx 转发到没就绪的 frontend → 502。要么等几十秒,要么部署脚本加 frontend 健康检查。
4. **多 worker 的进程内状态**:backend `uvicorn --workers 4`,任何进程内 dict 注册表都出问题(详见《多 Worker 下的"幽灵取消"》)。

## 七、总结

全栈部署用 Compose 编排多服务,关键点:

- **拓扑**:nginx 对外,frontend/backend 内部通信,用服务名连 pg/redis
- **持久化**:PG 数据用 named volume,日志用 bind mount(落到宿主机方便排查)
- **环境**:敏感凭证 .env.prod(不进 git),运行时配置写 environment
- **部署**:tar+ssh 同步 + docker compose up --build + 健康检查脚本

> 单机部署不丢人。一台 ECS + 一个 docker-compose.prod.yml + 一个 deploy.sh,对小到中等规模的全栈项目,比上 K8s 务实得多。把"服务编排、持久化、环境隔离、部署脚本"这四样做扎实,单机也稳。

