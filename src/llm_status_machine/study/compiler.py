from __future__ import annotations

import itertools
import random
from pathlib import Path
from typing import Any

import yaml

from llm_status_machine.domain.models import Design, StatePolicy, StudySpec, Trial, TrialPlan
from llm_status_machine.prompts.core import freeze_prompt
from llm_status_machine.utils import atomic_write, canonical_json, sha256_bytes, stable_id
from llm_status_machine.workspaces.backend import build_manifest


def load_study(path: Path) -> StudySpec:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError("study YAML must contain a mapping")
    return StudySpec.model_validate(raw)


def _conditions(spec: StudySpec) -> list[dict[str, Any]]:
    if not spec.factors:
        return [{}]
    names = sorted(spec.factors)
    values = [spec.factors[name] for name in names]
    if any(not options for options in values):
        raise ValueError("factor values must not be empty")
    return [dict(zip(names, combination, strict=True)) for combination in itertools.product(*values)]


def compile_study(spec: StudySpec) -> TrialPlan:
    source = Path(spec.workspace.path).resolve(strict=True)
    baseline = build_manifest(source, set(spec.workspace.excludes))
    workspace = spec.workspace.model_copy(update={"path": str(source), "baseline_sha256": baseline["sha256"]})
    spec_payload = spec.model_dump(mode="json")
    spec_payload["workspace"]["path"] = str(source)
    spec_payload["workspace"]["baseline_sha256"] = baseline["sha256"]
    spec_sha256 = sha256_bytes(canonical_json(spec_payload))
    rows: list[dict[str, Any]] = []
    blocks = spec.blocks if spec.design == Design.BLOCK else [{}]
    for repetition in range(1, spec.repeats + 1):
        for block in blocks:
            for condition in _conditions(spec):
                for prompt in spec.prompts:
                    rendered = freeze_prompt(prompt, {**condition, **block})
                    rows.append(
                        {
                            "repetition": repetition,
                            "prompt": prompt,
                            "actual_prompt": rendered["body"],
                            "prompt_sha256": rendered["sha256"],
                            "condition": condition,
                            "block": block,
                        }
                    )
    random.Random(spec.seed).shuffle(rows)
    trials: list[Trial] = []
    previous: str | None = None
    for ordinal, row in enumerate(rows, start=1):
        identity = {
            "spec_sha256": spec_sha256,
            "ordinal": ordinal,
            "repetition": row["repetition"],
            "prompt_sha256": row["prompt_sha256"],
            "condition": row["condition"],
            "block": row["block"],
        }
        trial_id = stable_id("episode", identity)
        parent = previous if spec.state_policy == StatePolicy.CARRY_FORWARD else None
        trials.append(
            Trial(
                id=trial_id,
                ordinal=ordinal,
                repetition=row["repetition"],
                prompt=row["prompt"],
                actual_prompt=row["actual_prompt"],
                prompt_sha256=row["prompt_sha256"],
                condition=row["condition"],
                block=row["block"],
                workspace=workspace,
                runtime=spec.runtime,
                endpoint=spec.endpoint,
                profile=spec.profile,
                parent_trial_id=parent,
            )
        )
        previous = trial_id
    plan_id = stable_id("plan", {"spec": spec_sha256, "trials": [trial.id for trial in trials]})
    return TrialPlan(
        id=plan_id,
        study_name=spec.name,
        seed=spec.seed,
        concurrency=spec.concurrency,
        state_policy=spec.state_policy,
        design=spec.design,
        spec_sha256=spec_sha256,
        trials=trials,
    )


def plan_bytes(plan: TrialPlan) -> bytes:
    header = plan.model_dump(mode="json", exclude={"trials"}) | {"record_type": "plan"}
    lines = [canonical_json(header)]
    lines.extend(
        canonical_json(trial.model_dump(mode="json") | {"record_type": "trial"}) for trial in plan.trials
    )
    return b"".join(lines)


def write_plan(plan: TrialPlan, path: Path) -> None:
    atomic_write(path, plan_bytes(plan))


def load_plan(path: Path) -> TrialPlan:
    records = [yaml.safe_load(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not records or records[0].pop("record_type", None) != "plan":
        raise ValueError("invalid plan header")
    trials = []
    for record in records[1:]:
        if record.pop("record_type", None) != "trial":
            raise ValueError("invalid trial record")
        trials.append(Trial.model_validate(record))
    return TrialPlan.model_validate(records[0] | {"trials": trials})
