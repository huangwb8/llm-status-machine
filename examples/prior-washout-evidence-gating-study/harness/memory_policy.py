from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any


def _copy(source: Path, target: Path) -> None:
    if target.exists():
        raise FileExistsError(target)
    shutil.copytree(source, target, symlinks=False)


def transform_workspace(
    policy: str,
    pristine: Path,
    previous: Path,
    target: Path,
    ledger: list[dict[str, Any]],
) -> dict[str, Any]:
    if policy == "open":
        _copy(previous, target)
        retained = "complete-previous-workspace"
    elif policy == "gated":
        _copy(pristine, target)
        evidence = target / "evidence"
        evidence.mkdir()
        (evidence / "validated-ledger.json").write_text(
            json.dumps(ledger, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        retained = "typed-evaluator-ledger"
    elif policy == "purged":
        _copy(pristine, target)
        evidence = target / "evidence"
        evidence.mkdir()
        placeholder = {
            "schema_version": 1,
            "records": [
                {"sequence": index, "status": "neutral-placeholder"}
                for index, _record in enumerate(ledger, start=1)
            ],
        }
        (evidence / "neutral-placeholder.json").write_text(
            json.dumps(placeholder, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        retained = "neutral-placeholder"
    else:
        raise ValueError(f"unknown memory policy: {policy}")
    return {"policy": policy, "retained": retained, "ledger_records": len(ledger)}

