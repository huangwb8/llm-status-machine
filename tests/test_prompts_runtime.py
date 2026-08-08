from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from conftest import make_study

from llm_status_machine.domain.models import ExecutionProfile, ModelEndpoint, PromptRevision
from llm_status_machine.harnesses.builtin import CodexAdapter, CustomCommandAdapter
from llm_status_machine.prompts.core import freeze_prompt, lint_prompt, render_prompt
from llm_status_machine.runtimes.providers import lock_runtime, simulator_runtime
from llm_status_machine.study.compiler import compile_study


def test_prompt_lint_render_and_freeze() -> None:
    prompt = PromptRevision(id="p", body="Hello {{name}}", variables={"name": "world"})
    assert lint_prompt(prompt) == []
    assert render_prompt(prompt) == "Hello world"
    assert freeze_prompt(prompt)["sha256"] == freeze_prompt(prompt)["sha256"]


def test_prompt_rejects_unbound_variable() -> None:
    prompt = PromptRevision(id="p", body="Hello {{name}}")
    assert lint_prompt(prompt) == ["unbound variable: name"]
    with pytest.raises(ValueError, match="unbound"):
        render_prompt(prompt)


def test_custom_command_rejects_shell() -> None:
    with pytest.raises(ValueError, match="cannot invoke a shell"):
        ExecutionProfile(custom_argv=["bash", "-c", "echo nope"])


def test_simulator_runtime_preserves_environment_interpreter() -> None:
    runtime = simulator_runtime()
    result = subprocess.run(
        [runtime.executable, "-c", "import llm_status_machine"], check=False, capture_output=True
    )
    assert result.returncode == 0, result.stderr.decode()


def test_simulator_runtime_lock_preserves_environment_interpreter() -> None:
    executable = Path(__import__("sys").executable)
    observed = subprocess.run(
        [str(executable), "--version"], check=True, capture_output=True, text=True
    ).stdout.strip()
    version = observed.split()[-1]

    runtime = lock_runtime(surface="simulator", executable=executable, requested_version=version)

    assert runtime.executable == str(executable.absolute())
    result = subprocess.run(
        [runtime.executable, "-c", "import llm_status_machine"], check=False, capture_output=True
    )
    assert result.returncode == 0, result.stderr.decode()


def test_runtime_lock_requires_matching_exact_version(tmp_path: Path) -> None:
    executable = Path(__import__("sys").executable)
    observed = subprocess.run(
        [str(executable), "--version"], check=True, capture_output=True, text=True
    ).stdout.strip()
    version = observed.split()[-1]
    runtime = lock_runtime(surface="custom_command", executable=executable, requested_version=version)
    assert runtime.executable == str(executable.resolve())
    with pytest.raises(ValueError, match="version mismatch"):
        lock_runtime(surface="custom_command", executable=executable, requested_version="0.0-impossible")


def test_custom_argv_must_match_frozen_runtime(source_workspace: Path, tmp_path: Path) -> None:
    runtime = simulator_runtime().model_copy(update={"surface": "custom_command"})
    spec = make_study(
        source_workspace,
        runtime=runtime,
        profile={"custom_argv": ["/different/python", "-c", "print('nope')"], "decoder": "text"},
    )
    trial = compile_study(spec).trials[0]
    with pytest.raises(ValueError, match=r"argv\[0\]"):
        CustomCommandAdapter().build_launch(trial, tmp_path / "prompt.md", tmp_path / "artifacts")


def test_custom_workspace_placeholder_points_to_episode_workspace(
    source_workspace: Path, tmp_path: Path
) -> None:
    runtime = simulator_runtime().model_copy(update={"surface": "custom_command"})
    spec = make_study(
        source_workspace,
        runtime=runtime,
        profile={
            "custom_argv": [runtime.executable, "{workspace}/tools/runner.py"],
            "decoder": "jsonl",
        },
    )
    trial = compile_study(spec).trials[0]
    episode = tmp_path / "runs" / "run-1" / "episodes" / trial.id
    bundle = episode / "attempts" / "attempt-1" / "raw-bundle"

    launch = CustomCommandAdapter().build_launch(
        trial, bundle / "prompt.md", bundle / "artifacts"
    )

    assert launch.argv[1] == str(episode / "workspace" / "tools" / "runner.py")


def test_codex_argv_enforces_profile_and_artifact_scope(
    source_workspace: Path, tmp_path: Path
) -> None:
    runtime = simulator_runtime().model_copy(update={"surface": "codex_exec_cli"})
    spec = make_study(
        source_workspace,
        runtime=runtime,
        endpoint=ModelEndpoint(provider="openai", model_id="gpt-5.6-sol"),
        profile=ExecutionProfile(
            reasoning_effort="medium", permissions="workspace-write", ephemeral=True
        ),
    )
    trial = compile_study(spec).trials[0]
    artifacts = tmp_path / "artifacts"
    launch = CodexAdapter().build_launch(trial, tmp_path / "prompt.md", artifacts)
    assert launch.argv == [
        runtime.executable,
        "exec",
        "--json",
        "--model",
        "gpt-5.6-sol",
        "--sandbox",
        "workspace-write",
        "--add-dir",
        str(artifacts),
        "-c",
        'model_reasoning_effort="medium"',
        "--ephemeral",
        "Do the task",
    ]


def test_codex_rejects_profile_controls_it_cannot_enforce(
    source_workspace: Path, tmp_path: Path
) -> None:
    runtime = simulator_runtime().model_copy(update={"surface": "codex_exec_cli"})
    spec = make_study(
        source_workspace,
        runtime=runtime,
        profile=ExecutionProfile(network="disabled"),
    )
    trial = compile_study(spec).trials[0]
    with pytest.raises(ValueError, match="cannot enforce network=disabled"):
        CodexAdapter().build_launch(trial, tmp_path / "prompt.md", tmp_path / "artifacts")


@pytest.mark.parametrize(
    ("profile", "message"),
    [
        (ExecutionProfile(config_mode="hermetic"), "requires config_mode=workspace_native"),
        (ExecutionProfile(research_mode="controlled"), "requires research_mode=ecological"),
    ],
)
def test_codex_rejects_unsupported_profile_modes(
    source_workspace: Path,
    tmp_path: Path,
    profile: ExecutionProfile,
    message: str,
) -> None:
    runtime = simulator_runtime().model_copy(update={"surface": "codex_exec_cli"})
    trial = compile_study(make_study(source_workspace, runtime=runtime, profile=profile)).trials[0]
    with pytest.raises(ValueError, match=message):
        CodexAdapter().build_launch(trial, tmp_path / "prompt.md", tmp_path / "artifacts")


def test_codex_omits_ephemeral_when_disabled(source_workspace: Path, tmp_path: Path) -> None:
    runtime = simulator_runtime().model_copy(update={"surface": "codex_exec_cli"})
    trial = compile_study(
        make_study(source_workspace, runtime=runtime, profile=ExecutionProfile(ephemeral=False))
    ).trials[0]
    launch = CodexAdapter().build_launch(trial, tmp_path / "prompt.md", tmp_path / "artifacts")
    assert "--ephemeral" not in launch.argv
