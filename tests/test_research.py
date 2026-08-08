from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from conftest import make_study
from pydantic import ValidationError
from typer.testing import CliRunner

from llm_status_machine.analysis.dataset import build_dataset
from llm_status_machine.analysis.inference import _analyze_contrast, infer_run, render_report
from llm_status_machine.analysis.multiplicity import holm_adjust
from llm_status_machine.analysis.power import estimate_power
from llm_status_machine.cli.app import app
from llm_status_machine.domain.models import (
    AnalysisSpec,
    ContrastSpec,
    Design,
    EvaluationSpec,
    MetricSpec,
    MetricType,
    OutcomeSpec,
    PromptRevision,
    ScorerSpec,
    StatePolicy,
    StudyMode,
    TrialPlan,
)
from llm_status_machine.evaluation.agreement import krippendorff_alpha
from llm_status_machine.evaluation.runner import evaluate_run
from llm_status_machine.execution.runner import RunEngine
from llm_status_machine.recording.bundle import RawBundle, validate_seal
from llm_status_machine.study.compiler import compile_study, load_plan, load_study, plan_bytes, write_plan
from llm_status_machine.utils import read_json, write_json


def research_contract(*, scorer: ScorerSpec | None = None) -> tuple[EvaluationSpec, AnalysisSpec]:
    metric = MetricSpec(
        id="quality", type=MetricType.CONTINUOUS, direction="higher", lower_bound=0, upper_bound=1
    )
    scorer = scorer or ScorerSpec(id="quality-scorer", metrics=[metric])
    outcome = OutcomeSpec(
        id="primary-quality",
        scorer_id=scorer.id,
        metric_id="quality",
        role="primary",
        type=MetricType.CONTINUOUS,
        direction="higher",
        lower_bound=0,
        upper_bound=1,
        failure_policy="worst_case",
        missing_policy="error",
    )
    analysis = AnalysisSpec(
        outcomes=[outcome],
        contrasts=[
            ContrastSpec(
                id="treatment-v-control",
                outcome_id=outcome.id,
                treatment_arm="treatment",
                control_arm="control",
                estimand="mean_difference",
            )
        ],
        permutations=500,
        bootstrap_samples=200,
        seed=17,
    )
    return EvaluationSpec(scorers=[scorer]), analysis


