from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parents[1]
LSM = [sys.executable, "-m", "llm_status_machine"]


def run_json(arguments: list[str]) -> Any:
    result = subprocess.run(arguments, cwd=REPOSITORY, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def run(arguments: list[str]) -> None:
    subprocess.run(arguments, cwd=REPOSITORY, check=True, capture_output=True, text=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True, help="An absent local output directory.")
    args = parser.parse_args()
    output_root = args.root.resolve()
    if output_root.exists():
        raise FileExistsError(f"qualification root already exists: {output_root}")
    output_root.mkdir(parents=True)
    data_root = output_root / "data"
    runtime = output_root / "runtime.json"
    study = output_root / "study.yml"
    plan = output_root / "plan.jsonl"
    second_plan = output_root / "plan-repeat.jsonl"
    exports = output_root / "exports"
    exports.mkdir()

    doctor = run_json([*LSM, "doctor", "--data-root", str(data_root), "--json"])
    harnesses = run_json([*LSM, "harness", "list", "--json"])
    probe = run_json([*LSM, "harness", "probe", sys.executable, "--json"])
    version = probe["version_output"].split()[1]
    run([*LSM, "harness", "lock", "--surface", "custom_command", "--executable", sys.executable, "--version", version, "--output", str(runtime)])
    run([sys.executable, str(ROOT / "scripts/build_fixture.py")])
    run([sys.executable, str(ROOT / "scripts/prepare_study.py"), "--runtime", str(runtime), "--output", str(study)])
    for prompt in sorted((ROOT / "prompts").glob("*.md")):
        if prompt.name != "prompt-template.md":
            run_json([*LSM, "prompt", "lint", str(prompt), "--json"])
    frozen_prompt = output_root / "prompt.false-gated.frozen.json"
    run([*LSM, "prompt", "freeze", str(ROOT / "prompts/false-gated.md"), str(frozen_prompt), "--prompt-id", "false-gated"])
    workspace_snapshot = output_root / "workspace.snapshot.json"
    run_json([*LSM, "workspace", "snapshot", str(ROOT / "fixture"), "--output", str(workspace_snapshot), "--json"])
    validation = run_json([*LSM, "study", "validate", str(study), "--json"])
    estimate = run_json([*LSM, "study", "estimate", str(study), "--json"])
    power = run_json([*LSM, "study", "power", str(study), "--metric-type", "continuous", "--effect", "0.25", "--standard-deviation", "0.30", "--power", "0.8", "--json"])
    run_json([*LSM, "study", "compile", str(study), str(plan), "--json"])
    run_json([*LSM, "study", "compile", str(study), str(second_plan), "--json"])
    if plan.read_bytes() != second_plan.read_bytes():
        raise ValueError("identical StudySpec compilation did not produce stable plan bytes")
    run([sys.executable, str(ROOT / "scripts/verify_plan.py"), str(plan)])
    recorder = run_json([sys.executable, str(ROOT / "scripts/qualify_recorder.py"), "--root", str(output_root / "recorder-qualification")])

    run_record = run_json([*LSM, "run", "start", str(plan), "--data-root", str(data_root), "--json"])
    run_id = run_record["id"]
    run_json([*LSM, "run", "status", run_id, "--data-root", str(data_root), "--json"])
    episode_ids = run_record["episodes"]
    for episode_id in episode_ids:
        result = run_json([*LSM, "episode", "validate", episode_id, "--data-root", str(data_root), "--json"])
        if not result["valid"]:
            raise ValueError(f"invalid episode seal: {episode_id}")
    run_json([*LSM, "episode", "show", episode_ids[0], "--data-root", str(data_root), "--json"])
    run_json([*LSM, "evaluate", "episode", episode_ids[0], "--data-root", str(data_root), "--json"])
    evaluation = run_json([*LSM, "evaluate", "run", run_id, "--data-root", str(data_root), "--json"])
    dataset = run_json([*LSM, "research", "dataset", run_id, "--data-root", str(data_root), "--json"])
    analysis = run_json([*LSM, "research", "infer", run_id, "--data-root", str(data_root), "--json"])
    report = run_json([*LSM, "research", "report", analysis["analysis_id"], "--data-root", str(data_root), "--json"])
    store_before = run_json([*LSM, "store", "verify", "--data-root", str(data_root), "--json"])
    for format_name, suffix in (("archive", "tar.gz"), ("jsonl", "jsonl"), ("csv", "csv")):
        run([*LSM, "export", "run", run_id, str(exports / f"{run_id}.{suffix}"), "--data-root", str(data_root), "--format", format_name])
    index = data_root / "index.sqlite3"
    backup = data_root / "index.before-reindex.sqlite3"
    if index.exists():
        index.replace(backup)
    reindex = run_json([*LSM, "store", "reindex", "--data-root", str(data_root), "--json"])
    store_after = run_json([*LSM, "store", "verify", "--data-root", str(data_root), "--json"])
    summary = {
        "status": "completed",
        "scope": "deterministic infrastructure qualification; not an LLM behavioral result",
        "run_id": run_id,
        "episode_count": len(episode_ids),
        "plan_sha256": hashlib.sha256(plan.read_bytes()).hexdigest(),
        "doctor_ok": doctor["ok"],
        "custom_command_available": any(item["surface"] == "custom_command" for item in harnesses),
        "confirmatory_valid": validation["confirmatory_valid"] and evaluation["confirmatory_valid"] and analysis["confirmatory_valid"],
        "dataset_rows": dataset["row_count"],
        "estimate": estimate,
        "power": power,
        "analysis_id": analysis["analysis_id"],
        "report": report["report"],
        "store_valid_before_reindex": store_before["valid"],
        "store_valid_after_reindex": store_after["valid"],
        "reindex": reindex,
        "recorder_qualification": recorder,
        "exports": sorted(path.name for path in exports.iterdir()),
        "index_backup": str(backup) if backup.exists() else None,
    }
    summary_path = output_root / "qualification-summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
