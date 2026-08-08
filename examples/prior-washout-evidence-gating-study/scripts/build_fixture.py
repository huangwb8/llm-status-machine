from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "fixture/tools"
sys.path.insert(0, str(TOOLS))

from evaluate_candidate import evaluate_expression

TASKS = {
    "poly": "x**2 + 2*x + 1",
    "rational": "x / (1 + x)",
    "decay": "exp(-x)",
    "periodic": "sin(x) + 0.2*x",
    "saturation": "2*x / (1 + x)",
    "combined": "exp(-x) + 0.5*sin(x)",
}
TRAIN_X = [0.1, 0.25, 0.4, 0.55, 0.7, 0.85, 1.0]
VALIDATION_X = [1.1, 1.25, 1.4, 1.55]


def observations(expression: str, values: list[float]) -> list[dict[str, float]]:
    return [{"x": x, "y": evaluate_expression(expression, x)} for x in values]


def main() -> None:
    payload = {
        "schema_version": 1,
        "note": "No hidden OOD points or structural truth are stored in the episode workspace.",
        "tasks": [
            {
                "id": task_id,
                "train": observations(expression, TRAIN_X),
                "validation": observations(expression, VALIDATION_X),
            }
            for task_id, expression in TASKS.items()
        ],
    }
    output = ROOT / "fixture/datasets/visible-tasks.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "tasks": len(TASKS)}, sort_keys=True))


if __name__ == "__main__":
    main()
