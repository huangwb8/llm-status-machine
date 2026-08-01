from __future__ import annotations

from pathlib import Path

import pytest
from conftest import make_study
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from llm_status_machine.domain.models import PromptRevision, StatePolicy
from llm_status_machine.study.compiler import compile_study, load_plan, plan_bytes, write_plan


def test_same_spec_and_seed_produce_identical_plan(source_workspace: Path, tmp_path: Path) -> None:
    spec = make_study(
        source_workspace,
        repeats=3,
        prompts=[PromptRevision(id="a", body="A {{tone}}"), PromptRevision(id="b", body="B {{tone}}")],
        factors={"tone": ["plain", "polite"], "order": [1, 2]},
    )
    first = compile_study(spec)
    second = compile_study(spec)
    assert plan_bytes(first) == plan_bytes(second)
    path = tmp_path / "plan.jsonl"
    write_plan(first, path)
    assert load_plan(path) == first


def test_concurrency_does_not_change_assignment_or_baseline(source_workspace: Path) -> None:
    spec = make_study(source_workspace, repeats=4, factors={"variant": ["a", "b"]})
    serial = compile_study(spec.model_copy(update={"concurrency": 1}))
    parallel = compile_study(spec.model_copy(update={"concurrency": 8}))
    assert [trial.condition for trial in serial.trials] == [trial.condition for trial in parallel.trials]
    assert {trial.workspace.baseline_sha256 for trial in serial.trials} == {
        trial.workspace.baseline_sha256 for trial in parallel.trials
    }


def test_invalid_carry_forward_concurrency_fails_preflight(source_workspace: Path) -> None:
    with pytest.raises(ValidationError, match="carry_forward requires concurrency=1"):
        make_study(source_workspace, concurrency=2, state_policy=StatePolicy.CARRY_FORWARD)


@given(seed=st.integers(min_value=0, max_value=2**32 - 1))
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_seed_is_deterministic(source_workspace: Path, seed: int) -> None:
    spec = make_study(source_workspace, seed=seed, repeats=3, factors={"x": [1, 2, 3]})
    assert plan_bytes(compile_study(spec)) == plan_bytes(compile_study(spec))
