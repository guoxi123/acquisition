#!/bin/bash
# PreToolUse(Bash)：拦截未过测试的 git commit。
# 规则与 smart-commit skill 的测试门槛一致——backend/app/ 有未提交改动时，
# 必须先跑 pytest 全量（排除 agent_eval）通过才放行 commit。
# 这是硬门槛：Claude 无法绕过，除非测试真过或改动不含后端逻辑。

INPUT=$(cat)
CMD=$(python3 -c 'import sys,json; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' <<<"$INPUT")

# 只管 git commit（不含 git add / status / diff 等）
echo "$CMD" | grep -qE '(^|[;&|]\s*)git (commit|-C [^ ]+ commit)' || exit 0

cd "/Users/guoxi/Documents/agent 项目实战/acquisition" || exit 0

# backend/app 无未提交改动 → 放行（纯前端/文档/配置提交）
if git diff --name-only -- backend/app | grep -q . || \
   git diff --cached --name-only -- backend/app | grep -q .; then
  :
else
  exit 0
fi

# 有后端改动 → 跑测试门槛（与 deploy.sh 同口径：排除 agent_eval 外部 LLM 依赖）
cd backend
if .venv/bin/python -m pytest tests/ -q --ignore=tests/agent_eval > /tmp/hook_pytest.log 2>&1; then
  exit 0
fi

cat >&2 <<EOF
{
  "decision": "block",
  "reason": "提交门槛：backend/app 有改动但 pytest 未通过（详见 /tmp/hook_pytest.log）。测试全过后再 commit。"
}
EOF
exit 0
