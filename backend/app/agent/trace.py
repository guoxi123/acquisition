"""轻量本地 trace：把 agent 每次运行的 LLM 调用写 jsonl 日志，供线上排查。

零外部依赖、零额外内存、数据不出域。每个请求用 thread_id 串起它的所有 LLM 调用，
排查时 grep <thread_id> logs/trace_*.jsonl 即可看到该请求的 LLM 输入/输出/耗时/token。
"""

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.callbacks import BaseCallbackHandler

LOG_DIR = Path("logs")


class JsonlTracer(BaseCallbackHandler):
    """记录 LLM 调用到 logs/trace_YYYY-MM-DD.jsonl。同一 run_id 串一次请求的全部调用。"""

    def __init__(self, run_id: str | None = None):
        self.run_id = run_id or uuid.uuid4().hex[:8]
        self._starts: dict[str, float] = {}

    def _log(self, event: dict) -> None:
        event["ts"] = datetime.now(timezone.utc).isoformat()
        event["run_id"] = self.run_id
        LOG_DIR.mkdir(exist_ok=True)
        path = LOG_DIR / f"trace_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
        self._starts[str(run_id)] = time.monotonic()
        self._log({
            "type": "llm_start",
            "rid": str(run_id),
            "model": (serialized or {}).get("name", ""),
            "prompt": (prompts[0] if prompts else "")[:1000],
        })

    def on_llm_end(self, response, *, run_id, **kwargs):
        start = self._starts.pop(str(run_id), None)
        dur_ms = int((time.monotonic() - start) * 1000) if start else None
        try:
            out = response.generations[0][0].text if response.generations else ""
        except Exception:
            out = ""
        llm_output = response.llm_output if response.llm_output else {}
        usage = llm_output.get("token_usage") or llm_output.get("usage")
        self._log({
            "type": "llm_end",
            "rid": str(run_id),
            "dur_ms": dur_ms,
            "out": str(out)[:1000],
            "token": usage,
        })

    def on_chain_start(self, serialized, inputs, *, run_id, **kwargs):
        # 只记有名链（便于关联节点），匿名子链跳过避免噪音
        name = (serialized or {}).get("name") or ""
        if not name:
            return
        self._starts[str(run_id)] = time.monotonic()
        self._log({"type": "chain_start", "rid": str(run_id), "name": name, "inputs": str(inputs)[:500]})

    def on_chain_end(self, outputs, *, run_id, **kwargs):
        start = self._starts.pop(str(run_id), None)
        if start is None:
            return
        self._log({
            "type": "chain_end",
            "rid": str(run_id),
            "dur_ms": int((time.monotonic() - start) * 1000),
            "outputs": str(outputs)[:500],
        })
