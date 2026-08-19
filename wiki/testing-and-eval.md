# 项目测试与评估体系（含 Claude Code 工作流）

> 本文是本项目测试/评估体系的完整技术文档：为什么这么分层、每一层怎么写、
> 怎么挡住"改 A 坏 B"的回归、agent 的 LLM 环节怎么评估、以及如何把这些流程
> 和 Claude Code 的协作模式（CLAUDE.md 测试纪律 / smart-commit 门槛 / deploy.sh 门槛）焊在一起。
>
> 适用读者：维护本项目的开发者，以及想复制这套体系到其他 agent 项目的人。

***

## 一、核心问题：Agent 项目的测试难点

传统 Web 项目的测试对象是**确定性代码**——输入固定输出固定。Agent 项目多了一类全新的被测对象：**LLM 驱动的行为**（意图分类、参数提取、prompt 驱动的分析）。两类对象的失效模式完全不同：

| <br /> | 确定性代码       | LLM 行为                     |
| ------ | ----------- | -------------------------- |
| 失效方式   | 逻辑 bug、边界条件 | prompt 改动导致输出漂移、模型升级导致能力变化 |
| 判定方式   | 断言相等        | 断言"准确率"（统计意义）              |
| 测试成本   | 毫秒级、免费      | 秒级、烧 token                 |
| 能否 CI  | 能           | 慢且贵，通常手动/定期跑               |

所以 agent 项目的测试必须拆成两套：**代码测试**（pytest，快而准）+ **行为评估**（eval，真实 LLM 跑 golden set）。混在一起要么测不到 LLM 退化，要么 CI 慢到没人跑。

第二个难点：**agent 的"正确"不只看单点，还看链路**。意图识别对了但配额判断漏了、采集入库了但联系方式没查、LLM 返回了卖家但没落库 `user_acquired_sellers`——每个环节单独对，拼起来照样错。这要求有贯通全流程的集成测试。

第三个难点：**性能/环境类的 bug 单测测不出来**。多 worker 下取消失效、JSONB 原地修改不生效、pytest event loop 绑定——这些都是本项目实际踩过的坑（见 `multi-worker-ghost-cancel.md`、`pg-data-sync-pitfalls.md`），只能靠针对性测试或线上观测兜住。

***

## 二、总体架构：四层防线

```
改动 → [第 1 层] 单元测试      毫秒级，CI + 本地高频跑
    → [第 2 层] 集成测试      秒级，真实 PG，部署前跑
    → [第 3 层] Agent 评估     真实 LLM，改 prompt/换模型前后跑
    → [第 4 层] 部署门槛 + CI + 线上拨测
```

### 目录结构（backend/tests/）

```
tests/
├── agent_eval/       # LLM 行为评估（真实 LLM，不进常规 CI）
│   ├── test_classify.py       # 意图分类准确率（16 case）
│   ├── test_parse_intent.py   # 提参准确率（13 case）
│   └── test_score_sellers.py  # 评分策略回归（4 case，纯规则）
├── unit/             # 单元测试（mock 外部，不连 DB/LLM）
│   ├── test_route.py          # _route_acquire 路由逻辑
│   ├── test_cancel.py         # 协作式取消（CancelledByUser + check_cancel）
│   ├── test_sms.py            # 验证码校验逻辑
│   ├── test_sms_send.py       # send_sms_code（mock httpx + 阿里云三种分支）
│   ├── test_auth_security.py  # 密码哈希 + JWT
│   ├── test_geo.py            # 地址→国籍
│   ├── test_skills.py         # 工具注册表
│   └── test_retry.py          # 重试分类（瞬时/永久）
├── integration/      # 集成测试（真实 PG + ASGI client，跑完清理）
│   ├── test_quota.py          # 配额发放/并发/去重
│   ├── test_agent_flow.py     # agent 主流程（mock LLM/Apify）
│   ├── test_auth_api.py       # 注册/登录/me 全流程
│   └── test_sessions_api.py   # 会话 CRUD + 归属校验
└── conftest.py       # session-scoped engine fixture（解决 asyncpg event loop 绑定）
```

### 每层的职责边界

\| 层 | 回答的问题 | 依赖 | 速度 | 跑的时机 |
\|---|---改|---|---|---|
\| unit | "这段逻辑对不对" | 无（全 mock） | 毫秒 | 每次改动 + CI |
\| integration | "这些模块拼起来对不对" | 真实 PG | 秒级 | 部署前 + CI 可选 |
\| agent\_eval | "LLM 表现变好还是变差" | 真实 LLM API | 十秒级 | 改 prompt/换模型前后 |
\| 部署门槛 | "坏代码会不会到线上" | 全部本地依赖 | \~30s | 每次 deploy.sh |

