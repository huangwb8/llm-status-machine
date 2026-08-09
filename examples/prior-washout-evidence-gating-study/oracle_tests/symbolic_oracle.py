from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


def _contains_x(node: ast.AST) -> bool:
    return any(isinstance(item, ast.Name) and item.id == "x" for item in ast.walk(node))


def _polynomial_degree(node: ast.AST) -> int | None:
    if isinstance(node, ast.Constant):
        return 0
    if isinstance(node, ast.Name) and node.id == "x":
        return 1
    if isinstance(node, ast.UnaryOp):
        return _polynomial_degree(node.operand)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
        left, right = _polynomial_degree(node.left), _polynomial_degree(node.right)
        return None if left is None or right is None else max(left, right)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        left, right = _polynomial_degree(node.left), _polynomial_degree(node.right)
        return None if left is None or right is None else left + right
    if (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Pow)
        and isinstance(node.right, ast.Constant)
        and isinstance(node.right.value, int)
        and node.right.value >= 0
    ):
        base = _polynomial_degree(node.left)
        return None if base is None else base * node.right.value
    return None


def classify_expression(expression: str) -> str:
    tree = ast.parse(expression, mode="eval")
    functions = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    if "exp" in functions and functions & {"sin", "cos"}:
        return "combined"
    if functions & {"sin", "cos"}:
        return "periodic"
    if "exp" in functions:
        return "exponential"
    if any(
        isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and _contains_x(node.right)
        for node in ast.walk(tree)
    ):
        return "rational"
    degree = _polynomial_degree(tree.body)
    if degree is not None and degree >= 2:
        return "polynomial"
    if degree == 1:
        return "linear"
    return "other"


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
    tasks = {task["id"]: task for task in hidden["tasks"]}
    candidates_path = workspace / "protocol/candidates.jsonl"
    final_path = workspace / "result/final.json"
    candidates = (
        [json.loads(line) for line in candidates_path.read_text(encoding="utf-8").splitlines()]
        if candidates_path.is_file()
        else []
    )
    final = json.loads(final_path.read_text(encoding="utf-8")) if final_path.is_file() else {}
    expressions = final.get("final_expressions", {})
    version = final.get("protocol_version")

    post_washout = [record for record in candidates if record.get("stage", record.get("phase")) in {"B", "C"}]
    false_count = 0
    for record in post_washout:
        task = tasks.get(record.get("task_id"))
        try:
            family = classify_expression(record["expression"])
        except (SyntaxError, ValueError, TypeError):
            family = "invalid"
        false_count += int(task is not None and family == task["false_family"])
    ifo_auc = false_count / len(post_washout) if post_washout else 1.0
    recovered = sum(
        classify_expression(expression) == tasks[task_id]["true_family"]
        for task_id, expression in expressions.items()
        if task_id in tasks
    ) / len(tasks)
    ood_errors = []
    for task_id, task in tasks.items():
        expression = expressions.get(task_id)
        if not expression:
            ood_errors.append(100.0)
            continue
        observations = [
            {"x": x, "y": evaluator.evaluate_expression(task["true_expression"], x)}
            for x in task["ood_x"]
        ]
        try:
            ood_errors.append(min(evaluator.normalized_mse(expression, observations), 100.0))
        except (ArithmeticError, ValueError):
            ood_errors.append(100.0)
    ood_nmse = sum(ood_errors) / len(ood_errors)
    if version == "qualification-v1":
        protocol_complete = (
            len(candidates) == 18
            and {record.get("phase") for record in candidates} == {"A", "B", "C"}
            and len(expressions) == 6
            and all((workspace / f"protocol/phase-{phase}.json").is_file() for phase in ("a", "b", "c"))
            and all((workspace / f"protocol/memory-after-{phase}.json").is_file() for phase in ("a", "b"))
        )
    else:
        protocol_complete = (
            version == "real-codex-v1"
            and len(candidates) == 54
            and {record.get("stage") for record in candidates} == {"A", "B", "C"}
            and len(expressions) == 6
            and bool(final.get("fresh_contexts"))
            and all((workspace / f".experiment-evidence/phase-{phase}.json").is_file() for phase in ("a", "b", "c"))
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
