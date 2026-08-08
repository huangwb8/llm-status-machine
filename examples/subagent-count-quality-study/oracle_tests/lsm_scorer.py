#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    manifest_path = Path(os.environ["LSM_SCORER_MANIFEST"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    workspace = (manifest_path.parent / manifest["workspace"]).resolve()
    oracle = next(
        (
            manifest_path.parent / item["path"]
            for item in manifest["support_files"]
            if item["path"].endswith("score_cache.py")
        ),
        None,
    )
    if oracle is None:
        raise SystemExit("pinned score_cache.py support file is missing")
    result = subprocess.run(
        [sys.executable, str(oracle), str(workspace)],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        print(json.dumps({"status": "failed", "metrics": [], "error": result.stderr[-1000:]}))
        return
    score = json.loads(result.stdout)
    metrics = [
        {
            "metric_id": "quality_score",
            "value": score["score"],
            "evidence_references": [item["name"] for item in score["tests"] if not item["passed"]],
        }
    ]
    metrics.extend(
        {
            "metric_id": f"category_{name}",
            "value": value,
            "evidence_references": [
                item["name"] for item in score["tests"] if item["category"] == name and not item["passed"]
            ],
        }
        for name, value in score["category_scores"].items()
    )
    print(json.dumps({"status": "completed", "metrics": metrics}, sort_keys=True))


if __name__ == "__main__":
    main()
