#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

TRUE = {
    "poly": "x**2 + 2*x + 1",
    "rational": "x / (1 + x)",
    "decay": "exp(-x)",
    "periodic": "sin(x) + 0.2*x",
    "saturation": "2*x / (1 + x)",
    "combined": "exp(-x) + 0.5*sin(x)",
}
FALSE = {
    "poly": "exp(x) + x",
    "rational": "0.5*x",
    "decay": "1 / (1 + x)",
    "periodic": "0.5*x",
    "saturation": "2 - exp(-x)",
    "combined": "1 / (1 + x)",
}


def argument(name: str) -> str:
    index = sys.argv.index(name)
    return sys.argv[index + 1]


def state_for(prompt: str, workspace: Path) -> str:
    if "研究起点" in prompt:
        if "poly 优先考虑指数增长" in prompt:
            return "false"
        if "poly 优先考虑二次多项式" in prompt:
            return "true"
        return "neutral"
    if (workspace / "evidence/validated-ledger.json").exists():
        return "true"
    if (workspace / "evidence/neutral-placeholder.json").exists():
        return "true" if "恢复与提交" in prompt else "neutral"
    prior = workspace / "memory/evaluated-output-a.json"
    if prior.exists() and "exp(x) + x" in prior.read_text(encoding="utf-8"):
        return "false"
    return "true"


def main() -> int:
    if "--version" in sys.argv:
        print("codex-cli 0.0.0-fake")
        return 0
    prompt = sys.stdin.read()
    workspace = Path(argument("-C"))
    last_message = Path(argument("--output-last-message"))
    state = state_for(prompt, workspace)
    expressions = TRUE if state == "true" else FALSE if state == "false" else {key: "x" for key in TRUE}
    candidates = [
        {
            "task_id": task_id,
            "candidate_index": index,
            "expression": expression,
            "rationale": "deterministic fake nested qualification",
            "confidence": 0.5,
        }
        for task_id, expression in expressions.items()
        for index in range(1, 4)
    ]
    last_message.parent.mkdir(parents=True, exist_ok=True)
    last_message.write_text(
        json.dumps({"candidates": candidates, "summary": "fake qualification"}, sort_keys=True),
        encoding="utf-8",
    )
    thread_id = str(uuid.uuid4())
    print(json.dumps({"type": "thread.started", "thread_id": thread_id}), flush=True)
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
