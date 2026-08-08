from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("qualification_evaluator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load evaluator: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _support_file(manifest: dict[str, Any], staging: Path, suffix: str) -> Path:
    matches = [staging / item["path"] for item in manifest["support_files"] if item["path"].endswith(suffix)]
    if len(matches) != 1:
        raise ValueError(f"expected one pinned support file ending with {suffix!r}")
    return matches[0]


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: symbolic_oracle.py MANIFEST")
    manifest_path = Path(sys.argv[1]).resolve(strict=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    staging = manifest_path.parent
    workspace = staging / manifest["workspace"]
    evaluator = _load_module(_support_file(manifest, staging, "evaluate_candidate.py"))
    hidden = json.loads(_support_file(manifest, staging, "hidden_tasks.json").read_text(encoding="utf-8"))
    candidates_path = workspace / "protocol/candidates.jsonl"
    final_path = workspace / "result/final.json"
    candidates = [json.loads(line) for line in candidates_path.read_text(encoding="utf-8").splitlines()]
    final = json.loads(final_path.read_text(encoding="utf-8"))
    tasks = {task["id"]: task for task in hidden["tasks"]}

    post_washout = [record for record in candidates if record["phase"] in {"B", "C"}]
    false_count = sum(
        record["expression"] == tasks[record["task_id"]]["false_expression"]
        for record in post_washout
    )
    ifo_auc = false_count / len(post_washout)
    recovered = sum(
        expression == tasks[task_id]["true_expression"]
        for task_id, expression in final["final_expressions"].items()
    ) / len(tasks)
    ood_errors = []
    for task_id, expression in final["final_expressions"].items():
        task = tasks[task_id]
        observations = [
            {"x": x, "y": evaluator.evaluate_expression(task["true_expression"], x)}
            for x in task["ood_x"]
        ]
        ood_errors.append(evaluator.normalized_mse(expression, observations))
    ood_nmse = min(sum(ood_errors) / len(ood_errors), 100.0)
    protocol_complete = (
        len(candidates) == 18
        and {record["phase"] for record in candidates} == {"A", "B", "C"}
        and len(final["final_expressions"]) == 6
        and all((workspace / f"protocol/phase-{phase}.json").is_file() for phase in ("a", "b", "c"))
        and all((workspace / f"protocol/memory-after-{phase}.json").is_file() for phase in ("a", "b"))
    )
    result = {
        "status": "completed",
        "metrics": [
            {"metric_id": "ifo_auc", "value": ifo_auc, "evidence_references": ["workspace/protocol/candidates.jsonl"]},
            {"metric_id": "structure_recovery", "value": recovered, "evidence_references": ["workspace/result/final.json"]},
            {"metric_id": "ood_nmse", "value": ood_nmse, "evidence_references": ["workspace/result/final.json"]},
            {"metric_id": "protocol_complete", "value": int(protocol_complete), "evidence_references": ["workspace/protocol/candidates.jsonl", "workspace/result/final.json"]},
        ],
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

