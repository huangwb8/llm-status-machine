from __future__ import annotations

import itertools
import math
import platform
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from filelock import FileLock

from llm_status_machine.analysis.multiplicity import holm_adjust
from llm_status_machine.domain.models import AnalysisSpec, ContrastSpec, OutcomeSpec, TrialPlan
from llm_status_machine.storage.index import IndexStore
from llm_status_machine.utils import (
    atomic_write,
    canonical_json,
    read_json,
    sha256_bytes,
    sha256_file,
    stable_id,
    utc_now,
    write_json,
)
from llm_status_machine.version import ANALYSIS_SCHEMA_VERSION, RUN_SCHEMA_VERSION, __version__


def _numpy() -> Any:
    try:
        import numpy as np
    except ImportError as error:  # pragma: no cover - exercised in an environment without the extra
        raise RuntimeError("statistical inference requires: uv sync --extra analysis") from error
    return np


def _load_rows(dataset_root: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    path = dataset_root / "observations.jsonl"
    if sha256_file(path) != manifest["files"]["observations.jsonl"]:
        raise ValueError("observation dataset digest mismatch")
    return [__import__("json").loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _policy_value(row: dict[str, Any], outcome: OutcomeSpec) -> tuple[float | None, str | None]:
    value = row.get(f"metric.{outcome.scorer_id}.{outcome.metric_id}")
    if row["episode_status"] != "completed":
        policy, explicit, reason = outcome.failure_policy, outcome.failure_value, "episode_failure"
    elif value is None:
        policy, explicit, reason = outcome.missing_policy, outcome.missing_value, "metric_missing"
    else:
        return float(value), None
    if policy == "value":
        return float(explicit), reason
    if policy == "worst_case":
        bound = outcome.lower_bound if outcome.direction == "higher" else outcome.upper_bound
        return float(bound), reason
    if policy == "error":
        raise ValueError(
            f"missing_policy=error rejected {reason} for episode {row.get('episode_id', 'unknown')}"
        )
    return None, reason


def _effect(treatment: list[float], control: list[float]) -> float:
    return sum(treatment) / len(treatment) - sum(control) / len(control)


def _paired_units(
    rows: list[dict[str, Any]], contrast: ContrastSpec, outcome: OutcomeSpec
) -> tuple[list[float], dict[str, int]]:
    sets: dict[str, dict[str, float]] = defaultdict(dict)
    reasons = CounterLike()
    for row in rows:
        if row["arm_id"] not in {contrast.treatment_arm, contrast.control_arm}:
            continue
        value, reason = _policy_value(row, outcome)
        if reason:
            reasons[reason] += 1
        if value is not None:
            sets[row["comparison_set_id"]][row["arm_id"]] = value
    diffs = [
        values[contrast.treatment_arm] - values[contrast.control_arm]
        for values in sets.values()
        if contrast.treatment_arm in values and contrast.control_arm in values
    ]
    reasons["incomplete_units"] = len(sets) - len(diffs)
    return diffs, dict(reasons)


class CounterLike(defaultdict[str, int]):
    def __init__(self) -> None:
        super().__init__(int)


def _bootstrap_paired(diffs: list[float], samples: int, alpha: float, seed: int) -> list[float]:
    np = _numpy()
    rng = random.Random(seed)
    estimates = [sum(rng.choices(diffs, k=len(diffs))) / len(diffs) for _ in range(samples)]
    return [float(np.quantile(estimates, alpha / 2)), float(np.quantile(estimates, 1 - alpha / 2))]


def _bootstrap_stratified(
    values: list[tuple[float, str, str]],
    treatment_arm: str,
    samples: int,
    alpha: float,
    seed: int,
) -> list[float]:
    np = _numpy()
    rng = random.Random(seed)
    strata: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for value, arm, stratum in values:
        strata[stratum][arm].append(value)
    control_arm = next(arm for _, arm, _ in values if arm != treatment_arm)
    estimates: list[float] = []
    for _ in range(samples):
        treatment: list[float] = []
        control: list[float] = []
        for arms in strata.values():
            treatment.extend(rng.choices(arms[treatment_arm], k=len(arms[treatment_arm])))
            control.extend(rng.choices(arms[control_arm], k=len(arms[control_arm])))
        estimates.append(_effect(treatment, control))
    return [float(np.quantile(estimates, alpha / 2)), float(np.quantile(estimates, 1 - alpha / 2))]


def _block_effect(values: list[tuple[float, str, str]], treatment_arm: str) -> float:
    blocks: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for value, arm, block in values:
        blocks[block][arm].append(value)
    control_arm = next(arm for _, arm, _ in values if arm != treatment_arm)
    weighted = []
    for arms in blocks.values():
        treatment = arms[treatment_arm]
        control = arms[control_arm]
        if treatment and control:
            weight = min(len(treatment), len(control))
            weighted.append((_effect(treatment, control), weight))
    if not weighted:
        raise ValueError("block design has no complete blocks")
    return sum(effect * weight for effect, weight in weighted) / sum(weight for _, weight in weighted)


def _bootstrap_block(
    values: list[tuple[float, str, str]],
    treatment_arm: str,
    samples: int,
    alpha: float,
    seed: int,
) -> list[float]:
    np = _numpy()
    rng = random.Random(seed)
    blocks: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for value, arm, block in values:
        blocks[block].append((value, arm))
    block_names = sorted(blocks)
    estimates: list[float] = []
    for _ in range(samples):
        sampled: list[tuple[float, str, str]] = []
        for draw, block_name in enumerate(rng.choices(block_names, k=len(block_names))):
            arms: dict[str, list[float]] = defaultdict(list)
            for value, arm in blocks[block_name]:
                arms[arm].append(value)
            for arm, arm_values in arms.items():
                sampled.extend(
                    (value, arm, str(draw)) for value in rng.choices(arm_values, k=len(arm_values))
                )
        estimates.append(_block_effect(sampled, treatment_arm))
    return [float(np.quantile(estimates, alpha / 2)), float(np.quantile(estimates, 1 - alpha / 2))]


def _sign_flip_test(diffs: list[float], permutations: int, seed: int) -> dict[str, Any]:
    observed = abs(sum(diffs) / len(diffs))
    combinations = 2 ** len(diffs)
    if combinations <= permutations:
        estimates = (
            abs(sum(value * sign for value, sign in zip(diffs, signs, strict=True)) / len(diffs))
            for signs in itertools.product((-1, 1), repeat=len(diffs))
        )
        extreme = sum(estimate >= observed - 1e-15 for estimate in estimates)
        return {
            "method": "exact_sign_flip",
            "p_value": extreme / combinations,
            "permutations": combinations,
            "monte_carlo_standard_error": 0.0,
        }
    rng = random.Random(seed)
    extreme = 0
    for _ in range(permutations):
        estimate = abs(sum(value * rng.choice((-1, 1)) for value in diffs) / len(diffs))
        extreme += estimate >= observed - 1e-15
    p_value = (extreme + 1) / (permutations + 1)
    return {
        "method": "monte_carlo_sign_flip",
        "p_value": p_value,
        "permutations": permutations,
        "monte_carlo_standard_error": math.sqrt(p_value * (1 - p_value) / permutations),
    }


def _stratified_permutation(
    values: list[tuple[float, str, str]], treatment_arm: str, permutations: int, seed: int
) -> dict[str, Any]:
    by_stratum: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for value, arm, stratum in values:
        by_stratum[stratum].append((value, arm))
    control_arm = next(arm for _, arm, _ in values if arm != treatment_arm)

    def estimate(assignments: list[list[str]]) -> float:
        treatment: list[float] = []
        control: list[float] = []
        for items, labels in zip(by_stratum.values(), assignments, strict=True):
            for (value, _), label in zip(items, labels, strict=True):
                (treatment if label == treatment_arm else control).append(value)
        return _effect(treatment, control)

    original = [[arm for _, arm in items] for items in by_stratum.values()]
    observed = abs(estimate(original))
    total = math.prod(math.comb(len(labels), labels.count(treatment_arm)) for labels in original)
    if total <= permutations:
        choices: list[list[tuple[str, ...]]] = []
        for labels in original:
            treatment_n = labels.count(treatment_arm)
            variants = []
            for positions in itertools.combinations(range(len(labels)), treatment_n):
                selected = set(positions)
                variants.append(
                    tuple(treatment_arm if index in selected else control_arm for index in range(len(labels)))
                )
            choices.append(variants)
        extreme = sum(
            abs(estimate([list(labels) for labels in assignment])) >= observed - 1e-15
            for assignment in itertools.product(*choices)
        )
        return {
            "method": "exact_stratified_permutation",
            "p_value": extreme / total,
            "permutations": total,
            "monte_carlo_standard_error": 0.0,
        }
    rng = random.Random(seed)
    extreme = 0
    for _ in range(permutations):
        assignment = []
        for labels in original:
            shuffled = list(labels)
            rng.shuffle(shuffled)
            assignment.append(tuple(shuffled))
        extreme += abs(estimate([list(labels) for labels in assignment])) >= observed - 1e-15
    p_value = (extreme + 1) / (permutations + 1)
    return {
        "method": "monte_carlo_stratified_permutation",
        "p_value": p_value,
        "permutations": permutations,
        "monte_carlo_standard_error": math.sqrt(p_value * (1 - p_value) / permutations),
    }


def _block_permutation(
    values: list[tuple[float, str, str]], treatment_arm: str, permutations: int, seed: int
) -> dict[str, Any]:
    blocks: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for value, arm, block in values:
        blocks[block].append((value, arm))
    control_arm = next(arm for _, arm, _ in values if arm != treatment_arm)
    observed = abs(_block_effect(values, treatment_arm))
    original = [[arm for _, arm in items] for items in blocks.values()]
    total = math.prod(math.comb(len(labels), labels.count(treatment_arm)) for labels in original)

    def estimate(assignment: list[tuple[str, ...]]) -> float:
        permuted = [
            (value, label, block)
            for (block, items), labels in zip(blocks.items(), assignment, strict=True)
            for (value, _), label in zip(items, labels, strict=True)
        ]
        return abs(_block_effect(permuted, treatment_arm))

    if total <= permutations:
        choices: list[list[tuple[str, ...]]] = []
        for labels in original:
            treatment_n = labels.count(treatment_arm)
            variants = []
            for positions in itertools.combinations(range(len(labels)), treatment_n):
                selected = set(positions)
                variants.append(
                    tuple(treatment_arm if index in selected else control_arm for index in range(len(labels)))
                )
            choices.append(variants)
        extreme = sum(
            estimate(list(assignment)) >= observed - 1e-15 for assignment in itertools.product(*choices)
        )
        return {
            "method": "exact_block_permutation",
            "p_value": extreme / total,
            "permutations": total,
            "monte_carlo_standard_error": 0.0,
        }
    rng = random.Random(seed)
    extreme = 0
    for _ in range(permutations):
        assignment = []
        for labels in original:
            shuffled = list(labels)
            rng.shuffle(shuffled)
            assignment.append(tuple(shuffled))
        extreme += estimate(assignment) >= observed - 1e-15
    p_value = (extreme + 1) / (permutations + 1)
    return {
        "method": "monte_carlo_block_permutation",
        "p_value": p_value,
        "permutations": permutations,
        "monte_carlo_standard_error": math.sqrt(p_value * (1 - p_value) / permutations),
    }


def _analyze_contrast(
    rows: list[dict[str, Any]],
    plan: TrialPlan,
    outcome: OutcomeSpec,
    contrast: ContrastSpec,
    spec: AnalysisSpec,
) -> dict[str, Any]:
    relevant = [row for row in rows if row["arm_id"] in {contrast.treatment_arm, contrast.control_arm}]
    failures = sum(row["episode_status"] != "completed" for row in relevant)
    missing = sum(
        row["episode_status"] == "completed"
        and row.get(f"metric.{outcome.scorer_id}.{outcome.metric_id}") is None
        for row in relevant
    )
    seed = int(sha256_bytes(canonical_json({"seed": spec.seed, "contrast": contrast.id}))[:16], 16)
    warnings: list[str] = []
    if plan.design == "matched_pair":
        diffs, policy_counts = _paired_units(rows, contrast, outcome)
        if len(diffs) < 2:
            return {
                "contrast_id": contrast.id,
                "treatment_arm": contrast.treatment_arm,
                "control_arm": contrast.control_arm,
                "estimand": contrast.estimand,
                "outcome_id": outcome.id,
                "status": "descriptive_only",
                "planned_n": len(relevant),
                "successful_n": len(relevant) - failures,
                "valid_scored_n": 2 * len(diffs),
                "failed_n": failures,
                "missing_n": missing,
                "effect": sum(diffs) / len(diffs) if diffs else None,
                "confidence_interval": None,
                "standardized_effect": None,
                "raw_p_value": None,
                "confirmatory_valid": False,
                "warnings": ["fewer than two complete randomized units"],
                "policy_counts": policy_counts,
            }
        effect = sum(diffs) / len(diffs)
        interval = _bootstrap_paired(diffs, spec.bootstrap_samples, spec.alpha, seed + 1)
        test = _sign_flip_test(diffs, spec.permutations, seed + 2)
        valid_n = 2 * len(diffs)
        treatment = control = []
    else:
        values: list[tuple[float, str, str]] = []
        policy_counts = CounterLike()
        for row in relevant:
            value, reason = _policy_value(row, outcome)
            if reason:
                policy_counts[reason] += 1
            if value is not None:
                stratum = (
                    str(row["block_id"])
                    if plan.design == "block"
                    else __import__("json").dumps({"condition": row["condition"]}, sort_keys=True)
                )
                values.append((value, row["arm_id"], stratum))
        treatment = [value for value, arm, _ in values if arm == contrast.treatment_arm]
        control = [value for value, arm, _ in values if arm == contrast.control_arm]
        if plan.design == "block":
            block_arms: dict[str, set[str]] = defaultdict(set)
            for _, arm, block in values:
                block_arms[block].add(arm)
            complete_blocks = {
                block
                for block, arms in block_arms.items()
                if {contrast.treatment_arm, contrast.control_arm} <= arms
            }
            policy_counts["incomplete_blocks"] = len(block_arms) - len(complete_blocks)
            values = [item for item in values if item[2] in complete_blocks]
            treatment = [value for value, arm, _ in values if arm == contrast.treatment_arm]
            control = [value for value, arm, _ in values if arm == contrast.control_arm]
        if len(treatment) < 2 or len(control) < 2:
            return {
                "contrast_id": contrast.id,
                "treatment_arm": contrast.treatment_arm,
                "control_arm": contrast.control_arm,
                "estimand": contrast.estimand,
                "outcome_id": outcome.id,
                "status": "descriptive_only",
                "planned_n": len(relevant),
                "successful_n": len(relevant) - failures,
                "valid_scored_n": len(values),
                "failed_n": failures,
                "missing_n": missing,
                "effect": _effect(treatment, control) if treatment and control else None,
                "confidence_interval": None,
                "standardized_effect": None,
                "raw_p_value": None,
                "confirmatory_valid": False,
                "warnings": ["fewer than two observations in each arm"],
                "policy_counts": dict(policy_counts),
            }
        if plan.design == "block":
            effect = _block_effect(values, contrast.treatment_arm)
            interval = _bootstrap_block(
                values,
                contrast.treatment_arm,
                spec.bootstrap_samples,
                spec.alpha,
                seed + 1,
            )
            test = _block_permutation(values, contrast.treatment_arm, spec.permutations, seed + 2)
        else:
            effect = _effect(treatment, control)
            interval = _bootstrap_stratified(
                values,
                contrast.treatment_arm,
                spec.bootstrap_samples,
                spec.alpha,
                seed + 1,
            )
            test = _stratified_permutation(
                values, contrast.treatment_arm, spec.permutations, seed + 2
            )
        valid_n = len(values)
    standardized = None
    if outcome.type == "continuous":
        if plan.design == "matched_pair":
            np = _numpy()
            standard_deviation = float(np.std(diffs, ddof=1))
        else:
            np = _numpy()
            pooled = treatment + control
            standard_deviation = float(np.std(pooled, ddof=1))
        standardized = effect / standard_deviation if standard_deviation > 0 else None
    confirmatory_valid = plan.study_mode == "confirmatory" and plan.diagnostics.get(
        "confirmatory_valid", False
    )
    if (
        (outcome.failure_policy == "missing" and failures)
        or outcome.missing_policy
        in {
            "descriptive",
            "error",
        }
        and missing
    ):
        confirmatory_valid = False
        warnings.append("predeclared policy leaves failed or missing episodes without an analysis value")
    return {
        "contrast_id": contrast.id,
        "treatment_arm": contrast.treatment_arm,
        "control_arm": contrast.control_arm,
        "estimand": contrast.estimand,
        "outcome_id": outcome.id,
        "status": "completed",
        "planned_n": len(relevant),
        "successful_n": len(relevant) - failures,
        "valid_scored_n": valid_n,
        "failed_n": failures,
        "missing_n": missing,
        "effect": effect,
        "confidence_interval": interval,
        "standardized_effect": standardized,
        "raw_p_value": test["p_value"],
        "test": test,
        "confirmatory_valid": confirmatory_valid,
        "warnings": warnings,
        "policy_counts": dict(policy_counts),
    }


def render_report(results: dict[str, Any]) -> str:
    lines = [
        f"# Research analysis: {results['analysis_id']}",
        "",
        f"- Status: `{results['status']}`",
        f"- Confirmatory valid: `{str(results['confirmatory_valid']).lower()}`",
        f"- Alpha: `{results['alpha']}`",
        "",
        "## Results",
        "",
    ]
    for result in results["contrasts"]:
        interval = result.get("confidence_interval")
        interval_text = "unavailable" if interval is None else f"[{interval[0]:.6g}, {interval[1]:.6g}]"
        lines.extend(
            [
                f"### {result['contrast_id']}",
                "",
                f"Treatment `{result['treatment_arm']}` minus control `{result['control_arm']}`.",
                "",
                (
                    f"Effect: `{result.get('effect')}`; {1 - results['alpha']:.0%} CI: "
                    f"`{interval_text}`; planned n: `{result['planned_n']}`; valid scored n: "
                    f"`{result['valid_scored_n']}`."
                ),
                "",
                f"Standardized effect: `{result.get('standardized_effect')}`.",
                "",
                (
                    f"Failures: `{result['failed_n']}`; missing: `{result['missing_n']}`; "
                    f"raw p: `{result.get('raw_p_value')}`; adjusted p: "
                    f"`{result.get('adjusted_p_value')}`."
                ),
                "",
            ]
        )
    if results["warnings"]:
        lines.extend(["## Warnings", "", *[f"- {warning}" for warning in results["warnings"]], ""])
    return "\n".join(lines)


def _infer_run_unlocked(
    run_root: Path,
    dataset_manifest: dict[str, Any],
    *,
    index_path: Path | None = None,
) -> dict[str, Any]:
    _numpy()
    run_root = run_root.resolve(strict=True)
    run = read_json(run_root / "run.json")
    if run.get("schema_version") != RUN_SCHEMA_VERSION:
        raise ValueError(f"unsupported run schema_version: {run.get('schema_version')}")
    plan_path = run_root / "plan.json"
    if run.get("plan_sha256") != sha256_file(plan_path):
        raise ValueError("frozen run plan digest mismatch")
    plan = TrialPlan.model_validate(read_json(plan_path))
    if run.get("plan_id") != plan.id:
        raise ValueError("run and frozen plan identity mismatch")
    dataset_root = Path(dataset_manifest.get("path", ""))
    if not dataset_root.is_dir():
        dataset_root = run_root / "research" / "datasets" / dataset_manifest["id"]
    manifest = read_json(dataset_root / "dataset-manifest.json")
    if manifest.get("schema_version") != ANALYSIS_SCHEMA_VERSION:
        raise ValueError(f"unsupported dataset schema_version: {manifest.get('schema_version')}")
    manifest_digest = manifest.get("manifest_sha256")
    unsigned_manifest = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    if manifest_digest != sha256_bytes(canonical_json(unsigned_manifest)):
        raise ValueError("dataset manifest digest mismatch")
    if manifest["plan_id"] != plan.id or manifest["analysis_spec"] != plan.analysis.model_dump(mode="json"):
        raise ValueError("dataset does not match the frozen TrialPlan analysis contract")
    if plan.study_mode == "confirmatory" and manifest.get("evaluation_missing", 0):
        raise ValueError("confirmatory inference refuses unscored planned evaluations")
    rows = _load_rows(dataset_root, manifest)
    outcomes = {outcome.id: outcome for outcome in plan.analysis.outcomes}
    contrast_results = [
        _analyze_contrast(rows, plan, outcomes[contrast.outcome_id], contrast, plan.analysis)
        for contrast in plan.analysis.contrasts
    ]
    by_family: dict[str, list[int]] = defaultdict(list)
    for index, contrast in enumerate(plan.analysis.contrasts):
        if contrast_results[index].get("raw_p_value") is not None:
            by_family[contrast.family].append(index)
    for indices in by_family.values():
        raw = [contrast_results[index]["raw_p_value"] for index in indices]
        adjusted = holm_adjust(raw) if plan.analysis.multiplicity == "holm" else raw
        for index, value in zip(indices, adjusted, strict=True):
            contrast_results[index]["adjusted_p_value"] = value
    for result in contrast_results:
        result.setdefault("adjusted_p_value", None)

    warnings = list(plan.migration_warnings) + list(plan.diagnostics.get("warnings", []))
    if plan.study_mode != "confirmatory":
        warnings.append("exploratory or legacy study: results are descriptive, not confirmatory")
    for outcome in plan.analysis.outcomes:
        metric = next(
            metric
            for scorer in plan.evaluation.scorers
            if scorer.id == outcome.scorer_id
            for metric in scorer.metrics
            if metric.id == outcome.metric_id
        )
        alpha = manifest.get("agreement", {}).get(f"{outcome.scorer_id}.{outcome.metric_id}")
        if metric.agreement_threshold is not None and (alpha is None or alpha < metric.agreement_threshold):
            warnings.append(f"agreement threshold was not met for outcome {outcome.id}")
            for result in contrast_results:
                if result["outcome_id"] == outcome.id:
                    result["confirmatory_valid"] = False
    confirmatory_valid = bool(contrast_results) and all(
        result["confirmatory_valid"] for result in contrast_results
    )
    analysis_payload = {
        "dataset_sha256": manifest["dataset_sha256"],
        "analysis_spec": plan.analysis.model_dump(mode="json"),
        "algorithm_version": "design-inference-v1",
        "application_version": __version__,
    }
    analysis_id = stable_id("analysis", analysis_payload)
    results = {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "analysis_id": analysis_id,
        "run_id": manifest["run_id"],
        "dataset_id": manifest["id"],
        "status": "completed"
        if any(result["status"] == "completed" for result in contrast_results)
        else "descriptive_only",
        "confirmatory_valid": confirmatory_valid,
        "alpha": plan.analysis.alpha,
        "multiplicity": plan.analysis.multiplicity,
        "contrasts": contrast_results,
        "warnings": sorted(
            set(warnings + [warning for item in contrast_results for warning in item["warnings"]])
        ),
        "errors": [],
    }
    output_root = run_root / "research" / "analyses" / analysis_id
    existing_manifest_path = output_root / "analysis-manifest.json"
    if existing_manifest_path.exists():
        existing_manifest = read_json(existing_manifest_path)
        if existing_manifest.get("schema_version") != ANALYSIS_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported analysis schema_version: {existing_manifest.get('schema_version')}"
            )
        existing_digest = existing_manifest.get("manifest_sha256")
        unsigned_existing = {
            key: value for key, value in existing_manifest.items() if key != "manifest_sha256"
        }
        if existing_digest != sha256_bytes(canonical_json(unsigned_existing)):
            raise ValueError("existing analysis manifest was modified")
        expected_input = {"dataset_id": manifest["id"], "dataset_sha256": manifest["dataset_sha256"]}
        if existing_manifest.get("input") != expected_input:
            raise ValueError(f"analysis identity collision: {analysis_id}")
        for name, digest in existing_manifest.get("files", {}).items():
            if sha256_file(output_root / name) != digest:
                raise ValueError(f"existing analysis file was modified: {name}")
        return read_json(output_root / "results.json") | {"path": str(output_root)}
    output_root.mkdir(parents=True, exist_ok=True)
    write_json(output_root / "results.json", results)
    atomic_write(output_root / "report.md", render_report(results).encode("utf-8"))
    analysis_manifest = {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "id": analysis_id,
        "run_id": manifest["run_id"],
        "status": results["status"],
        "confirmatory_valid": confirmatory_valid,
        "analysis_spec": plan.analysis.model_dump(mode="json"),
        "input": {"dataset_id": manifest["id"], "dataset_sha256": manifest["dataset_sha256"]},
        "algorithms": {
            "inference": "design-inference-v1",
            "bootstrap_seed": plan.analysis.seed,
            "permutation_seed": plan.analysis.seed,
            "numpy": _numpy().__version__,
            "scipy": __import__("scipy").__version__,
            "python": platform.python_version(),
        },
        "files": {
            "results.json": sha256_file(output_root / "results.json"),
            "report.md": sha256_file(output_root / "report.md"),
        },
        "created_at": utc_now(),
    }
    analysis_manifest["manifest_sha256"] = sha256_bytes(canonical_json(analysis_manifest))
    write_json(output_root / "analysis-manifest.json", analysis_manifest)
    if index_path:
        store = IndexStore(index_path)
        try:
            store.upsert_analysis(analysis_manifest, output_root)
        finally:
            store.close()
    return results | {"path": str(output_root)}


def infer_run(
    run_root: Path,
    dataset_manifest: dict[str, Any],
    *,
    index_path: Path | None = None,
) -> dict[str, Any]:
    run_root = run_root.resolve(strict=True)
    lock_root = run_root / ".locks"
    lock_root.mkdir(exist_ok=True)
    with FileLock(lock_root / "analysis.lock"):
        return _infer_run_unlocked(run_root, dataset_manifest, index_path=index_path)


def rebuild_report(analysis_root: Path) -> Path:
    manifest_path = analysis_root / "analysis-manifest.json"
    manifest = read_json(manifest_path)
    if manifest.get("schema_version") != ANALYSIS_SCHEMA_VERSION:
        raise ValueError(f"unsupported analysis schema_version: {manifest.get('schema_version')}")
    digest = manifest.get("manifest_sha256")
    unsigned = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    if digest != sha256_bytes(canonical_json(unsigned)):
        raise ValueError("analysis manifest digest mismatch")
    if sha256_file(analysis_root / "results.json") != manifest["files"]["results.json"]:
        raise ValueError("analysis results digest mismatch")
    results = read_json(analysis_root / "results.json")
    output = analysis_root / "report.md"
    atomic_write(output, render_report(results).encode("utf-8"))
    manifest["files"]["report.md"] = sha256_file(output)
    manifest.pop("manifest_sha256", None)
    manifest["manifest_sha256"] = sha256_bytes(canonical_json(manifest))
    write_json(manifest_path, manifest)
    return output
