---
name: smart-commit
description: 用户要求提交代码（"提交"/"commit"/"git commit"）时，分析 git diff 改动点，按 Conventional Commits + 中文规范生成 commit message，确认后提交。不主动 push。
---

# 智能提交

当用户要求提交代码时（触发词：提交、commit、git commit、保存改动），按以下流程执行。

## 1. 收集改动
- `git status`：看暂存区 + 工作区状态。
- `git diff --staged`：已暂存改动（本次将提交的）。
- `git diff`：未暂存改动。
- 若有未暂存改动，**询问用户**是否一并加入本次提交；默认只提交已暂存的，不擅自 `git add` 全部。

## 2. 分析改动点
- 按文件/模块归类改动。
- 识别改动类型（取最主要的一个，混杂时可在 body 分点）：
  - `feat` 新功能
  - `fix` 修复 bug
  - `refactor` 重构（不改外部行为）
  - `docs` 文档
  - `test` 测试
  - `perf` 性能优化
  - `chore` 构建/工具/依赖/杂项
- 提取 1-3 个核心改动要点（基于实际 diff，不臆测、不夸大）。

## 3. 生成 commit message
格式（Conventional Commits + 中文描述）：
```
<type>(<scope>): <中文简述>

- 改动要点 1
- 改动要点 2
```
- `<type>`：上一步识别的类型。
- `<scope>`：影响模块（可选，如 backend / frontend / auth / quota / graph / agent）。
- **subject**：中文，简洁（建议 ≤50 字），祈使语气，结论先行。
- **body**：分点列改动（中文），说清"改了什么、为什么"，基于 diff 不编造。
- 标识符/路径/命令保持英文。

## 4. 提交
- 先把生成的完整 message 展示给用户确认。
- 确认后用 `git commit` 提交（多行 message 用多个 `-m` 或 heredoc，确保 body 保留换行）。
- **不主动 `git push`**，除非用户明确要求。
- 提交后简述本次 commit 的 hash + message 首行。

## 注意
- message 必须基于实际 diff，不编造未发生的改动。
- 只提交本次相关改动，不夹带无关文件（遵循"外科手术式改动"）。
- 若项目 CLAUDE.md / commit 规范有特殊要求（如签名、特定前缀），以项目为准。
- 大而混杂的改动建议拆成多个 commit（先问用户），每个 commit 单一职责。
- 遵循全局偏好：commit message 用中文描述，代码/路径/命令英文。
- 本项目结构：backend（FastAPI）+ frontend（Next.js），scope 用 backend/frontend 或具体模块。
