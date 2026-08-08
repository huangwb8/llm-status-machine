from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any

from filelock import FileLock

from llm_status_machine.domain.models import TrialPlan
from llm_status_machine.evaluation.agreement import krippendorff_alpha
from llm_status_machine.recording.bundle import resolve_bundle_path, validate_seal
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
from llm_status_machine.version import (
    ANALYSIS_SCHEMA_VERSION,
    EVALUATION_SCHEMA_VERSION,
    RUN_SCHEMA_VERSION,
    __version__,
)


def _validate_evaluation(path: Path) -> tuple[dict[str, Any], str]:
    manifest = read_json(path / "manifest.json")
    if manifest.get("schema_version") != EVALUATION_SCHEMA_VERSION:
        raise ValueError(f"unsupported evaluation schema_version: {manifest.get('schema_version')}")
    actual = [
        {"path": name, "sha256": sha256_file(path / name), "size": (path / name).stat().st_size}
        for name in ("evaluation.json", "stdout.raw", "stderr.raw")
    ]
    if actual != manifest.get("files") or sha256_bytes(canonical_json(actual)) != manifest.get(
        "manifest_sha256"
    ):
        raise ValueError(f"invalid evaluation manifest: {path}")
    record = read_json(path / "evaluation.json")
    if record.get("schema_version") != EVALUATION_SCHEMA_VERSION:
        raise ValueError(f"unsupported evaluation record schema_version: {record.get('schema_version')}")
    if manifest.get("input_digest") != record.get("input_digest"):
        raise ValueError(f"evaluation input digest mismatch: {path}")
    return record, manifest["manifest_sha256"]


def _aggregate(values: list[Any], method: str) -> Any:
    if not values:
        return None
    if method == "mean":
        return mean(float(value) for value in values)
    if method == "median":
        return median(float(value) for value in values)
    if method == "min":
        return min(values)
    if method == "max":
        return max(values)
    counts = {value: values.count(value) for value in set(values)}
    return min(counts, key=lambda value: (-counts[value], str(value)))


def _usage(transcript: Path) -> dict[str, float | int | None]:
    totals: dict[str, float | int | None] = {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "cost": None,
    }
    for line in transcript.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            payload = json.loads(line).get("payload", {})
        except json.JSONDecodeError:
            continue
        candidates = [payload, payload.get("usage", {})] if isinstance(payload, dict) else []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            for key in totals:
                value = candidate.get(key)
                if isinstance(value, (int, float)):
                    totals[key] = value
    return totals


def _column_schema(rows: list[dict[str, Any]]) -> dict[str, str]:
    schema: dict[str, str] = {}
    for key in sorted({key for row in rows for key in row}):
        kinds = {type(row.get(key)).__name__ for row in rows if row.get(key) is not None}
        schema[key] = next(iter(kinds)) if len(kinds) == 1 else "json"
    return schema


