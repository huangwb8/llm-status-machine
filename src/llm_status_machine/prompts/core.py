from __future__ import annotations

import re
from typing import Any

from llm_status_machine.domain.models import PromptRevision
from llm_status_machine.utils import sha256_bytes, stable_id

VARIABLE_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def lint_prompt(prompt: PromptRevision, supplied: dict[str, Any] | None = None) -> list[str]:
    supplied = supplied or {}
    declared = set(prompt.variables) | set(supplied)
    referenced = set(VARIABLE_PATTERN.findall(prompt.body))
    errors = [f"unbound variable: {name}" for name in sorted(referenced - declared)]
    unused = set(prompt.variables) - referenced
    errors.extend(f"unused variable: {name}" for name in sorted(unused))
    return errors


def render_prompt(prompt: PromptRevision, values: dict[str, Any] | None = None) -> str:
    merged = {**prompt.variables, **(values or {})}
    errors = lint_prompt(prompt, merged)
    blocking = [error for error in errors if error.startswith("unbound")]
    if blocking:
        raise ValueError("; ".join(blocking))

    def replace(match: re.Match[str]) -> str:
        return str(merged[match.group(1)])

    return VARIABLE_PATTERN.sub(replace, prompt.body)


def freeze_prompt(prompt: PromptRevision, values: dict[str, Any] | None = None) -> dict[str, str]:
    rendered = render_prompt(prompt, values)
    encoded = rendered.encode("utf-8")
    return {
        "revision_id": stable_id("prompt", {"prompt": prompt.model_dump(), "values": values or {}}),
        "body": rendered,
        "encoding": "utf-8",
        "sha256": sha256_bytes(encoded),
        "trailing_newline": str(rendered.endswith("\n")).lower(),
    }
