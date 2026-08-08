from __future__ import annotations

import itertools
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml

from llm_status_machine.domain.models import (
    Design,
    EvaluationSpec,
    PinnedFile,
    RandomizationRecord,
    ScorerSpec,
    StatePolicy,
    StudyMode,
    StudySpec,
    Trial,
    TrialPlan,
)
from llm_status_machine.prompts.core import freeze_prompt
from llm_status_machine.utils import atomic_write, canonical_json, sha256_bytes, sha256_file, stable_id
from llm_status_machine.version import STUDY_SCHEMA_VERSION, TRIAL_PLAN_SCHEMA_VERSION
from llm_status_machine.workspaces.backend import build_manifest


def load_study(path: Path) -> StudySpec:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError("study YAML must contain a mapping")
    version = raw.get("schema_version", 1)
    if not isinstance(version, int) or version < 1:
        raise ValueError(f"invalid study schema_version: {version}")
    if version > STUDY_SCHEMA_VERSION:
        raise ValueError(f"unsupported future study schema_version: {version}")
    if version == 1:
        raw = dict(raw)
        raw["schema_version"] = STUDY_SCHEMA_VERSION
        raw.setdefault("study_mode", StudyMode.EXPLORATORY)
        raw.setdefault("migration_warnings", []).append(
            "v1 StudySpec loaded as exploratory; no confirmatory preregistration was inferred"
        )
    return StudySpec.model_validate(raw)


def _conditions(spec: StudySpec) -> list[dict[str, Any]]:
    if not spec.factors:
        return [{}]
    names = sorted(spec.factors)
    values = [spec.factors[name] for name in names]
    if any(not options for options in values):
        raise ValueError("factor values must not be empty")
    result = [dict(zip(names, combination, strict=True)) for combination in itertools.product(*values)]
    return sorted(result, key=canonical_json)


def _derived_seed(seed: int, payload: Any) -> int:
    digest = sha256_bytes(canonical_json({"seed": seed, "payload": payload}))
    return int(digest[:16], 16)


def _pin_file(value: PinnedFile | None) -> PinnedFile | None:
    if value is None:
        return None
    path = Path(value.path).resolve(strict=True)
    observed = sha256_file(path)
    if value.sha256 and value.sha256 != observed:
        raise ValueError(f"pinned file digest mismatch: {path}")
    return value.model_copy(update={"path": str(path), "sha256": observed})


def _freeze_evaluation(evaluation: EvaluationSpec) -> EvaluationSpec:
    scorers: list[ScorerSpec] = []
    for scorer in evaluation.scorers:
        if scorer.kind != "command":
            scorers.append(scorer)
            continue
        executable = Path(scorer.argv[0]).resolve(strict=True)
        observed = sha256_file(executable)
        if scorer.executable_sha256 and scorer.executable_sha256 != observed:
            raise ValueError(f"scorer executable digest mismatch: {executable}")
        argv = [str(executable), *scorer.argv[1:]]
        support = [item for item in (_pin_file(value) for value in scorer.support_files) if item]
        pinned_paths = {item.path for item in support}
        rubric = _pin_file(scorer.rubric)
        if rubric:
            pinned_paths.add(rubric.path)
        for index, argument in enumerate(argv[1:], start=1):
            candidate = Path(argument)
            if candidate.is_absolute() and candidate.is_file():
                resolved = str(candidate.resolve())
                argv[index] = resolved
                if resolved not in pinned_paths:
                    support.append(PinnedFile(path=resolved, sha256=sha256_file(Path(resolved))))
                    pinned_paths.add(resolved)
            elif candidate.is_absolute() and candidate.exists():
                raise ValueError(f"command scorer argv cannot reference an unpinned directory: {candidate}")
        scorers.append(
            scorer.model_copy(
                update={
                    "argv": argv,
                    "executable_sha256": observed,
                    "rubric": rubric,
                    "support_files": support,
                }
            )
        )
    return evaluation.model_copy(update={"scorers": scorers})


