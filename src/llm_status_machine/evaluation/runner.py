from __future__ import annotations

import io
import json
import os
import shutil
import signal
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath
from statistics import mean, median
from typing import Any

from filelock import FileLock

from llm_status_machine.domain.models import MetricSpec, ScorerSpec, TrialPlan
from llm_status_machine.evaluation.agreement import krippendorff_alpha
from llm_status_machine.recording.bundle import resolve_bundle_path, validate_seal
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
from llm_status_machine.version import EVALUATION_SCHEMA_VERSION, __version__


class _ScorerFailure(ValueError):
    def __init__(self, message: str, stdout: bytes = b"", stderr: bytes = b"", elapsed: float = 0.0) -> None:
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr
        self.elapsed = elapsed


def _load_plan(run_root: Path, run: dict[str, Any]) -> TrialPlan:
    path = run_root / "plan.json"
    if not path.exists():
        raise ValueError("run does not contain a frozen v2 TrialPlan")
    if run.get("plan_sha256") != sha256_file(path):
        raise ValueError("frozen run plan digest mismatch")
    plan = TrialPlan.model_validate(read_json(path))
    if run.get("plan_id") != plan.id:
        raise ValueError("run and frozen plan identity mismatch")
    return plan


def _validate_existing_evaluation(output_root: Path, input_digest: str) -> dict[str, Any]:
    manifest_path = output_root / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError("incomplete evaluation output")
    manifest = read_json(manifest_path)
    if manifest.get("schema_version") != EVALUATION_SCHEMA_VERSION:
        raise ValueError(f"unsupported evaluation schema_version: {manifest.get('schema_version')}")
    actual = [
        {"path": name, "sha256": sha256_file(output_root / name), "size": (output_root / name).stat().st_size}
        for name in ("evaluation.json", "stdout.raw", "stderr.raw")
    ]
    if actual != manifest.get("files") or sha256_bytes(canonical_json(actual)) != manifest.get(
        "manifest_sha256"
    ):
        raise ValueError(f"existing evaluation manifest is invalid: {output_root.name}")
    record = read_json(output_root / "evaluation.json")
    if record.get("input_digest") != input_digest or manifest.get("input_digest") != input_digest:
        raise ValueError(f"evaluation identity collision: {output_root.name}")
    return record


def _metric_value(metric: MetricSpec, value: Any) -> Any:
    if metric.type in {"continuous", "binary", "ordinal"}:
        if isinstance(value, bool):
            value = int(value)
        if not isinstance(value, (int, float)):
            raise ValueError(f"metric {metric.id} must be numeric")
        value = float(value)
        if metric.type == "binary" and value not in {0.0, 1.0}:
            raise ValueError(f"binary metric {metric.id} must be 0 or 1")
    elif not isinstance(value, (str, int, float, bool)):
        raise ValueError(f"nominal metric {metric.id} must be scalar")
    if metric.lower_bound is not None and float(value) < metric.lower_bound:
        raise ValueError(f"metric {metric.id} is below its lower bound")
    if metric.upper_bound is not None and float(value) > metric.upper_bound:
        raise ValueError(f"metric {metric.id} is above its upper bound")
    return value


def _strict_result(payload: Any, scorer: ScorerSpec) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) - {"status", "metrics", "error"}:
        raise ValueError("scorer output must be an object with status, metrics, and optional error")
    status = payload.get("status")
    if status not in {"completed", "failed"}:
        raise ValueError("scorer status must be completed or failed")
    raw_metrics = payload.get("metrics", [])
    if not isinstance(raw_metrics, list):
        raise TypeError("scorer metrics must be a list")
    expected = {metric.id: metric for metric in scorer.metrics}
    metrics: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_metrics:
        if not isinstance(item, dict) or set(item) - {"metric_id", "value", "evidence_references"}:
            raise ValueError("metric output has unknown or missing fields")
        metric_id = item.get("metric_id")
        if metric_id not in expected or metric_id in seen:
            raise ValueError(f"unexpected or duplicate metric: {metric_id}")
        references = item.get("evidence_references", [])
        if not isinstance(references, list) or not all(isinstance(value, str) for value in references):
            raise ValueError("evidence_references must be a string list")
        if any(
            PurePosixPath(value).is_absolute() or ".." in PurePosixPath(value).parts for value in references
        ):
            raise ValueError("evidence_references must stay within scorer staging")
        metrics.append(
            {
                "metric_id": metric_id,
                "value": _metric_value(expected[metric_id], item.get("value")),
                "evidence_references": references,
                "type": expected[metric_id].type,
                "direction": expected[metric_id].direction,
                "lower_bound": expected[metric_id].lower_bound,
                "upper_bound": expected[metric_id].upper_bound,
            }
        )
        seen.add(metric_id)
    missing = [metric.id for metric in scorer.metrics if metric.required and metric.id not in seen]
    if status == "completed" and missing:
        raise ValueError(f"scorer omitted required metrics: {missing}")
    return {"status": status, "metrics": metrics, "error": payload.get("error")}