***

## 三、第 1 层：单元测试

### 原则

1. **mock 一切外部**：DB、LLM、Apify、阿里云、天眼查全 mock。单测测的是**你写的代码的逻辑**，不是外部服务。
2. **每个测试只测一个行为**，测试名就是行为描述：`test_send_fail_aliyun_error`。
3. **不追求覆盖率数字**，追求"改这行代码哪个测试会挂"的可追溯性。覆盖盲区才是重点（`make test-cov` 看分模块缺口）。

### 模式示例

**mock httpx 外部 API**（[test\_sms\_send.py](../backend/tests/unit/test_sms_send.py)）：

```python
class _MockResp:
    def json(self):
        return {"Code": "OK", "Message": "OK"}

mock_client = AsyncMock()
mock_client.__aenter__ = AsyncMock(return_value=mock_client)
mock_client.__aexit__ = AsyncMock(return_value=False)
mock_client.post = AsyncMock(return_value=_MockResp())

with patch("app.core.sms.httpx.AsyncClient", return_value=mock_client):
    result = await sms_mod.send_sms_code("13800000000", "654321")
assert result["ok"] is True
```

坑：`AsyncMock().json.return_value` 返回 coroutine，要用普通 class 的同步 `json()`。

**纯函数断言**（[test\_geo.py](../backend/tests/unit/test_geo.py)）：`parse_country("深圳市南山区科技园") == "China"`，最简单也最稳。

**协作式取消**（[test\_cancel.py](../backend/tests/unit/test_cancel.py)）：mock DB 返回 cancelled 标志，断言抛 `CancelledByUser`——这类"进程间协作"逻辑没法靠真实多进程测，只能在单测里验证协议正确。

***

## 四、第 2 层：集成测试

### 原则

1. **真实 PG，不 mock DB**——SQLAlchemy 的 JSONB 原地修改不生效、enum 名不一致、行锁行为，只有真库才能暴露。
2. **ASGI 直连**（`httpx.ASGITransport`）不走网络，快且无端口冲突。
3. **测试数据隔离 + 自清理**：临时用户（随机前缀）+ fixture 里前后清理（先删 FK 子表再删 user）。

### 模式示例（[test\_quota.py](../backend/tests/integration/test_quota.py)）

```python
@pytest.fixture
async def clean_user():
    async with db_mod.async_session() as db:
        u = User(username=f"qt_{uuid.uuid4().hex[:6]}", plan="free")
        db.add(u); await db.commit()
        uid = u.id
    yield uid
    # 清理：先删子表（FK 约束），再删用户
    async with db_mod.async_session() as db:
        await db.execute(sa_delete(UserAcquiredSeller).where(...))
        await db.execute(delete(User).where(User.id == uid))
        await db.commit()
```

**并发配额测试**：`asyncio.gather` 并发 grant 同一用户，断言总数不超发——行锁（SELECT FOR UPDATE）行为只有真库并发才能验证。

### conftest.py：asyncpg event loop 坑

pytest-asyncio 默认每个测试一个 event loop，而 asyncpg 连接绑定创建时的 loop。解法是 session-scoped fixture 重建 engine 并替换 `db_mod.engine/async_session`：

```python
@pytest.fixture(scope="session")
async def test_engine():
    old = db_mod.engine
    old.dispose()
    engine = create_async_engine(TEST_DB_URL)
    db_mod.engine = engine
    db_mod.async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield engine
    engine.dispose()
    db_mod.engine = old  # 恢复
```

配合 `pytest.ini`：

```ini
asyncio_default_fixture_loop_scope = session
asyncio_default_test_loop_scope = session
```

***

## 五、第 3 层：Agent 评估（本体系的核心增量）

详细方法论见 `agent-eval.md`，这里讲落地形态。

### 1. 意图分类评估（test\_classify.py）

`(query, expected_intent)` 的 golden set，参数化跑真实 LLM：

```python
CASES = [
    ("美国站卖杯子的中国卖家", "acquisition"),
    ("查看我获取的所有卖家", "query"),
    ("你是谁", "chat"),
    ...
]

@pytest.mark.parametrize("query, expected", CASES)
async def test_classify_intent(query, expected):
    r = classify_intent({"user_query": query})
    assert r["intent"] == expected
```

### 2. 提参评估（test\_parse\_intent.py）——比分类更深一层

分类只回答"哪类请求"，提参回答"抽出的参数对不对"。断言粒度到字段：

