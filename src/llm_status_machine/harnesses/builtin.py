from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from llm_status_machine.domain.models import Trial
from llm_status_machine.harnesses.base import LaunchSpec


def _environment(trial: Trial, artifacts_dir: Path) -> dict[str, str]:
    allowed = {name: os.environ[name] for name in trial.profile.env_allowlist if name in os.environ}
    allowed.update(
        {
            "PATH": os.environ.get("PATH", ""),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
            "LLM_STATUS_MACHINE_EPISODE_ID": trial.id,
            "LLM_STATUS_MACHINE_ARTIFACTS_DIR": str(artifacts_dir),
        }
    )
    if trial.endpoint.base_url:
        allowed["OPENAI_BASE_URL"] = trial.endpoint.base_url
        allowed["ANTHROPIC_BASE_URL"] = trial.endpoint.base_url
    return allowed


class SimulatorAdapter:
    surface = "simulator"
    capabilities = frozenset({"jsonl", "workspace-write", "artifact", "failure-injection"})

    def build_launch(self, trial: Trial, prompt_file: Path, artifacts_dir: Path) -> LaunchSpec:
        argv = [
            trial.runtime.executable,
            "-m",
            "llm_status_machine.harnesses.simulator",
            "--prompt-file",
            str(prompt_file),
            "--artifacts-dir",
            str(artifacts_dir),
        ]
        return LaunchSpec(
            argv, None, _environment(trial, artifacts_dir), "jsonl", frozenset({"result"}), frozenset({0})
        )

    def normalize_event(self, vendor_event: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        kind = str(vendor_event.get("type", "unknown"))
        return (f"simulator.{kind}" if kind != "unknown" else "vendor.unknown", vendor_event)


class CodexAdapter:
    surface = "codex_exec_cli"
    capabilities = frozenset({"jsonl", "workspace-write", "sandbox", "model", "reasoning-effort"})

    def build_launch(self, trial: Trial, prompt_file: Path, artifacts_dir: Path) -> LaunchSpec:
        argv = [trial.runtime.executable, "exec", "--json", "--model", trial.endpoint.model_id]
        if trial.profile.reasoning_effort:
            argv.extend(["-c", f'model_reasoning_effort="{trial.profile.reasoning_effort}"'])
        argv.append(trial.actual_prompt)
        return LaunchSpec(
            argv,
            None,
            _environment(trial, artifacts_dir),
            "jsonl",
            frozenset({"turn.completed"}),
            frozenset({0}),
        )

    def normalize_event(self, vendor_event: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        kind = str(vendor_event.get("type", "unknown"))
        return (kind if kind != "unknown" else "vendor.unknown", vendor_event)


class ClaudeCodeAdapter:
    surface = "claude_print_cli"
    capabilities = frozenset({"jsonl", "workspace-write", "permissions", "model"})

    def build_launch(self, trial: Trial, prompt_file: Path, artifacts_dir: Path) -> LaunchSpec:
        argv = [
            trial.runtime.executable,
            "-p",
            trial.actual_prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            trial.endpoint.model_id,
        ]
        return LaunchSpec(
            argv, None, _environment(trial, artifacts_dir), "jsonl", frozenset({"result"}), frozenset({0})
        )

    def normalize_event(self, vendor_event: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        kind = str(vendor_event.get("type", "unknown"))
        return (f"claude.{kind}" if kind != "unknown" else "vendor.unknown", vendor_event)


class CustomCommandAdapter:
    surface = "custom_command"
    capabilities = frozenset({"text", "jsonl", "workspace-write", "stdin", "prompt-file"})

    def build_launch(self, trial: Trial, prompt_file: Path, artifacts_dir: Path) -> LaunchSpec:
        if not trial.profile.custom_argv:
            raise ValueError("custom_command requires profile.custom_argv")
        declared = Path(trial.profile.custom_argv[0]).expanduser().absolute()
        runtime = Path(trial.runtime.executable).absolute()
        if declared != runtime:
            raise ValueError("custom argv[0] must equal the frozen runtime executable")
        if runtime.name.lower() in {"sh", "bash", "zsh", "cmd", "powershell", "pwsh", "env"}:
            raise ValueError("custom command cannot use a shell or environment dispatcher as its runtime")
        replacements = {
            "{prompt}": trial.actual_prompt,
            "{prompt_file}": str(prompt_file),
            "{workspace}": str(prompt_file.parent.parent.parent / "workspace"),
            "{artifacts_dir}": str(artifacts_dir),
            "{model}": trial.endpoint.model_id,
        }
        argv = []
        for item in trial.profile.custom_argv:
            for key, value in replacements.items():
                item = item.replace(key, value)
            argv.append(item)
        stdin = trial.actual_prompt.encode() if trial.profile.prompt_transport == "stdin" else None
        return LaunchSpec(
            argv,
            stdin,
            _environment(trial, artifacts_dir),
            trial.profile.decoder,
            frozenset(),
            frozenset(trial.profile.success_exit_codes),
        )

    def normalize_event(self, vendor_event: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        kind = str(vendor_event.get("type", "unknown"))
        return (f"custom.{kind}" if kind != "unknown" else "vendor.unknown", vendor_event)
