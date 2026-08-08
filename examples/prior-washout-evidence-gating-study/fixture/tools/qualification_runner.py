from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from evaluate_candidate import normalized_mse

CONDITION_PATTERN = re.compile(r"^PRIOR_CONDITION=([a-z]+)$", re.MULTILINE)
POLICY_PATTERN = re.compile(r"^MEMORY_POLICY=([a-z]+)$", re.MULTILINE)

# 这些候选只用于确定性基础设施资格测试，不是模型输出，也不进入科学推断。
CANDIDATES = {
    "poly": {"true": "x**2 + 2*x + 1", "false": "exp(x) + x"},
    "rational": {"true": "x / (1 + x)", "false": "0.5*x"},
    "decay": {"true": "exp(-x)", "false": "1 / (1 + x)"},
    "periodic": {"true": "sin(x) + 0.2*x", "false": "0.5*x"},
    "saturation": {"true": "2*x / (1 + x)", "false": "2 - exp(-x)"},
    "combined": {"true": "exp(-x) + 0.5*sin(x)", "false": "1 / (1 + x)"},
}


def emit(kind: str, **payload: Any) -> None:
    print(json.dumps({"type": kind, **payload}, ensure_ascii=False, sort_keys=True), flush=True)


def _trajectory(condition: str, policy: str) -> tuple[str, str, str]:
    if condition == "correct":
        return ("true", "true", "true")
    if condition == "neutral":
        return ("neutral", "neutral", "true")
    if policy == "open":
        return ("false", "false", "false")
    if policy == "gated":
        return ("false", "true", "true")
    if policy == "purged":
        return ("false", "neutral", "true")
    raise ValueError(f"unsupported qualification arm: {condition}-{policy}")


def _candidate(task_id: str, state: str) -> str:
    if state == "neutral":
        return "x"
    return CANDIDATES[task_id][state]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    args = parser.parse_args()
    prompt = args.prompt_file.read_text(encoding="utf-8")
    condition_match = CONDITION_PATTERN.search(prompt)
    policy_match = POLICY_PATTERN.search(prompt)
    if condition_match is None or policy_match is None:
        raise SystemExit("qualification prompt is missing its preregistered markers")
    condition, policy = condition_match.group(1), policy_match.group(1)
    datasets = json.loads(Path("datasets/visible-tasks.json").read_text(encoding="utf-8"))
    protocol = Path("protocol")
    result = Path("result")
    protocol.mkdir(exist_ok=True)
    result.mkdir(exist_ok=True)
    args.artifacts_dir.mkdir(parents=True, exist_ok=True)
    emit("phase.started", phase="orchestrator", emulated_context_count=3)

    records: list[dict[str, Any]] = []
    for phase, state in zip(("A", "B", "C"), _trajectory(condition, policy), strict=True):
        emit("phase.started", phase=phase, context_mode="deterministic-emulation")
        split = "train" if phase == "A" else "validation"
        for task in datasets["tasks"]:
            expression = _candidate(task["id"], state)
            record = {
                "task_id": task["id"],
                "phase": phase,
                "candidate_index": len(records) + 1,
                "expression": expression,
                "visible_split": split,
                "visible_nmse": normalized_mse(expression, task[split]),
                "qualification_state": state,
            }
            records.append(record)
            emit("candidate.evaluated", **record)
        phase_path = protocol / f"phase-{phase.lower()}.json"
        phase_path.write_text(json.dumps(records[-len(datasets["tasks"]):], ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        emit("phase.completed", phase=phase, candidate_count=len(datasets["tasks"]))
        if phase != "C":
            transition = {
                "after_phase": phase,
                "policy": policy,
                "retained": "all" if policy == "open" else "validated-facts" if policy == "gated" else "neutral-placeholder",
                "source_digest": hashlib.sha256(phase_path.read_bytes()).hexdigest(),
            }
            (protocol / f"memory-after-{phase.lower()}.json").write_text(json.dumps(transition, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    candidates = protocol / "candidates.jsonl"
    candidates.write_text("".join(json.dumps(record, sort_keys=True) + "\n" for record in records), encoding="utf-8")
    final = {
        "protocol_version": "qualification-v1",
        "task_count": len(datasets["tasks"]),
        "phase_count": 3,
        "candidate_count": len(records),
        "condition": condition,
        "memory_policy": policy,
        "episode_id": os.environ.get("LLM_STATUS_MACHINE_EPISODE_ID", "unknown"),
        "final_expressions": {record["task_id"]: record["expression"] for record in records if record["phase"] == "C"},
    }
    final_path = result / "final.json"
    final_path.write_text(json.dumps(final, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    shutil.copyfile(candidates, args.artifacts_dir / "candidates.jsonl")
    shutil.copyfile(final_path, args.artifacts_dir / "final.json")
    (args.artifacts_dir / "protocol-summary.json").write_text(json.dumps({"phase_count": 3, "candidate_count": len(records), "memory_policy": policy}, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print("qualification runner completed three fresh-context protocol stages", file=sys.stderr, flush=True)
    emit("result", status="completed", protocol_version="qualification-v1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
