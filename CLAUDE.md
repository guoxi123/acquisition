# CLAUDE.md（项目级）

> 本文件是项目专属指引，叠加在全局 `~/.claude/CLAUDE.md` 之上；二者冲突时以项目为准。
> 第一部分的行为准则参照 Andrej Karpathy 对 LLM 编码通病的观察，整体偏向「谨慎优先」；
> 琐碎任务（改错字、明显的一行修复）可自行判断，不必套用全套流程。

---

## 一、编码行为准则

### 1. 先想再写
**不臆测、不藏起困惑、把权衡摆到台面上。**
- 显式说出假设；不确定就问，别猜。
- 存在多种合理解读时，列出来——别默默选一个。
- 有更简单的做法就直说；该反驳就反驳。
- 看不懂的地方，停下来指明哪里困惑，再问。

### 2. 简洁优先
**用解决问题的最少代码，不做任何投机性设计。**
- 不做要求之外的功能。
- 一次性使用的代码不抽象。
- 不加未被要求的「灵活性」「可配置性」。
- 不为不可能发生的场景写错误处理。
- 200 行能压到 50 行，就重写。
- 自检：资深工程师会不会觉得这过度复杂？会，就简化。

### 3. 外科手术式改动
**只动必须动的，只清理自己制造的残留。**
- 别顺手「改进」相邻的代码、注释、格式。
- 别重构没坏的东西；贴合现有风格，哪怕你想换种写法。
- 发现无关的死代码，提一句——别擅自删。
- 你的改动让某些 import / 变量 / 函数变成孤儿，就清掉；预先存在的死代码，未要求不动。
- 检验：每一行改动都能直接追溯到用户的请求。

### 4. 目标驱动执行
**先定义成功标准，再循环到验证通过。**
把祈使句任务转成可验证目标：
- 「加校验」→「为非法输入写测试，再让测试通过」
- 「修 bug」→「写一个能复现它的测试，再让它通过」
- 「重构 X」→「前后保证测试都通过」

多步任务先给简短计划：
```
1. [步骤] → 验证：[检查]
2. [步骤] → 验证：[检查]
```
强成功标准能让你独立循环到底；弱标准（「能跑就行」）会不停返工澄清。

> 见效的标志：diff 里没有多余改动、不再因过度复杂返工、澄清发生在动手之前而非犯错之后。

---

## 二、测试纪律（强制）

**改/加任何后端功能，必须同步写或改对应的 pytest 测试（backend/tests/），改完跑全量 pytest，通过才报告完成。**

- **写代码时同步写测试**：不要"先写完所有功能最后补测试"。一个功能改完，对应的 `test_xxx.py` 也要写好。
- **改完立刻跑**：`cd backend && .venv/bin/python -m pytest tests/ -v`。全量 61+ 测试 ~10 秒，秒级反馈。
- **测试失败如实说**：不粉饰、不跳过、不改测试迁就 bug。失败 → 修代码 → 再跑 → 直到通过。
- **琐碎例外**：改错字、文案、注释、CSS 类名等不影响逻辑的改动，可跳过测试。
- **新功能模板**：纯逻辑写单元测试（monkeypatch mock 外部）；涉及 DB 写集成测试（临时用户 + 前缀 sellers + 跑完清理）。

---

## 二、项目结构

