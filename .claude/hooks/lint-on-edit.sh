#!/bin/bash
# PostToolUse(Edit|Write)：改完 Python/TS 文件立刻 lint，秒级反馈语法/低级错误。
# 输入 stdin 是 JSON（tool_input.file_path），只 lint 本次改的文件，不动全仓库。

FILE=$(python3 -c 'import sys,json; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))' 2>/dev/null)
[ -z "$FILE" ] || [ ! -f "$FILE" ] && exit 0

case "$FILE" in
  *.py)
    RUFF="/Users/guoxi/Documents/agent 项目实战/acquisition/backend/.venv/bin/ruff"
    [ -x "$RUFF" ] || exit 0
    OUT=$("$RUFF" check --no-cache "$FILE" 2>&1)
    ;;
  *.ts|*.tsx)
    exit 0  # TS 由 tsc 门槛兜底（pnpm exec tsc 全量，避免逐文件跑 tsc 的慢与误报）
    ;;
  *)
    exit 0
    ;;
esac

if [ -n "$OUT" ] && ! echo "$OUT" | grep -q "All checks passed"; then
  echo "ruff 发现问题（$FILE）：" >&2
  echo "$OUT" >&2
  exit 0  # 只提示不阻断：lint 建议由 Claude 看到后自行修
fi
exit 0
