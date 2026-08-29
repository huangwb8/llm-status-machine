from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "examples/validate-md-ref-kernel-study/run_test.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("validate_md_ref_kernel_study", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_task_id_parser_accepts_codex_output() -> None:
    runner = load_runner()
    assert runner._task_id_from_output('event: "TaskID=2026-08-29-20-59-01"') == "2026-08-29-20-59-01"
    assert runner._task_id_from_output("no identifier") is None


def test_codex_command_pins_model_reasoning_and_workspace(tmp_path: Path) -> None:
    runner = load_runner()
    command = runner._codex_command(
        Path("/opt/bin/codex"),
        model="gpt-5.6-sol",
        reasoning_effort="high",
        workspace=tmp_path / "workspace",
        writable_dirs=[tmp_path / "skills"],
        prompt="hello",
    )
    assert command[:7] == ["/opt/bin/codex", "exec", "--json", "--model", "gpt-5.6-sol", "--sandbox", "workspace-write"]
    assert "--ephemeral" in command
    assert "--cd" in command
    assert 'model_reasoning_effort="high"' in command
    assert command[-1] == "hello"


def test_dry_run_defaults_to_three_unique_iterations(tmp_path: Path, capsys) -> None:
    runner = load_runner()
    output_root = tmp_path / "output"
    assert runner.main(["--dry-run", "--output-root", str(output_root)]) == 0
    payload = json.loads((output_root / "validate-md-ref-kernel-study.json").read_text(encoding="utf-8"))
    assert payload["repeats"] == 3
    assert payload["status"] == "completed"
    iterations = payload["iterations"]
    assert len(iterations) == 3
    assert len({item["task_id"] for item in iterations}) == 3
    assert all(item["optimization"] == "not-needed" for item in iterations)
    assert all((Path(item["workspace"]) / "stages/task_id/prompt.txt").is_file() for item in iterations)
    assert json.loads(capsys.readouterr().out)["iterations"] == 3


def test_codex_reported_task_id_becomes_workspace_name(tmp_path: Path, monkeypatch) -> None:
    runner = load_runner()
    fake = tmp_path / "fake-codex"
    fake.write_text("#!/usr/bin/env python3\nprint('TaskID=2099-01-02-03-04-05')\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    output_root = tmp_path / "output"
    assert runner.main(
        [
            "--repeats",
            "1",
            "--codex-executable",
            str(fake),
            "--output-root",
            str(output_root),
            "--skills-root",
            str(tmp_path / "skills"),
            "--article",
            str(tmp_path / "article.md"),
        ]
    ) == 0
    payload = json.loads((output_root / "validate-md-ref-kernel-study.json").read_text(encoding="utf-8"))
    iteration = payload["iterations"][0]
    assert iteration["task_id"] == "2099-01-02-03-04-05"
    assert Path(iteration["workspace"]).name == "task-validate-md-ref-2099-01-02-03-04-05"