```python
CASES = [
    # 欧洲站 → amazon.co.uk（线上出过 bug 的映射）
    ("欧洲站卖杯子卖家", {"marketplace": "amazon.co.uk", "category": "cups"}),
    # 全字段
    ("美国站找10个卖户外家具的中国大卖家",
     {"marketplace": "amazon.com", "category": "outdoor furniture",
      "business_country": "China", "desired_count": 10}),
    # 缺站点 → need_confirm
    ("卖杯子的", {"marketplace": None, "need_confirm": True}),
]

@pytest.mark.parametrize("query, expected", CASES)
async def test_parse_intent_fields(query, expected):
    msgs = [{"role": "system", "content": INTENT_PROMPT},
            {"role": "user", "content": query}]
    parsed = await _structured_invoke(ParsedIntent, msgs, "eval_parse_intent")
    for k, want in expected.items():
        assert getattr(parsed, k) == want
```

**关键设计：只断言写出的字段**。没写的字段（LLM 自由度内）不断言，避免误报。这是 LLM eval 和传统断言的重要区别——你评估的是"你关心的维度是否达标"，不是全量字段比对。

### 3. 评分策略回归（test\_score\_sellers.py）

`score_sellers` 是纯规则（无 LLM），但它是**业务策略**——公式改动的后果需要用测试锁住预期行为：

```python
async def test_china_bonus_beats_non_china():
    sellers = [
        _mk("us_big", 3000, "US"),      # 10 + 30 = 40
        _mk("cn_mid", 2000, "China"),   # 10 + 20 + 20 = 50
    ]
    r = await score_sellers({"sellers": sellers})
    assert [s["seller_id"] for s in r["scored_sellers"]][0] == "cn_mid"
```

这个测试还暴露过一个认知偏差：feedback 权重（0-70 封顶）实际比"中国加分"（+20）更重——feedback 差距超 2000 时国籍倾斜失效。**评估测试的价值不只是回归防护，还会反哺策略设计**。

### 4. eval 的运行纪律

- **不进常规 CI**（慢、烧钱、有波动）。改 `INTENT_PROMPT` / 评分公式 / 换模型前后手动跑：`.venv/bin/python -m pytest tests/agent_eval/ -v`
- **允许少量失败**：LLM 有随机性，100% 通过率不现实也不必要。关注**趋势**：上次 13/13 这次 10/13，说明 prompt 改坏了。
- **失败 case 进 golden set**：线上发现的 bad case（如"欧洲站→amazon.co.uk"）加进 CASES，防止同坑重踩。

### 5. 还没做但建议做（Roadmap）

- **端到端对话评估**：整轮对话（含 HITL 确认、采集循环）的轨迹正确性，需要更重的 harness（参考 LangSmith eval）。
- **LLM-as-judge**：对 llm\_analysis 生成的卖家画像做质量打分（另一个 LLM 当裁判），适合自由文本这类没法逐字断言的输出。
- **线上抽样回看**：生产 trace 里抽样真实用户 query，定期人工复核提参质量。

***

## 六、第 4 层：流程门槛（CI + 部署门槛）

### CI（.github/workflows/ci.yml）

两个并行 job：`backend-unit`（Python 3.12 + `pytest tests/unit/`）+ `frontend-check`（pnpm 10 + `tsc --noEmit`）。push/PR 到 main 触发。
integration 不进 CI——需要真实 PG，本地部署前跑（可加 services.postgres 后补）。

### deploy.sh 部署前门槛

在同步代码之前插入第 0 步：

```bash
# 0. 部署前测试门槛：后端全量 pytest + 前端 tsc，任一失败立即中止
echo "=== 0/4 部署前测试 ==="
cd "${PROJECT_DIR}/backend"
.venv/bin/python -m pytest tests/ -q
cd "${PROJECT_DIR}/frontend"
pnpm exec tsc --noEmit
```

`set -e` 保证失败即中止，坏代码到不了服务器。**这是最重要的一道门**——CI 只在推 GitHub 后生效（本项目当前无 remote），而 deploy.sh 每次部署必跑。

### Makefile 快捷命令

```bash
make test          # 后端全量
make test-unit     # 只跑 unit（不连 DB，秒级）
make test-cov      # 覆盖率
make frontend-check  # tsc
```

***

## 七、如何配合 Claude Code

以上是"体系"，这一节讲"怎么让 Claude Code 在这个体系里守规矩"。核心思路：**把测试纪律写进项目规则，让 AI 编码时强制走测试循环**。

### 1. CLAUDE.md 测试纪律（最重要）

[CLAUDE.md](../CLAUDE.md) 第二节写死了规则：

> **改/加任何后端功能，必须同步写或改对应的 pytest 测试（backend/tests/），改完跑全量 pytest，通过才报告完成。**