```
acquisition/
├── backend/                      FastAPI 后端（Python 3.12）
│   ├── app/
│   │   ├── agent/                Agent 核心
│   │   │   ├── v2/               主获客图（graph.py / state.py / nodes.py / intent.py）
│   │   │   ├── skills.py         query_agent 工具注册表（@tool 函数 + ALL_TOOLS）
│   │   │   ├── query_agent.py    手写 LangGraph ReAct 子图（agent ⟷ tools 循环）
│   │   │   ├── orchestrator.py   LLM 结构化输出（_structured_invoke）
│   │   │   ├── llm.py            LLM 工厂（ChatOpenAI / DeepSeek）
│   │   │   └── trace.py          轻量本地 trace（JsonlTracer callback）
│   │   ├── api/                  FastAPI 路由
│   │   │   ├── chat_stream.py    流式对话（POST /stream + SSE events + cancel）
│   │   │   └── chat_sessions.py  会话 CRUD（列表 / 删除 / 重命名）
│   │   ├── auth/                 JWT 认证（注册 / 登录 / 手机短信验证码）
│   │   ├── core/                 基础设施
│   │   │   ├── config.py         pydantic-settings（读 .env）
│   │   │   ├── db.py             async SQLAlchemy session
│   │   │   ├── cache.py          Redis 缓存
│   │   │   ├── retry.py          瞬时/永久错误分类 + 重试（with_retry）
│   │   │   ├── sms.py            阿里云 PNVS 短信验证码
│   │   │   └── geo.py            地址 → 国籍推断
│   │   ├── memory/               会话记忆（两级压缩 + 可追溯）
│   │   │   ├── agent.py          get_context（分层取）+ add_message_and_maybe_compress
│   │   │   ├── compressor.py     layer_2 原文→摘要 + layer_3 摘要→全局合并
│   │   │   ├── models.py         4 表（sessions / messages / compression_versions / map）
│   │   │   └── storage.py        消息 CRUD + 未压缩查询
│   │   ├── models/               SQLAlchemy ORM（Seller / Product / User / UserAcquiredSeller / …）
│   │   ├── providers/            外部数据源 adapter
│   │   │   ├── adapters/         Apify actor（amazon_products / seller / junglee / google）
│   │   │   ├── apify_provider.py Apify 统一接口（批量采集 + 缓存）
│   │   │   ├── tianyancha.py     天眼查联系方式
│   │   │   └── qichacha.py       企查查联系方式
│   │   ├── quota.py              配额服务（grant_sellers 行锁 + 永久去重）
│   │   └── main.py               FastAPI 入口（router 注册 + lifespan）
│   ├── alembic/                  数据库迁移（线性链，head = b3c4d5e6f7a8）
│   ├── tests/                    pytest（agent_eval / agent / core / 流程测试）
│   ├── entrypoint.sh             Docker 容器入口（alembic upgrade head + uvicorn）
│   ├── pyproject.toml            依赖 + pytest 配置（asyncio_mode=auto）
│   └── requirements.txt
├── frontend/                     Next.js 15（App Router）+ React 19 + TypeScript
│   ├── app/                      页面（首页 / login / register / chat）
│   ├── components/               React 组件（SellerTable 分页表格）
│   └── lib/api.ts                API 客户端（auth + streamChat + connectStream SSE + sessions）
├── wiki/                         技术文档站（VitePress：31 篇 md + .vitepress/config.mts，构建产物 wiki-dist/ 由 nginx 挂载）
├── scripts/                      deploy.sh（tar+ssh 一键部署）+ start.sh（本地启动）
├── nginx/                        Nginx 反代配置
├── docker-compose.prod.yml       生产编排（pg / redis / backend / frontend / nginx）
├── docker-compose.yml            本地编排
└── CLAUDE.md                     项目指引（本文件）
```

### 关键约定

- **后端**：`app/` 按职责分目录。改 Agent 图 → `agent/v2/`；加查询工具 → `agent/skills.py` + `ALL_TOOLS`；改 API → `api/`；改记忆 → `memory/`。
- **前端**：App Router。页面在 `app/`，复用组件在 `components/`，API 调用在 `lib/api.ts`（唯一出口）。
- **数据库**：改 schema 先写 alembic migration（`alembic revision --autogenerate`），不要手动改表。
- **测试**：`tests/agent_eval/` 跑真实 LLM（慢，手动/定期跑）；`tests/agent/` + `tests/core/` 是纯单测（快，可 CI）。
- **部署**：`scripts/deploy.sh` tar 同步代码 → SSH docker compose up --build → 健康检查。
