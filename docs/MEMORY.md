# 会话压缩 Agent 系统 - 技术设计文档

> 版本：1.0  
> 更新日期：2026-07-14  

---

## 目录

- [1. 背景与目标](#1-背景与目标)
- [2. 核心问题分析](#2-核心问题分析)
- [3. 系统架构](#3-系统架构)
- [4. 数据库表设计详解](#4-数据库表设计详解)
- [5. 压缩机制设计](#5-压缩机制设计)
- [6. 可追溯性设计](#6-可追溯性设计)
- [7. 缓存与查询优化](#7-缓存与查询优化)
- [8. 核心模块说明](#8-核心模块说明)
- [9. 典型工作流程](#9-典型工作流程)
- [10. 设计权衡与决策](#10-设计权衡与决策)

---

## 1. 背景与目标

### 1.1 业务背景

LLM（大语言模型）存在 token 上下文窗口限制（如 GPT-4 为 128K tokens）。在长时间对话场景中，历史消息会不断累积，最终超出模型处理能力。直接截断会丢失关键信息，影响对话质量。

### 1.2 设计目标

| 目标 | 说明 |
|------|------|
| **数据完整性** | 原始消息永不丢失，压缩只是衍生视图 |
| **可追溯性** | 通过映射表双向查询原始消息与压缩结果 |
| **版本管理** | 支持多版本压缩，可对比不同压缩策略的效果 |
| **分层查询** | 根据需要选择不同详细程度的上下文 |
| **性能优化** | 工作会话表缓存热数据，避免实时构建上下文 |
| **灵活压缩** | 针对不同部分使用不同策略，部分保留原文 |

---

## 2. 核心问题分析

### 2.1 为什么不能直接截断历史？

```
原始对话（5000 tokens）:
  msg1: 用户讨论了数据库设计方案
  msg2: 助手给出了三个优化建议
  ...
  msg20: 用户确认采用方案二

直接截断前16条:
  只剩 msg17~msg20 → LLM 完全不知道前面讨论了什么
  用户问 "再详细说说方案二" → LLM 无法回答
```

### 2.2 为什么不能"压缩完直接给 LLM"？

| 问题 | 影响 |
|------|------|
| 每次发消息都要重新压缩 | API 费用随对话轮次线性增长 |
| 无法做增量压缩 | 重复压缩已处理过的内容 |
| 压缩结果无法回溯 | 用户查询历史时找不到原文 |
| 无法对比压缩策略 | 用完即弃，无法评估效果 |
| 上下文构建慢 | 每次都要跨多表 JOIN 查询 |

### 2.3 解决方案

采用 **"原始表不动 + 压缩表存储 + 映射表关联 + 缓存表加速"** 的四层架构。

---

## 3. 系统架构

### 3.1 整体架构图

```
┌─────────────────────────────────────────────────────────┐
│                      Agent 层                            │
│   ChatAgent / AgentManager（对外接口）                    │
├─────────────┬───────────────┬───────────────────────────┤
│  存储模块    │   查询模块     │       压缩模块             │
│ SessionStorage│ SessionQuery │  SessionCompressor        │
├─────────────┴───────────────┴───────────────────────────┤
│                    数据模型层                             │
│  Session / Message / CompressionConfig / ...            │
├─────────────────────────────────────────────────────────┤
│                    数据库层                               │
│  ┌────────┐ ┌─────────┐ ┌───────────┐ ┌──────────────┐ │
│  │sessions│ │messages │ │compression│ │working_      │ │
│  │        │ │         │ │_versions  │ │sessions      │ │
│  └────────┘ └─────────┘ └───────────┘ └──────────────┘ │
└─────────────────────────────────────────────────────────┘
```

### 3.2 模块职责

| 模块 | 文件 | 职责 |
|------|------|------|
| 数据库管理 | `database.py` | 连接池、事务管理 |
| 数据模型 | `models.py` | 数据类、枚举定义 |
| 会话存储 | `session_storage.py` | 会话/消息的 CRUD |
| 会话查询 | `session_query.py` | 上下文构建、分层查询、可追溯查询 |
| 会话压缩 | `session_compressor.py` | 压缩策略执行、版本管理、触发检测 |
| Agent 主类 | `agent.py` | 集成所有模块，对外提供统一接口 |

---

## 4. 数据库表设计详解

### 4.1 表关系总览

```
sessions（会话主表）
  │
  ├── messages（原始消息表）── Source of Truth
  │       │
  │       └── message_compression_map（映射表）
  │               │
  │               └── compression_versions（压缩版本表）
  │                       │
  │                       └── compression_configs（压缩配置表）
  │
  └── working_sessions（工作会话缓存表）
```

### 4.2 各表详细说明

#### 4.2.1 sessions — 会话主表

**职责**：记录会话基本信息和状态。

| 关键字段 | 说明 |
|----------|------|
| `session_id` | 主键，UUID |
| `status` | 会话状态：active / paused / completed / expired |
| `total_messages` | 消息总数（冗余统计字段） |
| `total_tokens` | Token 总数（冗余统计字段） |
| `context_data` | 会话上下文 JSON（用户偏好、环境变量等） |

#### 4.2.2 messages — 原始消息表（Source of Truth）

**职责**：存储所有完整的消息记录，**永不修改**。

| 关键字段 | 说明 |
|----------|------|
| `sequence_number` | 消息序列号，保证顺序（唯一约束：session_id + sequence_number） |
| `role` | 消息角色：user / assistant / system / tool / function |
| `is_compressed` | 是否已被压缩处理 |
| `compression_version` | 被哪个压缩版本处理（可追溯） |
| `parent_message_id` | 关联上一条消息（树形对话结构） |

> **设计原则**：这张表是数据的唯一真实来源。所有压缩操作都不会修改此表内容，只会更新 `is_compressed` 标记。

#### 4.2.3 compression_configs — 压缩配置表

**职责**：定义压缩策略和参数。

| 关键字段 | 说明 |
|----------|------|
| `strategy` | 压缩策略：summary / key_points / hierarchical / token_window / custom |
| `trigger_type` | 触发类型：message_count / token_count / time_interval / manual |
| `trigger_threshold` | 触发阈值 |
| `target_compression_ratio` | 目标压缩比（如 0.3 表示压缩到 30%） |
| `preserve_last_n_messages` | 保留最近 N 条不压缩 |

#### 4.2.4 compression_versions — 压缩版本表

**职责**：记录每次压缩操作的结果，支持多版本管理。

| 关键字段 | 说明 |
|----------|------|
| `version_number` | 版本号（同会话内递增） |
| `start/end_sequence_number` | 压缩的消息序列号范围 |
| `compressed_content` | 压缩后的内容 |
| `original_tokens` / `compressed_tokens` | 压缩前后 token 数 |
| `compression_ratio` | 实际压缩比 |
| `quality_score` | 压缩质量评分 |
| `embedding_vector` | 压缩内容的向量表示（用于相似度检索） |
| `status` | 版本状态：draft / active / archived / failed |

#### 4.2.5 message_compression_map — 消息压缩映射表（核心）

**职责**：建立原始消息与压缩版本的多对多关系，是可追溯性的核心。

| 关键字段 | 说明 |
|----------|------|
| `message_id` | 原始消息 ID（外键 → messages） |
| `version_id` | 压缩版本 ID（外键 → compression_versions） |
| `message_role_in_compression` | 消息在压缩中的角色：source / reference / context |
| `relevance_score` | 该消息对压缩结果的重要性权重 |
| `mapped_by` | 映射创建者：system / manual / ai |

> **唯一约束**：`(message_id, version_id)` — 一条消息在同一版本中只有一条映射。

#### 4.2.6 working_sessions — 工作会话表（热数据缓存）

**职责**：缓存当前查询所需的预构建上下文，避免每次实时拼装。

| 关键字段 | 说明 |
|----------|------|
| `active_messages` | 最近 N 条未压缩消息（JSON） |
| `layer_1_recent` | 第 1 层：最近完整消息 |
| `layer_2_compressed` | 第 2 层：压缩后的近期会话 |
| `layer_3_summary` | 第 3 层：长期摘要 |
| `current_compression_version_id` | 当前使用的压缩版本 |
| `max_context_tokens` | 最大上下文 token 限制 |
| `expires_at` | 缓存过期时间 |

> **设计原则**：此表是纯性能优化，数据是其他表的冗余。删除不影响正确性，只影响性能。

---

## 5. 压缩机制设计

### 5.1 为什么需要压缩表

| 原因 | 说明 |
|------|------|
| **避免重复压缩** | 压缩结果持久化后可反复使用，无需每次重新调用 LLM |
| **增量压缩** | 只压缩新增消息，不重复处理已压缩内容 |
| **策略对比** | 不同版本的压缩结果可随时调出比较 |
| **质量评估** | 记录压缩比、质量分等指标用于持续优化 |

### 5.2 五种压缩策略

| 策略 | 说明 | 适用场景 |
|------|------|----------|
| `summary` | 生成对话摘要 | 通用场景，需要语义级概括 |
| `key_points` | 提取关键信息点 | 信息密集型对话，需要保留要点 |
| `hierarchical` | 分层压缩（按角色/主题分组） | 复杂多主题对话 |
| `token_window` | 滑动窗口保留 | 短期高频对话，只关心最近上下文 |
| `custom` | 自定义模板压缩 | 特殊需求场景 |

### 5.3 四种触发机制

```
message_count  → 未压缩消息数达到阈值时触发
token_count    → 未压缩消息 token 总量达到阈值时触发
time_interval  → 距上次压缩超过指定时间间隔时触发
manual         → 手动调用 compress_session() 触发
```

### 5.4 压缩执行流程

```
1. 检测是否需要压缩（check_compression_needed）
       ↓
2. 获取未压缩消息，排除最近 N 条保留消息
       ↓
3. 根据策略执行压缩（调用 LLM 或规则处理）
       ↓
4. 创建 compression_versions 记录
       ↓
5. 创建 message_compression_map 映射
       ↓
6. 更新 messages.is_compressed 标记
       ↓
7. 清除 working_sessions 缓存（下次查询时重建）
```

### 5.5 增量压缩示例

```
初始状态:
  msg1~msg16（未压缩）

第一次压缩 (threshold=16, preserve_last=4):
  压缩 msg1~msg12 → version_1
  msg13~msg16 保留原文

新增消息后:
  msg1~msg12（已压缩，version_1）
  msg13~msg24（未压缩）

第二次压缩:
  压缩 msg13~msg20 → version_2  ← 只处理新增的
  msg21~msg24 保留原文

发送给 LLM 的上下文:
  [version_1 摘要] + [version_2 摘要] + [msg21~msg24 原文]
```

---

## 6. 可追溯性设计

### 6.1 核心机制

可追溯性通过 `message_compression_map` 表实现双向查询：

```
原始消息  ←——映射——→  压缩版本
 (多)                   (多)
```

### 6.2 正向查询：压缩版本 → 包含哪些原始消息

**场景**：验证某次压缩的准确性，查看它基于哪些消息生成。

```sql
SELECT m.*
FROM messages m
JOIN message_compression_map mcm ON m.message_id = mcm.message_id
WHERE mcm.version_id = ?
ORDER BY m.sequence_number;
```

### 6.3 反向查询：原始消息 → 被压缩到了哪个版本

**场景**：用户查询历史消息，需要定位其所在的压缩版本。

```sql
SELECT cv.*
FROM compression_versions cv
JOIN message_compression_map mcm ON cv.version_id = mcm.version_id
WHERE mcm.message_id = ?;
```

### 6.4 实际应用场景

```
用户: "你上次提到的数据库设计方案，具体说了什么？"

系统处理:
1. 通过映射表定位 msg3 在 version_1 中
2. 直接从 messages 表读取 msg3 的完整原始内容
3. 将原文作为参考返回给 LLM

→ 即使发送给 LLM 的是压缩摘要，也能随时取回原文
```

---

## 7. 缓存与查询优化

### 7.1 工作会话表的作用

`working_sessions` 是其他三张表数据的**预计算结果缓存**。

#### 没有缓存时（每次 3 表查询）

```
用户发消息
  ↓
1. 查 messages 表       → 取最近 N 条未压缩原文
2. 查 compression_versions → 取最新压缩摘要
3. 查 sessions 表       → 取会话元数据
  ↓
拼接上下文 → 发给 LLM
```

#### 有缓存时（1 表查询）

```
用户发消息
  ↓
查 working_sessions → 直接拿到拼好的三层上下文
  ↓
发给 LLM
```

### 7.2 三层上下文结构

| 层级 | 字段 | 内容 | 细节程度 |
|------|------|------|----------|
| 第 1 层 | `layer_1_recent` | 最近完整消息 | 最高（保原文） |
| 第 2 层 | `layer_2_compressed` | 压缩后的近期会话 | 中等（有摘要） |
| 第 3 层 | `layer_3_summary` | 长期摘要 | 最低（只有要点） |

**按需取用示例**：

```
场景 1: 正常聊天 → layer_1 + layer_2 足够
场景 2: 查询很久之前的内容 → layer_1 + layer_2 + layer_3
场景 3: 简单问候 → 只用 layer_1
```

### 7.3 缓存更新策略

```
缓存命中判断:
  1. 缓存是否存在？
  2. 是否过期（expires_at）？
  3. 构建时间是否在合理范围内（如 1 小时内）？

缓存失效触发条件:
  - 新消息加入（active_messages 变化）
  - 执行了压缩操作（layer_2_compressed 变化）
  - 缓存自然过期
```

### 7.4 Token 裁剪机制

```python
def _trim_context_by_tokens(context, max_tokens):
    estimated_tokens = 0
    trimmed_messages = []
    for msg in context['active_messages']:
        msg_tokens = len(msg['content'].split())
        if estimated_tokens + msg_tokens > max_tokens:
            break
        trimmed_messages.append(msg)
        estimated_tokens += msg_tokens
    return context
```

---

## 8. 核心模块说明

### 8.1 ChatAgent — Agent 主类

提供完整的会话管理 API：

| 方法 | 功能 |
|------|------|
| `create_session()` | 创建新会话 |
| `send_message()` | 发送消息并获取回复（自动触发压缩检查） |
| `get_context()` | 获取会话上下文（优先走缓存） |
| `get_hierarchical_context()` | 获取分层上下文视图 |
| `manual_compress()` | 手动触发压缩 |
| `get_compression_traceability()` | 获取压缩可追溯性信息 |
| `compare_compression_versions()` | 对比两个压缩版本 |
| `get_statistics()` | 获取会话统计信息 |
| `configure_compression()` | 配置压缩策略 |

### 8.2 AgentManager — 多租户管理

```python
manager = AgentManager()
agent = manager.get_agent(user_id="user_001", agent_id="assistant_001")
```

支持多用户、多 Agent 的会话隔离管理。

### 8.3 SessionStorage — 存储模块

负责会话和消息的持久化，核心特点：
- 消息序列号自动递增（保证顺序）
- 会话统计信息实时更新
- 消息压缩状态标记管理

### 8.4 SessionQuery — 查询模块

负责多维度查询，核心特点：
- 优先从缓存读取上下文
- 支持分层上下文构建
- 提供双向可追溯查询
- Token 限制自动裁剪

### 8.5 SessionCompressor — 压缩模块

负责压缩策略执行，核心特点：
- 自动检测压缩触发条件
- 支持五种压缩策略
- 增量压缩（不重复处理）
- 压缩质量评估
- 版本管理与对比

---

## 9. 典型工作流程

### 9.1 消息发送完整流程

```
用户调用 agent.send_message("什么是机器学习？")
  │
  ├── 1. 确定目标会话（无则自动创建）
  │
  ├── 2. 存储用户消息到 messages 表
  │      └── 更新 sessions 统计信息
  │
  ├── 3. 构建上下文（get_session_context）
  │      ├── 查 working_sessions 缓存
  │      │   ├── 命中 → 直接使用
  │      │   └── 未命中 → 重建并更新缓存
  │      │
  │      └── Token 裁剪
  │
  ├── 4. 调用 LLM 生成回复
  │
  ├── 5. 存储助手消息到 messages 表
  │
  ├── 6. 检查是否需要压缩（check_compression_needed）
  │      ├── 不需要 → 流程结束
  │      └── 需要 → 执行压缩
  │           ├── 获取未压缩消息
  │           ├── 执行压缩策略
  │           ├── 创建 compression_versions 记录
  │           ├── 创建 message_compression_map 映射
  │           ├── 标记 messages.is_compressed
  │           └── 清除 working_sessions 缓存
  │
  └── 7. 返回助手回复
```

### 9.2 历史消息回溯流程

```
用户: "你之前说的方案二，再详细说说"
  │
  ├── 1. 在当前上下文中搜索 "方案二"
  │
  ├── 2. 如果在未压缩消息中 → 直接返回原文
  │
  ├── 3. 如果在压缩摘要中 → 查 message_compression_map
  │      ├── 定位到具体的 message_id
  │      └── 从 messages 表读取完整原文
  │
  ├── 4. 将原始内容注入当前上下文
  │
  └── 5. LLM 基于完整信息生成回复
```

---

## 10. 设计权衡与决策

### 10.1 为什么原始消息永不修改？

| 方案 | 优点 | 缺点 |
|------|------|------|
| **直接覆盖原始消息** | 简单，表少 | 数据永久丢失，无法回溯 |
| **原始不动 + 压缩存表** ✅ | 数据完整，可追溯 | 多表，增加复杂度 |

**决策**：选择后者。数据完整性是底线，压缩只是衍生视图。

### 10.2 为什么需要映射表？

| 方案 | 问题 |
|------|------|
| 在 messages 表加 `compressed_content` 字段 | 一条消息只能对应一个压缩结果，无法多版本 |
| 在 compression_versions 加 `message_ids` JSON | 查询效率低，无法建索引 |
| **独立映射表** ✅ | 多对多关系清晰，可双向查询，支持索引 |

### 10.3 为什么需要缓存表？

| 方案 | 性能 |
|------|------|
| 每次实时查询多表拼接 | 慢（3+ 次 DB 查询） |
| **缓存预构建结果** ✅ | 快（1 次 DB 查询），过期后重建 |

### 10.4 为什么保留最近 N 条不压缩？

```
不保留:
  msg1~msg16 全压缩 → 发给 LLM 的只有摘要 + 新消息
  → LLM 缺少最近上下文细节，回复质量下降

保留最近 4 条:
  msg1~msg12 压缩 + msg13~msg16 原文 + 新消息
  → LLM 既有历史概要，又有近期细节
```

---

## 附录：文件结构

```
project/memory/
├── src/                       源码包（from src.xxx import）
│   ├── models.py              数据模型与枚举定义
│   ├── database.py            数据库连接池管理
│   ├── session_storage.py     会话存储模块（CRUD）
│   ├── session_query.py       会话查询模块（上下文构建）
│   ├── session_compressor.py  会话压缩模块（策略执行）
│   └── agent.py               Agent 主类（集成所有模块）
├── examples/
│   ├── examples.py            使用示例
│   └── session_manager_draft.py   早期 AgentSessionManager 设计草图（伪代码）
├── db/
│   └── schema.sql             数据库表结构定义（6 张表）
├── requirements.txt           Python 依赖
├── .env.example               环境变量示例
├── TECH_DESIGN.md             本文档
└── README.md
```