配套细则：写代码同步写测试（不留到最后补）、失败如实说（不粉饰、不改测试迁就 bug）、琐碎例外（文案/注释跳过）。

**为什么有效**：Claude Code 每次会话都会读 CLAUDE.md，这相当于给 AI 的"系统级约束"。没有这条，AI 默认行为是"功能写完就报告完成"，测试是可选项；有了这条，"写测试 + 跑通过"成为完成定义的一部分。

### 2. smart-commit skill 提交门槛

[.claude/skills/smart-commit/SKILL.md](../.claude/skills/smart-commit/SKILL.md) 加了提交前测试门槛：

> backend 改动必须 pytest 全过才能提交。

流程：用户 `/smart-commit` → Claude 分析 diff → 生成 Conventional Commits message → **跑测试** → 全过才 `git commit`。

**为什么有效**：把测试门槛挂在 commit 这个天然动作上。你不需要记得跑测试，只需要记得 commit——而 commit 是必然发生的。

### 3. Claude Code 的分工建议

| 场景       | 让 Claude 做什么                  | 你做什么          |
| -------- | ----------------------------- | ------------- |
| 新功能      | 列计划（计划先行）→ 写代码 + 写测试 + 跑测试    | 审 diff、判断需求口径 |
| 改 prompt | 跑 agent\_eval 前后对比            | 看准确率是否回退      |
| 修 bug    | 先写复现测试再修                      | 确认复现逻辑符合预期    |
| 上线       | 跑全量 + agent\_eval + deploy.sh | 按健康检查结果决定     |
| 线上救援     | 读 trace + 日志定位                | 提供线上凭证/决策     |

### 4. 一个真实的协作循环（本项目实例）

以"多 worker 取消失效"为例：

1. 用户发现线上暂停后刷新卡 loading（现象）→ 走排查。
2. Claude 分析：`_tasks` 进程内 dict 在 4-worker 下不共享 → 取消标志到不了正在跑的 worker。
3. 方案：删 `_tasks`，改**协作式取消**（DB 标志 + 节点轮询 `check_cancel`）。
4. Claude 写 `test_cancel.py` 单测锁定协议 → 修改 nodes.py / chat\_stream.py → 全量跑通过。
5. 用户 `/smart-commit` → 测试门槛跑全量 → commit。
6. 用户跑 deploy.sh → 第 0 步全量测试 → 同步 → 健康检查 → 纚上。
7. 线上再出问题 → 借助 trace + 日志 → bad case 进 golden set。

这是一个完整的"现象 → 定位 → 测试锁定 → 修复 → 门槛验证 → 上线"闭环。

### 5. 关键心得

- **规则要写下来**：口头说"记得跑测试"没用，写进 CLAUDE 编码会话每次生效。
- **门槛挂在动作上**：测试门槛挂 commit（smart-commit）、挂部署（deploy.sh），不挂"记得"。
- **AI 的报告不能全信**：测试确实跑了、且确实通过了，才算数。Claude 有时会粉饰"部分完成"——CLAUDE.md 的"失败如实说"条款就是治这个的。
- **小步快跑**：每次改动都跑全量（\~10s），比攒一大堆再跑强。全量 106 测试 10 秒，这是维持纪律的物理基础——如果全量要 10 分钟，纪律就守不住。

***

## 八、检查清单（复制到新项目）

- [ ] `pytest.ini`：asyncio\_mode=auto + loop scope 统一 session
- [ ] `tests/unit/`（mock 外部）+ `tests/integration/`（真实 DB）+ `tests/agent_eval/`（真实 LLM）三层目录
- [ ] conftest.py：session-scoped engine fixture（asyncpg loop 绑定）
- [ ] 集成测试数据自清理（FK 子表先删）
- [ ] Makefile：test / test-unit / test-cov / frontend-check
- [ ] deploy.sh 第 0 步全量测试门槛
- [ ] CLAUDE.md 测试纪律段
- [ ] smart-commit 提交前测试门槛
- [ ] CI workflow（unit + tsc）
- [ ] golden set 持续从线上 bad case 补充

***

## 附录：常用命令速查

```bash
# 日常开发
make test-unit            # 秒级，改完就跑
make test                 # 全量 106 个，~10s
make test-cov             # 看覆盖率缺口
make frontend-check       # 前端 tsc

# agent eval（改 prompt / 换模型前后）
cd backend && .venv/bin/python -m pytest tests/agent_eval/ -v

# 部署（含第 0 步测试门槛）
./scripts/deploy.sh

# smart-commit（含提交前测试门槛）
/smart-commit
```

