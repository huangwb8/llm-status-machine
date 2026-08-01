from __future__ import annotations

from pathlib import Path

import pytest

from llm_status_machine.domain.models import PromptRevision, StudySpec, WorkspaceFixture
from llm_status_machine.runtimes.providers import simulator_runtime


@pytest.fixture
def source_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "source"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Fixture\n", encoding="utf-8")
    return workspace


def make_study(workspace: Path, **changes: object) -> StudySpec:
    values = {
        "name": "test-study",
        "seed": 42,
        "repeats": 1,
        "prompts": [PromptRevision(id="prompt", body="Do the task")],
        "workspace": WorkspaceFixture(path=str(workspace.resolve())),
        "runtime": simulator_runtime(),
    }
    values.update(changes)
    return StudySpec.model_validate(values)
