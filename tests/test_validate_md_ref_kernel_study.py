from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from llm_status_machine.recording.bundle import validate_seal

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
    assert command[:7] == [
        "/opt/bin/codex",
        "exec",
        "--json",
        "--model",
        "gpt-5.6-sol",
        "--sandbox",
        "workspace-write",
    ]
    assert "--ephemeral" in command
    assert "--cd" in command
    assert 'model_reasoning_effort="high"' in command
    assert command[-1] == "hello"


def test_codex_home_prefers_explicit_path_and_accepts_existing_directory(tmp_path: Path) -> None:
    runner = load_runner()
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    assert runner._resolve_codex_home(codex_home) == codex_home.resolve()


def test_codex_home_rejects_missing_explicit_path(tmp_path: Path) -> None:
    runner = load_runner()
    missing = tmp_path / "missing-codex-home"
    try:
        runner._resolve_codex_home(missing)
    except ValueError as error:
        assert "CODEX_HOME" in str(error)
    else:
        raise AssertionError("missing CODEX_HOME must be rejected")


def test_codex_executable_resolves_commands_from_path(monkeypatch, tmp_path: Path) -> None:
    runner = load_runner()
    fake = tmp_path / "codex"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert runner._resolve_codex_executable() == fake.resolve()


def test_optimization_plan_targets_external_skills_project(tmp_path: Path) -> None:
    runner = load_runner()
    skills_root = tmp_path / "skills"
    prompts = runner.build_prompts("2099-01-02-03-04-05", tmp_path / "workspace", tmp_path / "article.md", skills_root)
    expected = str(skills_root / "docs/plans/plan-validate-md-ref-2099-01-02-03-04-05.md")
    assert expected in prompts["evaluate"]
    assert expected in prompts["optimize"]
    assert str(runner.REPOSITORY / "docs/plans") not in prompts["evaluate"]


def test_dry_run_runs_three_lsm_episodes_with_sealed_evidence(tmp_path: Path, capsys) -> None:
    runner = load_runner()
    output_root = tmp_path / "output"
    assert runner.main(["--dry-run", "--output-root", str(output_root)]) == 0
    payload = json.loads((output_root / "validate-md-ref-kernel-study.json").read_text(encoding="utf-8"))
    assert payload["repeats"] == 3
    assert payload["status"] == "completed"
    assert payload["state_policy"] == "carry_forward"
    assert payload["concurrency"] == 1
    assert len(payload["episodes"]) == 3
    assert json.loads(capsys.readouterr().out)["episodes"] == 3

    data_root = output_root / "data"
    run_root = data_root / "runs" / payload["run_id"]
    previous_workspace = None
    for episode_id in payload["episodes"]:
        episode_root = run_root / "episodes" / episode_id
        episode = json.loads((episode_root / "episode.json").read_text(encoding="utf-8"))
        bundle = Path(episode["bundle"])
        assert episode["status"] == "completed"
        assert validate_seal(bundle) == (True, [])
        assert {
            "prompt.md",
            "stdout.raw",
            "stderr.raw",
            "transcript.jsonl",
            "metadata.json",
            "artifacts.json",
            "workspace.initial.json",
            "workspace.final.json",
            "changed-files.json",
            "diff.patch",
        } <= {p.name for p in bundle.iterdir()}
        assert (bundle / "artifacts" / "workflow-summary.json").is_file()
        metadata = json.loads((bundle / "metadata.json").read_text(encoding="utf-8"))
        if previous_workspace:
            assert metadata["source_workspace"] == previous_workspace
        previous_workspace = episode["workspace"]


def test_lsm_custom_worker_can_use_a_real_codex_executable(tmp_path: Path, monkeypatch) -> None:
    runner = load_runner()
    fake = tmp_path / "fake-codex"
    fake.write_text("#!/usr/bin/env python3\nprint('TaskID=2099-01-02-03-04-05')\n", encoding="utf-8")
    fake.chmod(0o755)
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    output_root = tmp_path / "output"
    assert (
        runner.main(
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
        )
        == 0
    )
    payload = json.loads((output_root / "validate-md-ref-kernel-study.json").read_text(encoding="utf-8"))
    assert payload["status"] == "completed"
    assert len(payload["episodes"]) == 1
    episode_root = output_root / "data" / "runs" / payload["run_id"] / "episodes" / payload["episodes"][0]
    transcript = (episode_root / "attempts/attempt-1/raw-bundle/transcript.jsonl").read_text(encoding="utf-8")
    assert "workflow.completed" in transcript
