from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/prior-washout-evidence-gating-study"


def load_evaluator():
    path = EXAMPLE / "fixture/tools/evaluate_candidate.py"
    spec = importlib.util.spec_from_file_location("prior_washout_evaluator", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_expression_evaluator_accepts_grammar_and_rejects_code_execution() -> None:
    evaluator = load_evaluator()
    assert evaluator.evaluate_expression("exp(-x) + 0.5*sin(x)", 0.5) == pytest.approx(0.846243)
    for expression in (
        "__import__('os').system('id')",
        "x.__class__",
        "open('secret')",
        "x[0]",
        "x**1000",
        " + ".join(["x"] * 65),
    ):
        with pytest.raises(evaluator.ExpressionError):
            evaluator.evaluate_expression(expression, 1.0)


def test_prior_washout_qualification_exercises_full_cli_chain(tmp_path: Path) -> None:
    output = tmp_path / "qualification"
    result = subprocess.run(
        [sys.executable, str(EXAMPLE / "scripts/run_qualification.py"), "--root", str(output)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    summary = json.loads(result.stdout)
    assert summary["status"] == "completed"
    assert summary["scope"].startswith("deterministic infrastructure qualification")
    assert summary["episode_count"] == 25
    assert summary["dataset_rows"] == 25
    assert summary["confirmatory_valid"] is True
    assert summary["store_valid_before_reindex"] is True
    assert summary["store_valid_after_reindex"] is True
    assert summary["recorder_qualification"]["scenario_count"] == 4
    assert len(summary["exports"]) == 3
    assert Path(summary["report"]).is_file()

    run_root = output / "data/runs" / summary["run_id"]
    episodes = sorted((run_root / "episodes").glob("*/episode.json"))
    assert len(episodes) == 25
    episode = json.loads(episodes[0].read_text(encoding="utf-8"))
    bundle = Path(episode["bundle"])
    assert {
        "prompt.md",
        "stdout.raw",
        "stderr.raw",
        "transcript.jsonl",
        "artifacts.json",
        "workspace.initial.json",
        "workspace.final.json",
        "changed-files.json",
        "diff.patch",
        "seal.json",
    } <= {path.name for path in bundle.iterdir()}
