from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from llm_status_machine.runtimes.providers import simulator_runtime
from llm_status_machine.study.compiler import compile_study, load_study

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/prior-washout-evidence-gating-study"


def load_module(relative: str, name: str) -> ModuleType:
    path = EXAMPLE / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_codex_argv_uses_restricted_permission_profile_and_stdin(tmp_path: Path) -> None:
    runner = load_module("harness/phase_runner.py", "real_phase_runner")
    argv = runner.build_codex_argv(
        executable=Path("/opt/bin/codex"),
        model="gpt-5.6-sol",
        reasoning_effort="low",
        workspace=tmp_path,
        schema=tmp_path / "schema.json",
        last_message=tmp_path / "last.json",
    )
    joined = "\n".join(argv)
    assert argv[:3] == ["/opt/bin/codex", "exec", "--json"]
    assert "--ephemeral" in argv
    assert "--ignore-user-config" not in argv
    assert "--ignore-rules" in argv
    assert "--skip-git-repo-check" in argv
    assert "--sandbox" not in argv
    assert 'default_permissions="lsm-experiment"' in joined
    assert (
        'permissions.lsm-experiment={description="LSM isolated experiment workspace",'
        'filesystem={":minimal"="read",":workspace_roots"={"."="write"}},'
        "network={enabled=false}}"
    ) in joined
    assert 'shell_environment_policy.inherit="none"' in joined
    assert 'web_search="disabled"' in joined
    assert argv[-1] == "-"


def test_stage_prompts_blind_arm_and_policy_labels() -> None:
    runner = load_module("harness/phase_runner.py", "real_phase_runner_prompts")
    false_a = {
        runner.build_stage_prompt(stage="A", arm=arm, observations={"tasks": []}, memory={})
        for arm in ("false-open", "false-gated", "false-purged")
    }
    assert len(false_a) == 1
    for stage in ("B", "C"):
        prompts = {
            runner.build_stage_prompt(stage=stage, arm=arm, observations={"tasks": []}, memory={})
            for arm in runner.ARM_IDS
        }
        assert len(prompts) == 1
        prompt = next(iter(prompts)).lower()
        for forbidden in ("false-open", "false-gated", "false-purged", "memory_policy", "prior_condition"):
            assert forbidden not in prompt


def test_memory_surgery_rebuilds_from_pristine(tmp_path: Path) -> None:
    policy = load_module("harness/memory_policy.py", "real_memory_policy")
    pristine = tmp_path / "pristine"
    pristine.mkdir()
    (pristine / "README.md").write_text("baseline", encoding="utf-8")
    previous = tmp_path / "previous"
    previous.mkdir()
    (previous / "model-secret-note.md").write_text("unique-stage-a-canary", encoding="utf-8")
    ledger = [{"task_id": "poly", "expression": "x", "visible_nmse": 1.0}]

    open_target = tmp_path / "open"
    policy.transform_workspace("open", pristine, previous, open_target, ledger)
    assert (open_target / "model-secret-note.md").is_file()

    gated_target = tmp_path / "gated"
    policy.transform_workspace("gated", pristine, previous, gated_target, ledger)
    assert not (gated_target / "model-secret-note.md").exists()
    assert json.loads((gated_target / "evidence/validated-ledger.json").read_text()) == ledger

    purged_target = tmp_path / "purged"
    policy.transform_workspace("purged", pristine, previous, purged_target, ledger)
    assert not (purged_target / "model-secret-note.md").exists()
    assert "expression" not in (purged_target / "evidence/neutral-placeholder.json").read_text()


def test_codex_home_preflight_keeps_external_path_out_of_result(tmp_path: Path) -> None:
    runner = load_module("harness/phase_runner.py", "real_phase_runner_home")
    project = tmp_path / "project"
    project.mkdir()
    external = tmp_path / "external-codex-home"
    external.mkdir(mode=0o700)
    result = runner.validate_codex_home(external, project)
    assert result == {"environment_name": "CODEX_HOME", "external": True}

    inside = project / ".codex"
    inside.mkdir()
    with pytest.raises(ValueError, match="outside the project"):
        runner.validate_codex_home(inside, project)


def test_nested_stream_redaction_removes_masked_credential_fragments() -> None:
    runner = load_module("harness/phase_runner.py", "real_phase_runner_redaction")
    raw = b'Incorrect API key provided: sk-example***************tail, request failed'
    sanitized = runner.sanitize_nested_stream(raw)
    assert b"sk-example" not in sanitized
    assert b"tail" not in sanitized
    assert b"[REDACTED_CREDENTIAL]" in sanitized


