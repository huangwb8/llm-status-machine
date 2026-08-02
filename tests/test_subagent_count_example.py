from __future__ import annotations

import asyncio
import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

from llm_status_machine.utils import write_json
from llm_status_machine.workspaces.backend import WorkspaceBackend

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "subagent-count-quality-study"
    / "scripts"
    / "score_run.py"
)
ORACLE = SCRIPT.parents[1] / "oracle_tests" / "score_cache.py"


def load_score_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("subagent_quality_score_run", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_oracle_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("subagent_quality_oracle", ORACLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("failure_kind", ["exit", "invalid_json", "timeout"])
def test_oracle_failures_have_a_stable_category_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure_kind: str
) -> None:
    module = load_score_module()

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        if failure_kind == "timeout":
            raise subprocess.TimeoutExpired("oracle", 120)
        if failure_kind == "invalid_json":
            return subprocess.CompletedProcess([], 0, stdout="not-json", stderr="")
        return subprocess.CompletedProcess([], 2, stdout="", stderr="oracle crashed")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    score = module.oracle_score(tmp_path)
    fields = module.category_fields(score)
    assert score["scorer_status"] == "failed"
    assert score["score"] is None
    assert list(fields) == [f"score_{name}" for name in module.CATEGORY_NAMES]
    assert set(fields.values()) == {""}


def test_scoring_exports_the_sealed_commit_not_mutated_live_workspace(tmp_path: Path) -> None:
    module = load_score_module()
    source = tmp_path / "source"
    source.mkdir()
    original = "class AsyncTTLCache:\n    pass\n"
    (source / "async_ttl_cache.py").write_text(original, encoding="utf-8")
    (source / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='0'\n", encoding="utf-8")
    (source / "tests").mkdir()
    (source / "tests" / "test_public.py").write_text("# public\n", encoding="utf-8")

    repository = tmp_path / "repository"
    backend = WorkspaceBackend([])
    backend.materialize(source, repository)
    final, _, _ = backend.capture_final(repository)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    write_json(bundle / "workspace.final.json", final)

    (repository / "async_ttl_cache.py").write_text("raise RuntimeError('mutated')\n", encoding="utf-8")
    snapshot = tmp_path / "snapshot"
    metadata = module.export_final_snapshot(bundle, repository, snapshot)

    assert (snapshot / "async_ttl_cache.py").read_text(encoding="utf-8") == original
    assert metadata["git_commit"] == final["git_commit"]
    assert metadata["workspace_manifest_sha256"] == final["sha256"]


def test_generation_oracle_rejects_reusing_invalidated_inflight() -> None:
    oracle = load_oracle_module()

    class ReusesInvalidatedInflight:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}
            self.inflight: dict[str, asyncio.Task[str]] = {}

        async def get_or_load(self, key: str, loader: object) -> str:
            if key not in self.inflight:
                self.inflight[key] = asyncio.create_task(loader())  # type: ignore[operator]
            value = await asyncio.shield(self.inflight[key])
            self.values[key] = value
            return value

        def invalidate(self, key: str) -> None:
            self.values.pop(key, None)

        def get(self, key: str) -> str | None:
            return self.values.get(key)

    with pytest.raises(TimeoutError):
        asyncio.run(oracle.invalidation_starts_a_new_generation(ReusesInvalidatedInflight))