def design_diagnostics(plan: TrialPlan) -> dict[str, Any]:
    arm_counts = Counter(trial.arm_id for trial in plan.trials)
    sets: dict[str, list[Trial]] = defaultdict(list)
    for trial in plan.trials:
        if trial.comparison_set_id:
            sets[trial.comparison_set_id].append(trial)
    expected_arms = set(arm_counts)
    incomplete = [
        set_id
        for set_id, trials in sets.items()
        if len(trials) != len(expected_arms) or {trial.arm_id for trial in trials} != expected_arms
    ]
    positions: dict[str, Counter[int]] = defaultdict(Counter)
    for trial in plan.trials:
        positions[trial.arm_id][trial.sequence_position] += 1
    position_balance = (
        all(
            len(set(counts.values())) <= 1 and set(counts) == set(range(1, len(expected_arms) + 1))
            for counts in positions.values()
        )
        if expected_arms
        else False
    )
    warnings = list(plan.migration_warnings)
    if incomplete:
        warnings.append("one or more comparison sets are incomplete")
    if not position_balance:
        warnings.append("arm sequence positions are not balanced")
    confirmatory_valid = (
        plan.study_mode == StudyMode.CONFIRMATORY
        and not incomplete
        and position_balance
        and plan.state_policy == StatePolicy.INDEPENDENT
        and plan.concurrency == 1
        and bool(plan.analysis.contrasts)
        and any(outcome.role == "primary" for outcome in plan.analysis.outcomes)
    )
    return {
        "study_mode": plan.study_mode.value,
        "confirmatory_valid": confirmatory_valid,
        "arm_counts": dict(sorted(arm_counts.items())),
        "comparison_set_count": len(sets),
        "incomplete_comparison_sets": incomplete,
        "sequence_positions": {
            arm: {str(position): count for position, count in sorted(counts.items())}
            for arm, counts in sorted(positions.items())
        },
        "sequence_balanced": position_balance,
        "primary_outcomes": [item.id for item in plan.analysis.outcomes if item.role == "primary"],
        "contrasts": [item.id for item in plan.analysis.contrasts],
        "warnings": warnings,
    }


