"""Per-run trace: every agent step, tool call and model call, as JSON lines in code/traces/<trace_id>.jsonl."""
from __future__ import annotations

import datetime as dt
import json
import time
import uuid
from pathlib import Path

from . import config


def new_id(prefix: str) -> str:
    return f"{prefix}-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


class Trace:
    def __init__(self, trace_id: str | None = None, run_id: str | None = None):
        self.trace_id = trace_id or new_id("TR")
        self.run_id = run_id
        self.t0 = time.perf_counter()
        self.path: Path = config.TRACES_DIR / f"{self.trace_id}.jsonl"
        config.TRACES_DIR.mkdir(parents=True, exist_ok=True)

    def log(self, agent: str, event: str, **data) -> None:
        rec = {"ts": dt.datetime.now().isoformat(timespec="milliseconds"),
               "ms": round((time.perf_counter() - self.t0) * 1000),
               "run_id": self.run_id, "agent": agent, "event": event, **data}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
