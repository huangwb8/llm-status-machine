from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from llm_status_machine.recording.bundle import RawBundle
from llm_status_machine.utils import ensure_within, read_json, sha256_file, stable_id, utc_now, write_json
from llm_status_machine.version import SCHEMA_VERSION

LEGACY_FILES = ("prompt.txt", "stdout.txt", "stderr.txt", "transcript.ndjson", "diff.patch", "metadata.json")


def _episode_dirs(root: Path) -> list[tuple[Path, str]]:
    runs = root / "runs"
    found: list[tuple[Path, str]] = []
    if not runs.is_dir():
        return found
    for run in sorted(path for path in runs.iterdir() if path.is_dir()):
        for episode in sorted(path for path in run.iterdir() if path.is_dir()):
            layout = "state" if episode.name.startswith("state-") else "session"
            found.append((episode, layout))
    return found


def inventory_legacy(root: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    store_path = root / "store.json"
    store = read_json(store_path) if store_path.exists() else {}
    episodes = _episode_dirs(root)
    return {
        "root": str(root),
        "store_sha256": sha256_file(store_path) if store_path.exists() else None,
        "store_counts": {key: len(value) for key, value in store.items() if isinstance(value, list)},
        "disk_run_count": len({path.parent.name for path, _ in episodes}),
        "disk_episode_count": len(episodes),
        "layouts": {
            "state": sum(layout == "state" for _, layout in episodes),
            "session": sum(layout == "session" for _, layout in episodes),
        },
    }


def validate_legacy(root: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    issues: list[dict[str, str]] = []
    for episode, layout in _episode_dirs(root):
        ensure_within(root, episode)
        missing = [name for name in LEGACY_FILES if not (episode / name).exists()]
        if missing:
            issues.append({"path": str(episode), "layout": layout, "issue": f"missing: {', '.join(missing)}"})
        metadata = episode / "metadata.json"
        if metadata.exists():
            try:
                json.loads(metadata.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                issues.append(
                    {"path": str(metadata), "layout": layout, "issue": f"invalid metadata: {error}"}
                )
    return {"valid": not issues, "issues": issues, **inventory_legacy(root)}


def import_legacy(source_root: Path, data_root: Path) -> dict[str, Any]:
    source_root = source_root.resolve(strict=True)
    import_id = stable_id("legacy", {"root": str(source_root), "inventory": inventory_legacy(source_root)})
    destination = data_root.resolve() / "legacy-imports" / import_id
    if destination.exists():
        return read_json(destination / "import.json")
    destination.mkdir(parents=True)
    imported = 0
    for source, layout in _episode_dirs(source_root):
        ensure_within(source_root, source)
        episode_id = stable_id("episode", {"legacy_path": str(source.relative_to(source_root))})
        raw = RawBundle(destination / "episodes" / episode_id / "raw-bundle")
        observed: dict[str, Any] = {
            "legacy_path": str(source),
            "legacy_layout": layout,
            "runtime": "unknown",
            "model": "unknown",
        }
        for legacy_name in LEGACY_FILES:
            candidate = source / legacy_name
            if not candidate.is_file():
                continue
            target_name = {
                "prompt.txt": "prompt.md",
                "stdout.txt": "stdout.raw",
                "stderr.txt": "stderr.raw",
                "transcript.ndjson": "transcript.jsonl",
            }.get(legacy_name, legacy_name)
            raw.write_bytes(target_name, candidate.read_bytes())
        artifacts_source = source / "artifacts"
        if artifacts_source.is_dir():
            for artifact in artifacts_source.rglob("*"):
                if artifact.is_file():
                    relative = artifact.relative_to(artifacts_source)
                    target = raw.root / "artifacts" / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(artifact, target)
        for required in ("prompt.md", "stdout.raw", "stderr.raw", "transcript.jsonl", "diff.patch"):
            if not (raw.root / required).exists():
                raw.write_bytes(required, b"")
        raw.write_json("legacy-observed.json", observed)
        raw.write_json(
            "outcomes.json",
            {
                "process": {"status": "completed", "detail": "legacy observed"},
                "protocol": {"status": "completed", "detail": "legacy observed"},
                "capture": {"status": "completed", "detail": "legacy observed"},
                "workspace": {"status": "completed", "detail": "legacy observed"},
            },
        )
        raw.write_json("workspace.initial.json", {"sha256": "unknown", "entries": []})
        raw.write_json("workspace.final.json", {"sha256": "unknown", "entries": []})
        raw.write_json("changed-files.json", [])
        raw.write_json("artifacts.json", [])
        seal = raw.seal(run_id=import_id, episode_id=episode_id, attempt_id="legacy-attempt-1")
        write_json(
            raw.root.parent / "episode.json",
            {
                "schema_version": SCHEMA_VERSION,
                "id": episode_id,
                "run_id": import_id,
                "status": "imported",
                "ordinal": imported + 1,
                "bundle": str(raw.root),
                "bundle_relative": "raw-bundle",
                "bundle_sha256": seal["bundle_sha256"],
            },
        )
        imported += 1
    report = {
        "schema_version": SCHEMA_VERSION,
        "id": import_id,
        "source": str(source_root),
        "destination": str(destination),
        "imported_episodes": imported,
        "created_at": utc_now(),
        "source_inventory": inventory_legacy(source_root),
    }
    write_json(destination / "import.json", report)
    return report