def _build_dataset_unlocked(run_root: Path) -> dict[str, Any]:
    run_root = run_root.resolve(strict=True)
    run = read_json(run_root / "run.json")
    if run.get("schema_version") != RUN_SCHEMA_VERSION:
        raise ValueError(f"unsupported run schema_version: {run.get('schema_version')}")
    plan_path = run_root / "plan.json"
    if not plan_path.exists():
        raise ValueError("research dataset requires a run with a frozen v2 TrialPlan")
    if run.get("plan_sha256") != sha256_file(plan_path):
        raise ValueError("frozen run plan digest mismatch")
    plan = TrialPlan.model_validate(read_json(plan_path))
    if run.get("plan_id") != plan.id:
        raise ValueError("run and frozen plan identity mismatch")
    episode_paths = {
        path.parent.name: path.parent for path in sorted((run_root / "episodes").glob("*/episode.json"))
    }
    extras = set(episode_paths) - {trial.id for trial in plan.trials}
    if extras:
        raise ValueError(f"run contains unplanned episodes: {sorted(extras)}")

    evaluations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    evaluation_inputs: list[dict[str, Any]] = []
    scorers = {scorer.id: scorer for scorer in plan.evaluation.scorers}
    expected_evaluations = {
        (trial.id, scorer.id, repetition)
        for trial in plan.trials
        for scorer in plan.evaluation.scorers
        for repetition in range(1, scorer.repetitions + 1)
    }
    seen_evaluations: set[tuple[str, str, int]] = set()
    for episode_id, episode_root in episode_paths.items():
        episode = read_json(episode_root / "episode.json")
        for path in sorted((episode_root / "evaluations").glob("*/manifest.json")):
            record, digest = _validate_evaluation(path.parent)
            if record["episode_id"] != episode_id:
                raise ValueError("evaluation episode identity mismatch")
            scorer = scorers.get(record.get("scorer_id"))
            repetition = record.get("scorer_repetition")
            if scorer is None or not isinstance(repetition, int):
                raise ValueError("evaluation does not match a planned scorer repetition")
            key = (episode_id, scorer.id, repetition)
            if key not in expected_evaluations:
                raise ValueError(f"unplanned evaluation record: {record.get('id')}")
            if key in seen_evaluations:
                raise ValueError(f"duplicate planned evaluation: {key}")
            blind_id = stable_id("blind", {"run_id": run["id"], "episode_id": episode_id})
            input_payload = {
                "bundle_sha256": episode.get("bundle_sha256"),
                "scorer": scorer.model_dump(mode="json"),
                "repetition": repetition,
                "blind_id": blind_id,
            }
            expected_input_digest = sha256_bytes(canonical_json(input_payload))
            expected_evaluation_id = stable_id("evaluation", input_payload)
            if (
                record.get("id") != expected_evaluation_id
                or record.get("run_id") != run["id"]
                or record.get("blind_id") != blind_id
                or record.get("bundle_sha256") != episode.get("bundle_sha256")
                or record.get("scorer_version") != scorer.version
                or record.get("input_digest") != expected_input_digest
            ):
                raise ValueError(f"evaluation provenance mismatch: {record.get('id')}")
            expected_metrics = {metric.id: metric for metric in scorer.metrics}
            observed_metric_ids: set[str] = set()
            for metric_row in record.get("metrics", []):
                metric = expected_metrics.get(metric_row.get("metric_id"))
                if metric is None or metric.id in observed_metric_ids:
                    raise ValueError(f"evaluation metric contract mismatch: {record.get('id')}")
                if (
                    metric_row.get("type"),
                    metric_row.get("direction"),
                    metric_row.get("lower_bound"),
                    metric_row.get("upper_bound"),
                ) != (
                    metric.type.value,
                    metric.direction,
                    metric.lower_bound,
                    metric.upper_bound,
                ):
                    raise ValueError(f"evaluation metric contract mismatch: {record.get('id')}")
                observed_metric_ids.add(metric.id)
            required_metrics = {metric.id for metric in scorer.metrics if metric.required}
            if record.get("status") == "completed" and not required_metrics <= observed_metric_ids:
                raise ValueError(f"evaluation omitted required metrics: {record.get('id')}")
            seen_evaluations.add(key)
            evaluations[episode_id].append(record)
            evaluation_inputs.append({"id": record["id"], "sha256": digest})

    aggregates: dict[str, dict[str, Any]] = {}
    agreement: dict[str, float | None] = {}
    for scorer in plan.evaluation.scorers:
        for metric in scorer.metrics:
            episode_values: dict[str, list[Any]] = defaultdict(list)
            for episode_id, records in evaluations.items():
                for record in records:
                    if record["scorer_id"] != scorer.id or record["status"] != "completed":
                        continue
                    value = next(
                        (item["value"] for item in record["metrics"] if item["metric_id"] == metric.id),
                        None,
                    )
                    if value is not None:
                        episode_values[episode_id].append(value)
            key = f"{scorer.id}.{metric.id}"
            aggregates[key] = {
                episode_id: _aggregate(values, scorer.aggregation)
                for episode_id, values in episode_values.items()
            }
            agreement[key] = krippendorff_alpha(list(episode_values.values()), metric.type.value)
    rows: list[dict[str, Any]] = []
    bundle_inputs: list[dict[str, Any]] = []
    for trial in sorted(plan.trials, key=lambda item: item.ordinal):
        episode_root = episode_paths.get(trial.id)
        row: dict[str, Any] = {
            "run_id": run["id"],
            "episode_id": trial.id,
            "plan_id": plan.id,
            "ordinal": trial.ordinal,
            "arm_id": trial.arm_id,
            "prompt_revision_id": trial.prompt.id,
            "repetition": trial.repetition,
            "condition": trial.condition,
            "block": trial.block,
            "block_id": trial.block_id,
            "pair_id": trial.pair_id,
            "comparison_set_id": trial.comparison_set_id,
            "sequence_position": trial.sequence_position,
            "planned_dispatch_batch": trial.dispatch_batch,
            "actual_dispatch_batch": None,
            "actual_started_at": None,
            "actual_finished_at": None,
            "episode_status": "not_started",
            "process_status": "not_started",
            "protocol_status": "not_started",
            "capture_status": "not_started",
            "workspace_status": "not_started",
            "wall_seconds": None,
            "changed_file_count": None,
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "cost": None,
            "bundle_sha256": None,
            "missing_reason": "episode_not_started",
            "design_deviations": [],
        }
        if episode_root is not None:
            episode = read_json(episode_root / "episode.json")
            expected_assignment = {
                "id": trial.id,
                "run_id": run["id"],
                "ordinal": trial.ordinal,
                "arm_id": trial.arm_id,
                "comparison_set_id": trial.comparison_set_id,
                "pair_id": trial.pair_id,
                "block_id": trial.block_id,
                "sequence_position": trial.sequence_position,
                "planned_dispatch_batch": trial.dispatch_batch,
            }
            if any(episode.get(key) != value for key, value in expected_assignment.items()):
                raise ValueError(f"episode assignment mismatch: {trial.id}")
            bundle = resolve_bundle_path(episode_root, episode)
            valid, errors = validate_seal(bundle)
            if not valid:
                raise ValueError(f"invalid RawBundle for {trial.id}: {errors}")
            sealed_sha256 = read_json(bundle / "seal.json")["bundle_sha256"]
            if episode.get("bundle_sha256") != sealed_sha256:
                raise ValueError(f"episode and RawBundle digest mismatch: {trial.id}")
            outcomes = read_json(bundle / "outcomes.json")
            metadata = read_json(bundle / "metadata.json")
            result = metadata.get("result") or {}
            row.update(
                actual_dispatch_batch=episode.get("actual_dispatch_batch"),
                actual_started_at=episode.get("actual_started_at"),
                actual_finished_at=episode.get("actual_finished_at"),
                episode_status=episode["status"],
                process_status=outcomes["process"]["status"],
                protocol_status=outcomes["protocol"]["status"],
                capture_status=outcomes["capture"]["status"],
                workspace_status=outcomes["workspace"]["status"],
                wall_seconds=(result.get("finished_monotonic", 0) - result.get("started_monotonic", 0))
                if result
                else None,
                changed_file_count=len(read_json(bundle / "changed-files.json")),
                bundle_sha256=sealed_sha256,
                missing_reason=None if episode["status"] == "completed" else f"episode_{episode['status']}",
                design_deviations=episode.get("design_deviations", []),
                **_usage(bundle / "transcript.jsonl"),
            )
            bundle_inputs.append({"episode_id": trial.id, "sha256": sealed_sha256})
        for scorer in plan.evaluation.scorers:
            scorer_records = [item for item in evaluations[trial.id] if item["scorer_id"] == scorer.id]
            for metric in scorer.metrics:
                key = f"{scorer.id}.{metric.id}"
                raw_values = [
                    metric_row["value"]
                    for record in scorer_records
                    if record["status"] == "completed"
                    for metric_row in record["metrics"]
                    if metric_row["metric_id"] == metric.id
                ]
                row[f"metric_raw.{key}"] = raw_values
                row[f"metric.{key}"] = aggregates.get(key, {}).get(trial.id)
                if row[f"metric.{key}"] is None:
                    failed = [record for record in scorer_records if record["status"] != "completed"]
                    row[f"metric_missing_reason.{key}"] = (
                        failed[0].get("error") if failed else "evaluation_not_run"
                    )
                else:
                    row[f"metric_missing_reason.{key}"] = None
        rows.append(row)

    if len(rows) != len(plan.trials) or len({row["episode_id"] for row in rows}) != len(rows):
        raise RuntimeError("dataset must contain exactly one row per planned episode")
    input_payload = {
        "plan_id": plan.id,
        "spec_sha256": plan.spec_sha256,
        "bundles": sorted(bundle_inputs, key=lambda item: item["episode_id"]),
        "evaluations": sorted(evaluation_inputs, key=lambda item: item["id"]),
        "generator": __version__,
    }
    dataset_id = stable_id("dataset", input_payload)
    output_root = run_root / "research" / "datasets" / dataset_id
    existing_manifest = output_root / "dataset-manifest.json"
    if existing_manifest.exists():
        existing = read_json(existing_manifest)
        if existing.get("schema_version") != ANALYSIS_SCHEMA_VERSION:
            raise ValueError(f"unsupported dataset schema_version: {existing.get('schema_version')}")
        existing_digest = existing.get("manifest_sha256")
        unsigned = {key: value for key, value in existing.items() if key != "manifest_sha256"}
        if existing_digest != sha256_bytes(canonical_json(unsigned)):
            raise ValueError("existing dataset manifest was modified")
        if existing.get("inputs") != input_payload:
            raise ValueError(f"dataset identity collision: {dataset_id}")
        for name, digest in existing.get("files", {}).items():
            if sha256_file(output_root / name) != digest:
                raise ValueError(f"existing dataset file was modified: {name}")
        return existing | {"path": str(output_root)}
    output_root.mkdir(parents=True, exist_ok=True)
    jsonl = b"".join(canonical_json(row) for row in rows)
    atomic_write(output_root / "observations.jsonl", jsonl)
    fields = sorted({key for row in rows for key in row})
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                key: json.dumps(value, ensure_ascii=False, sort_keys=True)
                if isinstance(value, (dict, list))
                else value
                for key, value in row.items()
            }
        )
    atomic_write(output_root / "observations.csv", buffer.getvalue().encode("utf-8"))
    missing_evaluation_count = len(expected_evaluations - seen_evaluations)
    warnings = []
    if missing_evaluation_count:
        warnings.append("one or more planned evaluations are missing")
    confirmatory_valid = (
        plan.study_mode == "confirmatory"
        and plan.diagnostics.get("confirmatory_valid", False)
        and missing_evaluation_count == 0
    )
    manifest = {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "id": dataset_id,
        "run_id": run["id"],
        "status": "completed",
        "confirmatory_valid": confirmatory_valid,
        "warnings": warnings,
        "errors": [],
        "plan_id": plan.id,
        "study_mode": plan.study_mode,
        "design": plan.design,
        "analysis_spec": plan.analysis.model_dump(mode="json"),
        "row_count": len(rows),
        "columns": _column_schema(rows),
        "inputs": input_payload,
        "agreement": agreement,
        "evaluation_failures": sum(
            record["status"] != "completed" for records in evaluations.values() for record in records
        ),
        "evaluation_missing": missing_evaluation_count,
        "files": {
            "observations.jsonl": sha256_file(output_root / "observations.jsonl"),
            "observations.csv": sha256_file(output_root / "observations.csv"),
        },
        "created_at": utc_now(),
    }
    manifest["dataset_sha256"] = sha256_bytes(canonical_json(manifest["files"]))
    manifest["manifest_sha256"] = sha256_bytes(canonical_json(manifest))
    write_json(output_root / "dataset-manifest.json", manifest)
    return manifest | {"path": str(output_root)}


def build_dataset(run_root: Path) -> dict[str, Any]:
    run_root = run_root.resolve(strict=True)
    lock_root = run_root / ".locks"
    lock_root.mkdir(exist_ok=True)
    with FileLock(lock_root / "dataset.lock"):
        return _build_dataset_unlocked(run_root)
