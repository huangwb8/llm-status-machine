from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Protocol

from llm_status_machine.domain.models import Trial


@dataclass(frozen=True)
class LaunchSpec:
    argv: list[str]
    stdin: bytes | None
    env: dict[str, str]
    decoder: str
    terminal_kinds: frozenset[str]
    success_exit_codes: frozenset[int]


class HarnessAdapter(Protocol):
    surface: str
    capabilities: frozenset[str]

    def build_launch(self, trial: Trial, prompt_file: Path, artifacts_dir: Path) -> LaunchSpec: ...

    def normalize_event(self, vendor_event: dict[str, Any]) -> tuple[str, dict[str, Any]]: ...


def _builtins() -> dict[str, type[HarnessAdapter]]:
    from llm_status_machine.harnesses.builtin import (
        ClaudeCodeAdapter,
        CodexAdapter,
        CustomCommandAdapter,
        SimulatorAdapter,
    )

    return {
        "simulator": SimulatorAdapter,
        "codex_exec_cli": CodexAdapter,
        "claude_print_cli": ClaudeCodeAdapter,
        "custom_command": CustomCommandAdapter,
    }


def list_adapters() -> dict[str, type[HarnessAdapter]]:
    adapters = _builtins()
    for entry in entry_points(group="llm_status_machine.harnesses"):
        if entry.name not in adapters:
            adapters[entry.name] = entry.load()
    return adapters


def get_adapter(surface: str) -> HarnessAdapter:
    adapter_type = list_adapters().get(surface)
    if adapter_type is None:
        raise ValueError(f"unknown harness surface: {surface}")
    return adapter_type()
