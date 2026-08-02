from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from llm_status_machine.recording.bundle import resolve_bundle_path, validate_seal
from llm_status_machine.utils import canonical_json, read_json, sha256_bytes, sha256_file, write_json

ROOT = Path(__file__).resolve().parents[1]
ORACLE = ROOT / "oracle_tests" / "score_cache.py"
COUNT_PATTERN = re.compile(r"^EVALUATOR_COUNT = (3|6|9)$", re.MULTILINE)
CATEGORY_NAMES = (
    "api_quality",
    "basic_ttl",
    "failure_cancel",
    "invalidation_race",
    "lru_capacity",
    "single_flight",
)


def latest_run(data_root: Path) -> Path:
    runs = sorted(
        (path for path in (data_root / "runs").iterdir() if (path / "run.json").is_file()),
        key=lambda path: read_json(path / "run.json")["started_at"],
    )
    if not runs:
        raise FileNotFoundError("no runs found")
    return runs[-1]


def oracle_score(workspace: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [sys.executable, str(ORACLE), str(workspace)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return oracle_failure(f"{type(error).__name__}: {error}")
    if result.returncode != 0:
        return oracle_failure(result.stderr[-1000:] or f"oracle exited {result.returncode}")
    try:
        score = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        return oracle_failure(f"invalid oracle JSON: {error}")
    if not isinstance(score, dict) or not isinstance(score.get("score"), (int, float)):
        return oracle_failure("oracle JSON is missing a numeric score")
    score["scorer_status"] = "ok"
    score["scorer_error"] = ""
    return score


def oracle_failure(message: str) -> dict[str, Any]:
    return {
        "scorer_version": "async-ttl-cache-v2",
        "scorer_status": "failed",
        "score": None,
        "category_scores": {},
        "tests": [],
        "scorer_error": message[-1000:],
    }


def category_fields(score: dict[str, Any]) -> dict[str, float | str]:
    category_scores = score.get("category_scores", {})
    return {
        f"score_{category}": round(category_scores[category], 2)
        if category in category_scores
        else ""
        for category in CATEGORY_NAMES
    }


def _content_manifest(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        if path.is_symlink():
            entries.append(
                {
                    "path": relative,
                    "mode": info.st_mode & 0o777,
                    "size": info.st_size,
                    "type": "symlink",
                    "target": os.readlink(path),
                }
            )
        elif path.is_file():
            entries.append(
                {
                    "path": relative,
                    "mode": info.st_mode & 0o777,
                    "size": info.st_size,
                    "type": "file",
                    "sha256": sha256_file(path),
                }
            )
    return entries


def _sealed_content_manifest(final: dict[str, Any]) -> list[dict[str, Any]]:
    keys = ("path", "mode", "size", "type", "sha256", "target")
    return [{key: entry[key] for key in keys if key in entry} for entry in final["entries"]]


def export_final_snapshot(bundle: Path, repository: Path, destination: Path) -> dict[str, Any]:
    final = read_json(bundle / "workspace.final.json")
    commit = final.get("git_commit")
    if not isinstance(commit, str) or not commit:
        raise ValueError("sealed final workspace is missing git_commit")
    archive = subprocess.run(
        ["git", "archive", "--format=tar", commit],
        cwd=repository,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if archive.returncode != 0:
        raise ValueError(f"cannot export sealed final commit {commit}: {archive.stderr[-500:]!r}")
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as handle:
        handle.extractall(destination, filter="data")
    actual = _content_manifest(destination)
    expected = _sealed_content_manifest(final)
    actual_by_path = {entry["path"]: entry for entry in actual}
    expected_by_path = {entry["path"]: entry for entry in expected}
    if set(actual_by_path) - set(expected_by_path):
        raise ValueError("exported final commit contains paths absent from workspace.final.json")
    mismatches = [
        path
        for path, entry in actual_by_path.items()
        if entry != expected_by_path.get(path)
    ]
    if mismatches:
        raise ValueError(f"exported final commit differs from workspace.final.json: {mismatches[:5]}")
    required = {"async_ttl_cache.py", "pyproject.toml", "tests/test_public.py"}
    if not required <= set(actual_by_path):
        raise ValueError(f"final commit is missing score inputs: {sorted(required - set(actual_by_path))}")
    content_sha256 = sha256_bytes(canonical_json(actual))
    return {
        "git_commit": commit,
        "workspace_manifest_sha256": final["sha256"],
        "scored_content_sha256": content_sha256,
        "sealed_entry_count": len(expected),
        "scored_entry_count": len(actual),
        "omitted_sealed_entry_count": len(set(expected_by_path) - set(actual_by_path)),
    }


def usage_from_transcript(path: Path) -> dict[str, int | None]:
    usage: dict[str, int | None] = {"input_tokens": None, "output_tokens": None, "total_tokens": None}

    def visit(value: Any) -> None:
        nonlocal usage
        if isinstance(value, dict):
            if "input_tokens" in value and "output_tokens" in value:
                input_tokens = int(value["input_tokens"])
                output_tokens = int(value["output_tokens"])
                usage = {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": int(value.get("total_tokens", input_tokens + output_tokens)),
                }
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            visit(json.loads(line))
        except json.JSONDecodeError:
            continue
    return usage


def evidence_counts(bundle: Path, workspace: Path, requested: int) -> dict[str, Any]:
    artifact_root = bundle / "artifacts" / "experiment-evidence"
    manifests = sorted(artifact_root.rglob("manifest.json")) if artifact_root.is_dir() else []
    manifest: dict[str, Any] = {}
    if manifests:
        try:
            manifest = read_json(manifests[0])
        except (OSError, json.JSONDecodeError):
            manifest = {}
    result_files = (
        sorted(artifact_root.glob("**/evaluator-*/workspace/RESULT.md"))
        if artifact_root.is_dir()
        else []
    )
    if not result_files:
        result_files = sorted(workspace.glob(".bensz-api/**/evaluator-*/workspace/RESULT.md"))
    completed = manifest.get("completed_evaluator_count", manifest.get("completed_evaluators"))
    if not isinstance(completed, int):
        completed = len(result_files)
    summaries = manifest.get("summary_count", manifest.get("summaries"))
    if not isinstance(summaries, int):
        summaries = len(list(workspace.glob(".bensz-api/**/summary/workspace/RESULT.md")))
    executors = manifest.get("executor_count", manifest.get("executors", 1))
    return {
        "requested_evaluators": requested,
        "completed_evaluators": int(completed),
        "summary_count": int(summaries) if isinstance(summaries, int) else 0,
        "executor_count": int(executors) if isinstance(executors, int) else 1,
        "protocol_ok": completed == requested and summaries == 1 and executors == 1,
        "protocol_deviations": manifest.get("protocol_deviations", "missing manifest" if not manifest else ""),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    run_root = args.data_root / "runs" / args.run_id if args.run_id else latest_run(args.data_root)
    run = read_json(run_root / "run.json")

    # Blind phase: execute the same oracle against each final workspace before reading treatment.
    blind_scores: dict[str, dict[str, Any]] = {}
    snapshot_metadata: dict[str, dict[str, Any]] = {}
    snapshot_roots: dict[str, Path] = {}
    bundle_hashes: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="lsm-scoring-") as temporary:
        temporary_root = Path(temporary)
        for episode_path in sorted((run_root / "episodes").iterdir()):
            episode = read_json(episode_path / "episode.json")
            bundle = resolve_bundle_path(episode_path, episode)
            valid, errors = validate_seal(bundle)
            if not valid:
                raise ValueError(f"invalid seal for {episode['id']}: {errors}")
            bundle_hashes[episode["id"]] = episode["bundle_sha256"]
            snapshot = temporary_root / episode["id"]
            snapshot_metadata[episode["id"]] = export_final_snapshot(
                bundle, Path(episode["workspace"]), snapshot
            )
            snapshot_roots[episode["id"]] = snapshot
            blind_scores[episode["id"]] = oracle_score(snapshot)

        rows = []
        details = []
        for episode_path in sorted((run_root / "episodes").iterdir()):
            episode = read_json(episode_path / "episode.json")
            bundle = resolve_bundle_path(episode_path, episode)
            trial = read_json(bundle / "trial.json")
            matches = COUNT_PATTERN.findall(trial["actual_prompt"])
            if len(matches) != 1:
                raise ValueError(f"cannot identify treatment for {episode['id']}")
            requested = int(matches[0])
            score = blind_scores[episode["id"]]
            metadata = read_json(bundle / "metadata.json")
            process_result = metadata.get("result") or {}
            wall_seconds = None
            if process_result.get("finished_monotonic") is not None:
                wall_seconds = process_result["finished_monotonic"] - process_result["started_monotonic"]
            evidence = evidence_counts(bundle, snapshot_roots[episode["id"]], requested)
            usage = usage_from_transcript(bundle / "transcript.jsonl")
            oracle_code_score = score["score"]
            delivery_score = (
                oracle_code_score
                if score["scorer_status"] == "ok" and episode["status"] == "completed"
                else 0.0 if score["scorer_status"] == "ok" else ""
            )
            row = {
                "run_id": run["id"],
                "episode_id": episode["id"],
                "ordinal": episode["ordinal"],
                "order_position": episode["ordinal"],
                "evaluator_count": requested,
                "episode_status": episode["status"],
                "quality_score": delivery_score,
                "oracle_code_score": oracle_code_score if oracle_code_score is not None else "",
                "scorer_status": score["scorer_status"],
                "scorer_error": score["scorer_error"],
                "delivery_completed": episode["status"] == "completed",
                "wall_seconds": round(wall_seconds, 3) if wall_seconds is not None else "",
                "input_tokens": usage["input_tokens"] if usage["input_tokens"] is not None else "",
                "output_tokens": usage["output_tokens"] if usage["output_tokens"] is not None else "",
                "total_tokens": usage["total_tokens"] if usage["total_tokens"] is not None else "",
                "changed_file_count": len(episode.get("changed_files", [])),
                **snapshot_metadata[episode["id"]],
                **evidence,
            }
            row.update(category_fields(score))
            rows.append(row)
            details.append(
                {
                    "episode_id": episode["id"],
                    "snapshot": snapshot_metadata[episode["id"]],
                    "oracle": score,
                }
            )
            if validate_seal(bundle) != (True, []):
                raise ValueError(f"scoring modified sealed bundle for {episode['id']}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0])
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    write_json(
        args.output.with_suffix(".manifest.json"),
        {
            "scorer_version": "async-ttl-cache-v2",
            "run_id": run["id"],
            "run_status": run["status"],
            "bundle_hashes": bundle_hashes,
            "episodes": details,
        },
    )
    print(json.dumps({"run_id": run["id"], "episodes": len(rows), "output": str(args.output)}))


if __name__ == "__main__":
    main()