def compile_study(spec: StudySpec) -> TrialPlan:
    source = Path(spec.workspace.path).resolve(strict=True)
    baseline = build_manifest(source, set(spec.workspace.excludes))
    workspace = spec.workspace.model_copy(update={"path": str(source), "baseline_sha256": baseline["sha256"]})
    evaluation = _freeze_evaluation(spec.evaluation)
    spec_payload = spec.model_dump(mode="json")
    spec_payload["prompts"] = sorted(spec_payload["prompts"], key=lambda item: item["id"])
    spec_payload["blocks"] = sorted(spec_payload["blocks"], key=canonical_json)
    spec_payload["workspace"]["excludes"] = sorted(spec_payload["workspace"]["excludes"])
    spec_payload["factors"] = {
        name: sorted(values, key=canonical_json) for name, values in sorted(spec_payload["factors"].items())
    }
    spec_payload["workspace"]["path"] = str(source)
    spec_payload["workspace"]["baseline_sha256"] = baseline["sha256"]
    spec_payload["evaluation"] = evaluation.model_dump(mode="json")
    spec_sha256 = sha256_bytes(canonical_json(spec_payload))
    assignment_payload = {
        key: value
        for key, value in spec_payload.items()
        if key
        not in {
            "analysis",
            "concurrency",
            "design_spec",
            "evaluation",
            "migration_warnings",
            "seed",
            "state_policy",
            "study_mode",
        }
    }
    assignment_sha256 = sha256_bytes(canonical_json(assignment_payload))

    prompts = sorted(spec.prompts, key=lambda item: item.id)
    blocks = spec.blocks if spec.design == Design.BLOCK else [{}]
    blocks = sorted(blocks, key=canonical_json)
    comparison_sets: list[dict[str, Any]] = []
    for block in blocks:
        block_id = stable_id("block", block) if spec.design == Design.BLOCK else None
        for condition in _conditions(spec):
            stratum = {"condition": condition, "block": block}
            base = _derived_seed(spec.seed, stratum) % len(prompts)
            for repetition in range(1, spec.repeats + 1):
                set_identity = {
                    "assignment_sha256": assignment_sha256,
                    "repetition": repetition,
                    "condition": condition,
                    "block": block,
                }
                set_id = stable_id("comparison", set_identity)
                rotation = (base + repetition - 1) % len(prompts)
                ordered = prompts[rotation:] + prompts[:rotation]
                comparison_sets.append(
                    {
                        "id": set_id,
                        "pair_id": stable_id("pair", set_identity)
                        if spec.design == Design.MATCHED_PAIR
                        else None,
                        "block_id": block_id,
                        "repetition": repetition,
                        "condition": condition,
                        "block": block,
                        "prompts": ordered,
                        "derived_seed": _derived_seed(spec.seed, set_identity),
                    }
                )

    set_rng = random.Random(_derived_seed(spec.seed, "comparison-set-order"))
    set_rng.shuffle(comparison_sets)
    rows: list[dict[str, Any]] = []
    for draw, comparison_set in enumerate(comparison_sets, start=1):
        for position, prompt in enumerate(comparison_set["prompts"], start=1):
            rendered = freeze_prompt(prompt, {**comparison_set["condition"], **comparison_set["block"]})
            rows.append(
                comparison_set
                | {
                    "prompt": prompt,
                    "actual_prompt": rendered["body"],
                    "prompt_sha256": rendered["sha256"],
                    "position": position,
                    "draw": draw,
                }
            )

    trials: list[Trial] = []
    previous: str | None = None
    for ordinal, row in enumerate(rows, start=1):
        identity = {
            "assignment_sha256": assignment_sha256,
            "comparison_set_id": row["id"],
            "arm_id": row["prompt"].id,
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
                arm_id=row["prompt"].id,
                comparison_set_id=row["id"],
                pair_id=row["pair_id"],
                block_id=row["block_id"],
                sequence_position=row["position"],
                dispatch_batch=(ordinal - 1) // spec.concurrency + 1,
                randomization=RandomizationRecord(
                    algorithm=spec.design_spec.randomization_algorithm,
                    version=spec.design_spec.randomization_version,
                    derived_seed=row["derived_seed"],
                    draw=row["draw"],
                ),
                workspace=workspace,
                runtime=spec.runtime,
                endpoint=spec.endpoint,
                profile=spec.profile,
                parent_trial_id=parent,
            )
        )
        previous = trial_id
    plan_id = stable_id(
        "plan",
        {
            "spec": spec_sha256,
            "trials": [trial.model_dump(mode="json") for trial in trials],
        },
    )
    provisional = TrialPlan(
        id=plan_id,
        study_name=spec.name,
        study_mode=spec.study_mode,
        seed=spec.seed,
        concurrency=spec.concurrency,
        state_policy=spec.state_policy,
        design=spec.design,
        design_spec=spec.design_spec,
        spec_sha256=spec_sha256,
        evaluation=evaluation,
        analysis=spec.analysis,
        diagnostics={"confirmatory_valid": True},
        migration_warnings=spec.migration_warnings,
        trials=trials,
    )
    diagnostics = design_diagnostics(provisional)
    if spec.study_mode == StudyMode.CONFIRMATORY and not diagnostics["confirmatory_valid"]:
        raise ValueError(f"confirmatory design diagnostics failed: {diagnostics['warnings']}")
    return provisional.model_copy(update={"diagnostics": diagnostics})


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
    version = records[0].get("schema_version", 1)
    if not isinstance(version, int) or version < 1:
        raise ValueError(f"invalid trial plan schema_version: {version}")
    if version > TRIAL_PLAN_SCHEMA_VERSION:
        raise ValueError(f"unsupported future trial plan schema_version: {version}")
    legacy = version == 1
    trials = []
    for record in records[1:]:
        if record.pop("record_type", None) != "trial":
            raise ValueError("invalid trial record")
        if legacy:
            record["schema_version"] = TRIAL_PLAN_SCHEMA_VERSION
            record.setdefault("arm_id", record.get("prompt", {}).get("id", ""))
        trials.append(Trial.model_validate(record))
    header = records[0]
    if legacy:
        header = dict(header)
        header.update(
            schema_version=TRIAL_PLAN_SCHEMA_VERSION,
            study_mode=StudyMode.EXPLORATORY,
            diagnostics={"confirmatory_valid": False, "warnings": ["legacy v1 plan"]},
            migration_warnings=[
                "v1 TrialPlan loaded as exploratory; assignment/randomization metadata was not inferred"
            ],
        )
    plan = TrialPlan.model_validate(header | {"trials": trials})
    if not legacy:
        for trial in plan.trials:
            if sha256_bytes(trial.actual_prompt.encode("utf-8")) != trial.prompt_sha256:
                raise ValueError(f"trial prompt digest mismatch: {trial.id}")
        expected_plan_id = stable_id(
            "plan",
            {
                "spec": plan.spec_sha256,
                "trials": [trial.model_dump(mode="json") for trial in plan.trials],
            },
        )
        if plan.id != expected_plan_id:
            raise ValueError("trial plan identity mismatch")
    return plan