def confirmatory_spec(source_workspace: Path, **changes: object):
    evaluation, analysis = research_contract()
    values: dict[str, object] = {
        "study_mode": StudyMode.CONFIRMATORY,
        "design": Design.MATCHED_PAIR,
        "state_policy": StatePolicy.INDEPENDENT,
        "concurrency": 1,
        "repeats": 4,
        "prompts": [
            PromptRevision(id="control", body="CONTROL"),
            PromptRevision(id="treatment", body="TREATMENT"),
        ],
        "evaluation": evaluation,
        "analysis": analysis,
    }
    values.update(changes)
    return make_study(source_workspace, **values)


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"state_policy": StatePolicy.CARRY_FORWARD}, "state_policy=independent"),
        ({"concurrency": 2}, "concurrency=1"),
        ({"repeats": 3}, "balance every arm"),
        ({"analysis": AnalysisSpec()}, "primary outcome"),
    ],
)
def test_confirmatory_preflight_rejects_invalid_design(
    source_workspace: Path, changes: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        confirmatory_spec(source_workspace, **changes)


def test_metric_aggregation_and_policy_values_are_validated() -> None:
    with pytest.raises(ValidationError, match="numeric aggregation"):
        ScorerSpec(
            id="nominal-scorer",
            aggregation="mean",
            metrics=[MetricSpec(id="label", type=MetricType.NOMINAL, direction="higher")],
        )
    with pytest.raises(ValidationError, match="failure_value.*bounds"):
        OutcomeSpec(
            id="binary-outcome",
            scorer_id="scorer",
            metric_id="flag",
            type=MetricType.BINARY,
            direction="higher",
            lower_bound=0,
            upper_bound=1,
            failure_policy="value",
            failure_value=2,
            missing_policy="error",
        )


def test_confirmatory_primary_outcomes_require_predeclared_contrasts(
    source_workspace: Path,
) -> None:
    evaluation, analysis = research_contract()
    uncovered = analysis.outcomes[0].model_copy(update={"id": "uncovered-primary"})
    analysis = analysis.model_copy(update={"outcomes": [*analysis.outcomes, uncovered]})

    with pytest.raises(ValidationError, match="primary outcomes require predeclared contrasts"):
        confirmatory_spec(source_workspace, evaluation=evaluation, analysis=analysis)


def test_balanced_assignment_is_stable_and_input_order_independent(source_workspace: Path) -> None:
    spec = confirmatory_spec(source_workspace)
    first = compile_study(spec)
    reversed_prompts = spec.model_copy(update={"prompts": list(reversed(spec.prompts))})
    second = compile_study(reversed_prompts)
    assert plan_bytes(first) == plan_bytes(second)
    assert first.diagnostics["confirmatory_valid"] is True
    assert first.diagnostics["sequence_positions"] == {
        "control": {"1": 2, "2": 2},
        "treatment": {"1": 2, "2": 2},
    }
    changed_seed = compile_study(spec.model_copy(update={"seed": spec.seed + 1}))
    assert {trial.id for trial in changed_seed.trials} == {trial.id for trial in first.trials}
    assert plan_bytes(changed_seed) != plan_bytes(first)
    assert sorted(changed_seed.diagnostics["arm_counts"].values()) == [4, 4]
    parallel = compile_study(spec.model_copy(update={"study_mode": StudyMode.EXPLORATORY, "concurrency": 4}))
    assert {trial.id for trial in parallel.trials} == {trial.id for trial in first.trials}


def test_confirmatory_plan_rejects_duplicate_arms_within_comparison_set(
    source_workspace: Path,
) -> None:
    plan = compile_study(confirmatory_spec(source_workspace))
    target_set = plan.trials[0].comparison_set_id
    target_pair = plan.trials[0].pair_id
    templates = {
        arm: next(trial for trial in plan.trials if trial.arm_id == arm)
        for arm in ("control", "treatment")
    }
    trials = [trial.model_dump(mode="json") for trial in plan.trials]
    for arm in ("control", "treatment"):
        for position in (1, 2):
            template = templates[arm].model_dump(mode="json")
            template.update(
                id=f"episode-duplicate-{arm}-{position}",
                ordinal=len(trials) + 1,
                comparison_set_id=target_set,
                pair_id=target_pair,
                sequence_position=position,
            )
            trials.append(template)

    with pytest.raises(ValidationError, match="exactly one trial per arm"):
        TrialPlan.model_validate(plan.model_dump(mode="json", exclude={"trials"}) | {"trials": trials})


def test_v1_is_read_as_exploratory_and_future_schema_is_rejected(
    source_workspace: Path, tmp_path: Path
) -> None:
    spec = make_study(source_workspace)
    raw = spec.model_dump(mode="json")
    raw["schema_version"] = 1
    for field in ("study_mode", "design_spec", "evaluation", "analysis", "migration_warnings"):
        raw.pop(field)
    path = tmp_path / "legacy.yml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    migrated = load_study(path)
    assert migrated.study_mode == StudyMode.EXPLORATORY
    assert migrated.migration_warnings

    plan = compile_study(spec)
    plan_path = tmp_path / "future.jsonl"
    write_plan(plan, plan_path)
    lines = [json.loads(line) for line in plan_path.read_text().splitlines()]
    lines[0]["schema_version"] = 999
    plan_path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    with pytest.raises(ValueError, match="future trial plan"):
        load_plan(plan_path)

    tampered_path = tmp_path / "tampered.jsonl"
    write_plan(plan, tampered_path)
    tampered = [json.loads(line) for line in tampered_path.read_text().splitlines()]
    tampered[1]["profile"]["timeout_seconds"] += 1
    tampered_path.write_text("".join(json.dumps(line) + "\n" for line in tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="plan identity mismatch"):
        load_plan(tampered_path)


def test_new_cli_json_errors_use_a_stable_contract(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["research", "dataset", "missing-run", "--data-root", str(tmp_path), "--json"],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert payload["confirmatory_valid"] is False
    assert payload["warnings"] == []
    assert payload["errors"] and "run not found" in payload["errors"][0]


def test_future_raw_bundle_schema_is_rejected(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    raw = RawBundle(raw_root)
    raw.write_bytes("payload.txt", b"evidence")
    seal = raw.seal(run_id="run", episode_id="episode", attempt_id="attempt")
    seal["schema_version"] = 999
    write_json(raw_root / "seal.json", seal)
    valid, errors = validate_seal(raw_root)
    assert valid is False
    assert any("unsupported RawBundle" in error for error in errors)


def test_holm_and_two_arm_power_calculations_are_fixed() -> None:
    assert holm_adjust([0.01, 0.02, 0.04]) == pytest.approx([0.03, 0.04, 0.04])
    continuous = estimate_power(
        metric_type="continuous",
        effect=5,
        standard_deviation=10,
        baseline_rate=None,
        alpha=0.05,
        power=0.8,
    )
    binary = estimate_power(
        metric_type="binary",
        effect=0.1,
        standard_deviation=None,
        baseline_rate=0.5,
        alpha=0.05,
        power=0.8,
    )
    assert continuous["required_episodes_per_arm"] == 63
    assert binary["required_episodes_per_arm"] > 0


def test_krippendorff_alpha_weights_units_with_missing_ratings() -> None:
    rows = [[0, 1], [0, 0, 1], [1, 1, None]]

    assert krippendorff_alpha(rows, "nominal") == pytest.approx(0.0)


def _write_scorer(path: Path, *, valid: bool = True) -> None:
    output = (
        "print('{not-json')"
        if not valid
        else """manifest = json.loads(pathlib.Path(os.environ['LSM_SCORER_MANIFEST']).read_text())
assert 'arm_id' not in manifest and 'condition' not in manifest and 'ordinal' not in manifest
try:
    (pathlib.Path(manifest['workspace']) / 'README.md').write_text('mutated')
    raise AssertionError('workspace was writable')
except OSError:
    pass
value = 1 if 'TREATMENT' in (manifest.get('prompt') or '') else 0
print(json.dumps({'status': 'completed', 'metrics': [
    {'metric_id': 'quality', 'value': value, 'evidence_references': ['workspace/README.md']}
]}))"""
    )
    path.write_text(
        "#!/usr/bin/env python3\nimport json, os, pathlib\n" + output + "\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


async def _run_spec(spec, data_root: Path) -> tuple[dict[str, object], Path]:
    plan = compile_study(spec)
    engine = RunEngine(data_root)
    try:
        run = await engine.run(plan)
    finally:
        engine.close()
    return run, data_root / "runs" / str(run["id"])


async def test_blind_command_evaluation_dataset_and_paired_inference_are_reproducible(
    source_workspace: Path, tmp_path: Path
) -> None:
    script = tmp_path / "scorer.py"
    _write_scorer(script)
    metric = MetricSpec(
        id="quality", type=MetricType.CONTINUOUS, direction="higher", lower_bound=0, upper_bound=1
    )
    scorer = ScorerSpec(
        id="quality-scorer",
        kind="command",
        version="fixture-v1",
        argv=[str(script.resolve())],
        include_prompt=True,
        repetitions=2,
        metrics=[metric],
    )
    evaluation, analysis = research_contract(scorer=scorer)
    spec = confirmatory_spec(source_workspace, evaluation=evaluation, analysis=analysis)
    run, run_root = await _run_spec(spec, tmp_path / "data")
    seals = {
        episode_id: read_json(run_root / "episodes" / episode_id / "episode.json")["bundle_sha256"]
        for episode_id in run["episodes"]
    }
    summary = evaluate_run(run_root)
    assert summary["status"] == "completed"
    assert summary["evaluation_count"] == 16
    assert summary["agreement"]["quality-scorer.quality"] == 1.0
    assert summary["warnings"]
    for episode_id, digest in seals.items():
        episode_root = run_root / "episodes" / episode_id
        episode = read_json(episode_root / "episode.json")
        assert episode["bundle_sha256"] == digest
        assert validate_seal(Path(episode["bundle"])) == (True, [])

    dataset = build_dataset(run_root)
    assert dataset["row_count"] == 8
    analysis_result = infer_run(run_root, dataset)
    contrast = analysis_result["contrasts"][0]
    assert analysis_result["confirmatory_valid"] is True
    assert contrast["effect"] == 1.0
    assert contrast["confidence_interval"] == [1.0, 1.0]
    assert contrast["raw_p_value"] == pytest.approx(0.125)
    assert build_dataset(run_root)["id"] == dataset["id"]
    assert infer_run(run_root, dataset)["analysis_id"] == analysis_result["analysis_id"]

    (Path(dataset["path"]) / "observations.jsonl").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="modified"):
        build_dataset(run_root)


async def test_scorer_tampering_and_invalid_json_are_first_class_failures(
    source_workspace: Path, tmp_path: Path
) -> None:
    for mode in ("tampered", "invalid", "nonzero", "timeout", "out_of_bounds"):
        root = tmp_path / mode
        root.mkdir()
        script = root / "scorer.py"
        _write_scorer(script, valid=mode != "invalid")
        if mode == "nonzero":
            script.write_text("#!/usr/bin/env python3\nraise SystemExit(3)\n", encoding="utf-8")
            script.chmod(0o755)
        elif mode == "timeout":
            script.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n", encoding="utf-8")
            script.chmod(0o755)
        elif mode == "out_of_bounds":
            script.write_text(
                "#!/usr/bin/env python3\nimport json\n"
                "print(json.dumps({'status':'completed','metrics':["
                "{'metric_id':'quality','value':2,'evidence_references':[]}]}))\n",
                encoding="utf-8",
            )
            script.chmod(0o755)
        metric = MetricSpec(
            id="quality", type=MetricType.CONTINUOUS, direction="higher", lower_bound=0, upper_bound=1
        )
        scorer = ScorerSpec(
            id="quality-scorer",
            kind="command",
            argv=[str(script.resolve())],
            include_prompt=True,
            timeout_seconds=0.1 if mode == "timeout" else 120,
            metrics=[metric],
        )
        evaluation, analysis = research_contract(scorer=scorer)
        spec = confirmatory_spec(source_workspace, evaluation=evaluation, analysis=analysis)
        plan = compile_study(spec)
        if mode == "tampered":
            script.write_text("#!/usr/bin/env python3\nprint('changed')\n", encoding="utf-8")
            script.chmod(0o755)
        engine = RunEngine(root / "data")
        try:
            run = await engine.run(plan)
        finally:
            engine.close()
        summary = evaluate_run(root / "data" / "runs" / run["id"])
        assert summary["status"] == "failed"
        assert len(summary["failures"]) == 8


async def test_run_evaluation_reports_missing_planned_episodes(
    source_workspace: Path, tmp_path: Path
) -> None:
    spec = confirmatory_spec(source_workspace)
    run, run_root = await _run_spec(spec, tmp_path / "data")
    missing_episode = str(run["episodes"][0])
    shutil.rmtree(run_root / "episodes" / missing_episode)

    summary = evaluate_run(run_root)

    assert summary["status"] == "failed"
    assert summary["confirmatory_valid"] is False
    assert summary["missing_evaluation_count"] == 1
    assert summary["missing_episode_ids"] == [missing_episode]
    assert not (run_root / "blinding" / "map.json").exists()


async def test_dataset_rejects_duplicate_planned_evaluation(
    source_workspace: Path, tmp_path: Path
) -> None:
    script = tmp_path / "scorer.py"
    _write_scorer(script)
    scorer = ScorerSpec(
        id="quality-scorer",
        kind="command",
        argv=[str(script.resolve())],
        include_prompt=True,
        metrics=[
            MetricSpec(
                id="quality",
                type=MetricType.CONTINUOUS,
                direction="higher",
                lower_bound=0,
                upper_bound=1,
            )
        ],
    )
    evaluation, analysis = research_contract(scorer=scorer)
    spec = confirmatory_spec(source_workspace, evaluation=evaluation, analysis=analysis)
    _run, run_root = await _run_spec(spec, tmp_path / "data")
    assert evaluate_run(run_root)["status"] == "completed"
    evaluation_root = next((run_root / "episodes").glob("*/evaluations/*"))
    shutil.copytree(evaluation_root, evaluation_root.parent / "duplicate-evaluation")

    with pytest.raises(ValueError, match="duplicate planned evaluation"):
        build_dataset(run_root)


async def test_dataset_rejects_episode_assignment_mismatch(
    source_workspace: Path, tmp_path: Path
) -> None:
    spec = confirmatory_spec(source_workspace)
    run, run_root = await _run_spec(spec, tmp_path / "data")
    episode_path = run_root / "episodes" / str(run["episodes"][0]) / "episode.json"
    episode = read_json(episode_path)
    episode["comparison_set_id"] = "comparison-tampered"
    write_json(episode_path, episode)

    with pytest.raises(ValueError, match="episode assignment mismatch"):
        build_dataset(run_root)


def test_missing_policy_error_refuses_inference(source_workspace: Path) -> None:
    spec = confirmatory_spec(source_workspace)
    plan = compile_study(spec)
    rows = [
        {
            "episode_id": trial.id,
            "arm_id": trial.arm_id,
            "comparison_set_id": trial.comparison_set_id,
            "condition": trial.condition,
            "block_id": trial.block_id,
            "episode_status": "completed",
            "metric.quality-scorer.quality": None
            if trial.id == plan.trials[0].id
            else (1.0 if trial.arm_id == "treatment" else 0.0),
        }
        for trial in plan.trials
    ]

    with pytest.raises(ValueError, match="missing_policy=error"):
        _analyze_contrast(
            rows, plan, plan.analysis.outcomes[0], plan.analysis.contrasts[0], plan.analysis
        )


def test_research_report_includes_standardized_effect() -> None:
    report = render_report(
        {
            "analysis_id": "analysis-test",
            "status": "completed",
            "confirmatory_valid": True,
            "alpha": 0.05,
            "warnings": [],
            "contrasts": [
                {
                    "contrast_id": "quality",
                    "treatment_arm": "treatment",
                    "control_arm": "control",
                    "effect": 2.0,
                    "standardized_effect": 0.5,
                    "confidence_interval": [1.0, 3.0],
                    "planned_n": 8,
                    "valid_scored_n": 8,
                    "failed_n": 0,
                    "missing_n": 0,
                    "raw_p_value": 0.02,
                    "adjusted_p_value": 0.02,
                }
            ],
        }
    )

    assert "Standardized effect: `0.5`" in report


@pytest.mark.parametrize("design", [Design.FULL_FACTORIAL, Design.MATCHED_PAIR, Design.BLOCK])
def test_each_supported_design_uses_its_predeclared_inference_path(
    source_workspace: Path, design: Design
) -> None:
    changes: dict[str, object] = {"design": design}
    if design == Design.BLOCK:
        changes["blocks"] = [{"window": "early"}, {"window": "late"}]
    spec = confirmatory_spec(source_workspace, **changes)
    plan = compile_study(spec)
    rows = [
        {
            "episode_id": trial.id,
            "arm_id": trial.arm_id,
            "comparison_set_id": trial.comparison_set_id,
            "condition": trial.condition,
            "block_id": trial.block_id,
            "episode_status": "completed",
            "metric.quality-scorer.quality": 1.0 if trial.arm_id == "treatment" else 0.0,
        }
        for trial in plan.trials
    ]
    result = _analyze_contrast(
        rows, plan, plan.analysis.outcomes[0], plan.analysis.contrasts[0], plan.analysis
    )
    assert result["status"] == "completed"
    assert result["effect"] == 1.0
    if design == Design.FULL_FACTORIAL:
        assert "stratified_permutation" in result["test"]["method"]
    elif design == Design.MATCHED_PAIR:
        assert "sign_flip" in result["test"]["method"]
    else:
        assert "block_permutation" in result["test"]["method"]
