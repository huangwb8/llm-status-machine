from __future__ import annotations

from pathlib import Path

from conftest import make_study

from llm_status_machine.domain.models import StatePolicy
from llm_status_machine.execution.runner import RunEngine
from llm_status_machine.study.compiler import compile_study


async def test_hundred_simulator_episodes_respect_concurrency_limit(
    source_workspace: Path, tmp_path: Path
) -> None:
    spec = make_study(
        source_workspace,
        repeats=100,
        concurrency=8,
        state_policy=StatePolicy.INDEPENDENT,
    )
    engine = RunEngine(tmp_path / "data")
    try:
        run = await engine.run(compile_study(spec))
    finally:
        engine.close()
    assert run["status"] == "completed"
    assert len(run["episodes"]) == 100
    assert 1 < run["max_active_attempts"] <= 8