def _execution_integrity(bundle: Path, scorer: ScorerSpec) -> dict[str, Any]:
    outcomes = read_json(bundle / "outcomes.json")
    changed = read_json(bundle / "changed-files.json")
    transcript_count = sum(1 for line in (bundle / "transcript.jsonl").read_bytes().splitlines() if line)
    available: dict[str, Any] = {
        "process_completed": outcomes["process"]["status"] == "completed",
        "protocol_completed": outcomes["protocol"]["status"] == "completed",
        "capture_completed": outcomes["capture"]["status"] == "completed",
        "workspace_completed": outcomes["workspace"]["status"] == "completed",
        "changed_file_count": len(changed),
        "transcript_record_count": transcript_count,
    }
    return _strict_result(
        {
            "status": "completed",
            "metrics": [
                {"metric_id": metric.id, "value": available[metric.id], "evidence_references": []}
                for metric in scorer.metrics
                if metric.id in available
            ],
        },
        scorer,
    )


def _snapshot_entries(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            entries.append(
                {
                    "path": relative,
                    "type": "symlink",
                    "size": path.lstat().st_size,
                    "target": os.readlink(path),
                }
            )
        elif path.is_file():
            entries.append(
                {
                    "path": relative,
                    "type": "file",
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    return entries


def _safe_extract_git_snapshot(workspace: Path, final: dict[str, Any], destination: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(workspace), "archive", "--format=tar", final["git_commit"]],
        check=True,
        capture_output=True,
    )
    destination.mkdir()
    root = destination.resolve()
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
        for member in archive.getmembers():
            target = (destination / member.name).resolve()
            if target != root and root not in target.parents:
                raise ValueError("git archive contains an escaping path")
        archive.extractall(destination, filter="data")
    actual = _snapshot_entries(destination)
    keys = ("path", "type", "size", "sha256", "target")
    expected = sorted(
        ({key: item[key] for key in keys if key in item} for item in final["entries"]),
        key=lambda item: item["path"],
    )
    if actual != expected:
        raise ValueError("exported scorer snapshot does not match sealed workspace manifest")
    return sha256_bytes(canonical_json(actual))


def _make_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def _execute_scorer_process(
    argv: list[str], cwd: Path, env: dict[str, str], timeout: float, scratch: Path
) -> tuple[int | None, bytes, bytes, float, bool]:
    started = time.monotonic()
    stdout_path = scratch / "stdout.raw"
    stderr_path = scratch / "stderr.raw"
    timed_out = False
    with stdout_path.open("w+b") as stdout_handle, stderr_path.open("w+b") as stderr_handle:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            start_new_session=os.name == "posix",
        )
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                except ProcessLookupError:
                    pass
                process.wait()
        stdout_handle.seek(0)
        stderr_handle.seek(0)
        maximum = 10 * 1024 * 1024
        stdout = stdout_handle.read(maximum + 1)
        stderr = stderr_handle.read(maximum + 1)
    elapsed = time.monotonic() - started
    if len(stdout) > maximum or len(stderr) > maximum:
        raise _ScorerFailure(
            "scorer output exceeded 10 MiB",
            stdout[:maximum],
            stderr[:maximum],
            elapsed,
        )
    return process.returncode, stdout, stderr, elapsed, timed_out


def _run_command_scorer(
    scorer: ScorerSpec,
    bundle: Path,
    episode: dict[str, Any],
    blind_id: str,
) -> tuple[dict[str, Any], bytes, bytes, float]:
    executable = Path(scorer.argv[0])
    if sha256_file(executable) != scorer.executable_sha256:
        raise ValueError("scorer executable digest changed after plan freeze")
    for pinned in [scorer.rubric, *scorer.support_files]:
        if pinned is not None and sha256_file(Path(pinned.path)) != pinned.sha256:
            raise ValueError(f"scorer input digest changed after plan freeze: {pinned.path}")
    with tempfile.TemporaryDirectory(prefix="lsm-evaluate-") as temporary:
        temporary_root = Path(temporary)
        staging = temporary_root / "staging"
        staging.mkdir()
        workspace = staging / "workspace"
        final = read_json(bundle / "workspace.final.json")
        snapshot_sha256 = _safe_extract_git_snapshot(Path(episode["workspace"]), final, workspace)
        inputs = staging / "inputs"
        inputs.mkdir()
        staged_paths: dict[str, str] = {}
        pinned_inputs: list[dict[str, Any]] = []
        for index, pinned in enumerate(
            [item for item in [scorer.rubric, *scorer.support_files] if item is not None], start=1
        ):
            destination = inputs / f"{index}-{Path(pinned.path).name}"
            shutil.copyfile(pinned.path, destination)
            staged_paths[pinned.path] = str(destination)
            pinned_inputs.append(
                {"path": destination.relative_to(staging).as_posix(), "sha256": pinned.sha256}
            )
        manifest = {
            "schema_version": EVALUATION_SCHEMA_VERSION,
            "blind_id": blind_id,
            "bundle_sha256": read_json(bundle / "seal.json")["bundle_sha256"],
            "workspace": "workspace",
            "workspace_manifest_sha256": final["sha256"],
            "snapshot_content_sha256": snapshot_sha256,
            "prompt": (bundle / "prompt.md").read_text(encoding="utf-8") if scorer.include_prompt else None,
            "rubric": staged_paths.get(scorer.rubric.path) if scorer.rubric else None,
            "support_files": pinned_inputs,
        }
        manifest_path = staging / "manifest.json"
        write_json(manifest_path, manifest)
        _make_read_only(staging)
        scratch = temporary_root / "scratch"
        scratch.mkdir()
        replacements = {
            "{manifest}": str(manifest_path),
            "{workspace}": str(workspace),
            "{rubric}": staged_paths.get(scorer.rubric.path, "") if scorer.rubric else "",
        }
        argv = [replacements.get(argument, staged_paths.get(argument, argument)) for argument in scorer.argv]
        return_code, stdout, stderr, elapsed, timed_out = _execute_scorer_process(
            argv,
            staging,
            {
                "PATH": os.defpath,
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "TMPDIR": str(scratch),
                "LSM_SCORER_MANIFEST": str(manifest_path),
            },
            scorer.timeout_seconds,
            scratch,
        )
        try:
            observed_snapshot_sha256 = sha256_bytes(canonical_json(_snapshot_entries(workspace)))
        except OSError as error:
            raise _ScorerFailure(
                f"scorer modified or removed its workspace snapshot: {error}",
                stdout,
                stderr,
                elapsed,
            ) from error
        if observed_snapshot_sha256 != snapshot_sha256:
            raise _ScorerFailure("scorer modified its read-only workspace snapshot", stdout, stderr, elapsed)
        if timed_out:
            raise _ScorerFailure("scorer timed out", stdout, stderr, elapsed)
        if return_code != 0:
            raise _ScorerFailure(
                f"scorer exited with status {return_code}",
                stdout,
                stderr,
                elapsed,
            )
        try:
            payload = json.loads(stdout.decode("utf-8", "strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise _ScorerFailure(f"scorer returned invalid JSON: {error}", stdout, stderr, elapsed) from error
        try:
            validated = _strict_result(payload, scorer)
        except (TypeError, ValueError) as error:
            raise _ScorerFailure(str(error), stdout, stderr, elapsed) from error
        return validated, stdout, stderr, elapsed


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


def _evaluate_run_unlocked(run_root: Path, *, index_path: Path | None = None) -> dict[str, Any]:
    run_root = run_root.resolve(strict=True)
    run = read_json(run_root / "run.json")
    plan = _load_plan(run_root, run)
    trials = {trial.id: trial for trial in plan.trials}
    episodes = {
        path.parent.name: read_json(path) for path in sorted((run_root / "episodes").glob("*/episode.json"))
    }
    unknown = set(episodes) - set(trials)
    if unknown:
        raise ValueError(f"run contains episodes outside TrialPlan: {sorted(unknown)}")
    missing_episode_ids = sorted(set(trials) - set(episodes))
    blind_ids = {
        episode_id: stable_id("blind", {"run_id": run["id"], "episode_id": episode_id})
        for episode_id in episodes
    }
    records: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    store = IndexStore(index_path) if index_path else None
    try:
        for episode_id, episode in episodes.items():
            episode_root = run_root / "episodes" / episode_id
            bundle = resolve_bundle_path(episode_root, episode)
            valid, errors = validate_seal(bundle)
            if not valid:
                raise ValueError(f"invalid RawBundle for {episode_id}: {errors}")
            bundle_sha = read_json(bundle / "seal.json")["bundle_sha256"]
            if episode.get("bundle_sha256") != bundle_sha:
                raise ValueError(f"episode and RawBundle digest mismatch: {episode_id}")
            for scorer in plan.evaluation.scorers:
                for repetition in range(1, scorer.repetitions + 1):
                    input_payload = {
                        "bundle_sha256": bundle_sha,
                        "scorer": scorer.model_dump(mode="json"),
                        "repetition": repetition,
                        "blind_id": blind_ids[episode_id],
                    }
                    input_digest = sha256_bytes(canonical_json(input_payload))
                    evaluation_id = stable_id("evaluation", input_payload)
                    output_root = episode_root / "evaluations" / evaluation_id
                    if output_root.exists() and (output_root / "manifest.json").exists():
                        existing = _validate_existing_evaluation(output_root, input_digest)
                        records.append(existing)
                        if store:
                            store.upsert_evaluation(existing, output_root)
                        if existing["status"] != "completed":
                            failures.append(
                                {
                                    "evaluation_id": evaluation_id,
                                    "error": existing.get("error") or "failed",
                                }
                            )
                        continue
                    if output_root.exists():
                        shutil.rmtree(output_root)
                    output_root.mkdir(parents=True)
                    stdout = b""
                    stderr = b""
                    elapsed = 0.0
                    try:
                        if scorer.kind == "execution_integrity":
                            result = _execution_integrity(bundle, scorer)
                        else:
                            result, stdout, stderr, elapsed = _run_command_scorer(
                                scorer, bundle, episode, blind_ids[episode_id]
                            )
                    except _ScorerFailure as error:
                        stdout, stderr, elapsed = error.stdout, error.stderr, error.elapsed
                        result = {
                            "status": "failed",
                            "metrics": [],
                            "error": f"{type(error).__name__}: {error}",
                        }
                    except (OSError, TypeError, ValueError, subprocess.SubprocessError) as error:
                        result = {
                            "status": "failed",
                            "metrics": [],
                            "error": f"{type(error).__name__}: {error}",
                        }
                    record = {
                        "schema_version": EVALUATION_SCHEMA_VERSION,
                        "id": evaluation_id,
                        "run_id": run["id"],
                        "episode_id": episode_id,
                        "blind_id": blind_ids[episode_id],
                        "scorer_id": scorer.id,
                        "scorer_version": scorer.version,
                        "scorer_repetition": repetition,
                        "status": result["status"],
                        "metrics": result["metrics"],
                        "error": result.get("error"),
                        "elapsed_seconds": elapsed,
                        "bundle_sha256": bundle_sha,
                        "input_digest": input_digest,
                        "created_at": utc_now(),
                    }
                    write_json(output_root / "evaluation.json", record)
                    atomic_write(output_root / "stdout.raw", stdout)
                    atomic_write(output_root / "stderr.raw", stderr)
                    files = [
                        {"path": path.name, "sha256": sha256_file(path), "size": path.stat().st_size}
                        for path in (
                            output_root / "evaluation.json",
                            output_root / "stdout.raw",
                            output_root / "stderr.raw",
                        )
                    ]
                    manifest = {
                        "schema_version": EVALUATION_SCHEMA_VERSION,
                        "evaluation_id": evaluation_id,
                        "input_digest": input_digest,
                        "files": files,
                        "manifest_sha256": sha256_bytes(canonical_json(files)),
                        "application_version": __version__,
                    }
                    write_json(output_root / "manifest.json", manifest)
                    valid_after, after_errors = validate_seal(bundle)
                    if not valid_after:
                        raise RuntimeError(f"scoring modified RawBundle: {after_errors}")
                    records.append(record)
                    if store:
                        store.upsert_evaluation(record, output_root)
                    if record["status"] != "completed":
                        failures.append(
                            {"evaluation_id": evaluation_id, "error": record["error"] or "failed"}
                        )
    finally:
        if store:
            store.close()

    aggregates: dict[str, dict[str, Any]] = {}
    agreement: dict[str, Any] = {}
    for scorer in plan.evaluation.scorers:
        for metric in scorer.metrics:
            episode_values: dict[str, list[Any]] = {}
            for record in records:
                if record["scorer_id"] != scorer.id or record["status"] != "completed":
                    continue
                value = next(
                    (item["value"] for item in record["metrics"] if item["metric_id"] == metric.id), None
                )
                episode_values.setdefault(record["episode_id"], []).append(value)
            key = f"{scorer.id}.{metric.id}"
            aggregates[key] = {
                episode_id: _aggregate([value for value in values if value is not None], scorer.aggregation)
                for episode_id, values in episode_values.items()
            }
            agreement[key] = krippendorff_alpha(list(episode_values.values()), metric.type.value)
    evaluations_per_episode = sum(scorer.repetitions for scorer in plan.evaluation.scorers)
    expected_evaluation_count = len(plan.trials) * evaluations_per_episode
    missing_evaluation_count = expected_evaluation_count - len(records)
    if not missing_episode_ids and missing_evaluation_count == 0:
        blinding_root = run_root / "blinding"
        blinding_root.mkdir(exist_ok=True)
        write_json(
            blinding_root / "map.json",
            {
                "schema_version": EVALUATION_SCHEMA_VERSION,
                "run_id": run["id"],
                "completed_at": utc_now(),
                "mapping": [
                    {
                        "blind_id": blind_ids[episode_id],
                        "episode_id": episode_id,
                        "arm_id": trials[episode_id].arm_id,
                    }
                    for episode_id in sorted(episodes)
                ],
            },
        )
    missing_errors = [
        {
            "episode_id": episode_id,
            "error": "planned episode is missing and could not be evaluated",
        }
        for episode_id in missing_episode_ids
    ]
    summary = {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "status": "completed"
        if not failures and not missing_episode_ids and missing_evaluation_count == 0
        else "failed",
        "confirmatory_valid": plan.study_mode == "confirmatory"
        and plan.diagnostics.get("confirmatory_valid", False)
        and not failures
        and not missing_episode_ids
        and missing_evaluation_count == 0,
        "run_id": run["id"],
        "evaluation_count": len(records),
        "expected_evaluation_count": expected_evaluation_count,
        "missing_evaluation_count": missing_evaluation_count,
        "missing_episode_ids": missing_episode_ids,
        "aggregates": aggregates,
        "agreement": agreement,
        "failures": failures,
        "errors": [*failures, *missing_errors],
        "warnings": [
            "prompt text was exposed to a scorer and may reveal treatment"
            for scorer in plan.evaluation.scorers
            if scorer.include_prompt
        ]
        + (["one or more planned episodes could not be evaluated"] if missing_episode_ids else []),
    }
    write_json(run_root / "evaluation-summary.json", summary)
    return summary


def evaluate_run(run_root: Path, *, index_path: Path | None = None) -> dict[str, Any]:
    run_root = run_root.resolve(strict=True)
    lock_root = run_root / ".locks"
    lock_root.mkdir(exist_ok=True)
    with FileLock(lock_root / "evaluate.lock"):
        return _evaluate_run_unlocked(run_root, index_path=index_path)