@pytest.mark.parametrize("arm", ["false-open", "false-gated", "false-purged"])
def test_fake_nested_protocol_runs_three_fresh_processes(tmp_path: Path, arm: str) -> None:
    fake = EXAMPLE / "harness/fake_nested_codex.py"
    fake.chmod(0o755)
    runtime = {
        "surface": "codex_exec_cli",
        "executable": str(fake.resolve()),
        "sha256": hashlib.sha256(fake.read_bytes()).hexdigest(),
        "version_output": "codex-cli 0.0.0-fake",
    }
    lock = tmp_path / "nested-runtime.json"
    lock.write_text(json.dumps(runtime), encoding="utf-8")
    codex_home = tmp_path / "external-codex-home"
    codex_home.mkdir(mode=0o700)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "protocol").mkdir()
    artifacts = tmp_path / "artifacts"
    prompt = EXAMPLE / f"prompts/{arm}.md"
    environment = dict(os.environ)
    environment["CODEX_HOME"] = str(codex_home)

    result = subprocess.run(
        [
            sys.executable,
            str(EXAMPLE / "harness/phase_runner.py"),
            "--prompt-file",
            str(prompt),
            "--artifacts-dir",
            str(artifacts),
            "--nested-runtime",
            str(lock),
            "--model",
            "fake-model",
            "--stage-timeout",
            "10",
        ],
        cwd=workspace,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert events[-1]["type"] == "result"
    final = json.loads((workspace / "result/final.json").read_text())
    assert final["fresh_contexts"] is True
    assert final["candidate_count"] == 54
    identities = json.loads(
        (workspace / ".experiment-evidence/nested-identities.json").read_text()
    )
    assert len({item["pid"] for item in identities}) == 3
    assert len({item["thread_id"] for item in identities}) == 3


@pytest.mark.parametrize(
    ("expression", "family"),
    [
        ("x", "linear"),
        ("x*x + 2*x + 1", "polynomial"),
        ("2*x / (1 + x)", "rational"),
        ("exp(-x)", "exponential"),
        ("sin(x) + 0.2*x", "periodic"),
        ("exp(-x) + 0.5*sin(x)", "combined"),
    ],
)
def test_symbolic_oracle_classifies_expression_motifs(expression: str, family: str) -> None:
    oracle = load_module("oracle_tests/symbolic_oracle.py", "real_symbolic_oracle")
    assert oracle.classify_expression(expression) == family


def test_real_study_generator_references_only_codex_home_name(tmp_path: Path) -> None:
    runtime = simulator_runtime().model_copy(update={"surface": "custom_command"})
    runtime_path = tmp_path / "outer-runtime.json"
    runtime_path.write_text(json.dumps(runtime.model_dump(mode="json")), encoding="utf-8")
    nested = simulator_runtime().model_copy(update={"surface": "codex_exec_cli"})
    nested_path = tmp_path / "nested-runtime.json"
    nested_path.write_text(json.dumps(nested.model_dump(mode="json")), encoding="utf-8")
    codex_home = tmp_path / "external-codex-home"
    codex_home.mkdir(mode=0o700)
    study_path = tmp_path / "study.yml"
    environment = dict(os.environ)
    environment["CODEX_HOME"] = str(codex_home)

    subprocess.run(
        [
            sys.executable,
            str(EXAMPLE / "scripts/prepare_real_study.py"),
            "--runtime",
            str(runtime_path),
            "--nested-runtime",
            str(nested_path),
            "--output",
            str(study_path),
            "--mode",
            "pilot",
            "--repeats",
            "5",
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    raw = study_path.read_text(encoding="utf-8")
    study = yaml.safe_load(raw)
    assert str(codex_home) not in raw
    assert study["profile"]["env_allowlist"] == ["CODEX_HOME"]
    assert study["repeats"] == 5
    assert len(study["prompts"]) == 5
    assert study["study_mode"] == "exploratory"
    plan = compile_study(load_study(study_path))
    assert len(plan.trials) == 25
    assert plan.diagnostics["confirmatory_valid"] is False


def test_real_study_runner_can_parse_declared_failed_run_status() -> None:
    runner = load_module("scripts/run_real_study.py", "real_study_runner")
    command = [
        sys.executable,
        "-c",
        "import json,sys; print(json.dumps({'status':'failed','episodes':['episode-1']})); sys.exit(1)",
    ]
    with pytest.raises(subprocess.CalledProcessError):
        runner.run_json(command, environment=dict(os.environ))
    payload = runner.run_json(
        command,
        environment=dict(os.environ),
        allowed_returncodes=(0, 1),
    )
    assert payload == {"status": "failed", "episodes": ["episode-1"]}


def test_confirmatory_generator_freezes_balanced_default_sample(tmp_path: Path) -> None:
    runtime = simulator_runtime().model_copy(update={"surface": "custom_command"})
    runtime_path = tmp_path / "outer-runtime.json"
    runtime_path.write_text(json.dumps(runtime.model_dump(mode="json")), encoding="utf-8")
    nested = simulator_runtime().model_copy(update={"surface": "codex_exec_cli"})
    nested_path = tmp_path / "nested-runtime.json"
    nested_path.write_text(json.dumps(nested.model_dump(mode="json")), encoding="utf-8")
    codex_home = tmp_path / "external-codex-home"
    codex_home.mkdir(mode=0o700)
    study_path = tmp_path / "confirmatory.yml"
    environment = dict(os.environ)
    environment["CODEX_HOME"] = str(codex_home)

    subprocess.run(
        [
            sys.executable,
            str(EXAMPLE / "scripts/prepare_real_study.py"),
            "--runtime",
            str(runtime_path),
            "--nested-runtime",
            str(nested_path),
            "--output",
            str(study_path),
            "--mode",
            "confirmatory",
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    study = yaml.safe_load(study_path.read_text(encoding="utf-8"))
    plan = compile_study(load_study(study_path))
    assert study["repeats"] == 35
    assert study["seed"] == 20260812
    assert len(plan.trials) == 175
    assert plan.diagnostics["confirmatory_valid"] is True
