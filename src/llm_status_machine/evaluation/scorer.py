from __future__ import annotations

from pathlib import Path
from typing import Any

from llm_status_machine.recording.bundle import resolve_bundle_path, validate_seal
from llm_status_machine.utils import read_json, stable_id, utc_now, write_json

SCORER_VERSION = "deterministic-v1"


def score_episode(episode_root: Path) -> dict[str, Any]:
    episode = read_json(episode_root / "episode.json")
    bundle = resolve_bundle_path(episode_root, episode)
    valid, errors = validate_seal(bundle)
    if not valid:
        raise ValueError(f"cannot score unsealed/invalid bundle: {errors}")
    seal = read_json(bundle / "seal.json")
    outcomes = read_json(bundle / "outcomes.json")
    changed = read_json(bundle / "changed-files.json")
    transcript_lines = sum(1 for line in (bundle / "transcript.jsonl").read_bytes().splitlines() if line)
    score = {
        "id": stable_id("score", {"bundle": seal["bundle_sha256"], "scorer": SCORER_VERSION}),
        "episode_id": episode["id"],
        "bundle_sha256": seal["bundle_sha256"],
        "scorer": SCORER_VERSION,
        "created_at": utc_now(),
        "passed": all(value["status"] == "completed" for value in outcomes.values()),
        "changed_file_count": len(changed),
        "transcript_record_count": transcript_lines,
        "metrics": {
            "process_completed": outcomes["process"]["status"] == "completed",
            "protocol_completed": outcomes["protocol"]["status"] == "completed",
            "capture_completed": outcomes["capture"]["status"] == "completed",
            "workspace_completed": outcomes["workspace"]["status"] == "completed",
        },
    }
    output = episode_root / "evaluations" / f"{SCORER_VERSION}.json"
    write_json(output, score)
    if read_json(bundle / "seal.json")["bundle_sha256"] != seal["bundle_sha256"]:
        raise RuntimeError("scoring modified RawBundle")
    return score
