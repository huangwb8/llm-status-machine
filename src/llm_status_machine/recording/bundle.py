from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from llm_status_machine.utils import canonical_json, read_json, sha256_bytes, sha256_file, write_json
from llm_status_machine.version import SCHEMA_VERSION


def resolve_bundle_path(episode_root: Path, episode: dict[str, Any]) -> Path:
    relative = episode.get("bundle_relative")
    if relative:
        candidate = (episode_root / relative).resolve()
        root = episode_root.resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError("bundle path escapes episode root")
        return candidate
    return Path(episode["bundle"])


def bundle_manifest(root: Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "seal.json":
            continue
        relative = path.relative_to(root).as_posix()
        files.append({"path": relative, "size": path.stat().st_size, "sha256": sha256_file(path)})
    return files


class RawBundle:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=False)
        (self.root / "artifacts").mkdir()

    def write_json(self, relative: str, value: Any) -> None:
        write_json(self.root / relative, value)

    def write_bytes(self, relative: str, value: bytes) -> None:
        from llm_status_machine.utils import atomic_write

        atomic_write(self.root / relative, value)

    def seal(self, *, run_id: str, episode_id: str, attempt_id: str) -> dict[str, Any]:
        files = bundle_manifest(self.root)
        seal = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "episode_id": episode_id,
            "attempt_id": attempt_id,
            "files": files,
            "bundle_sha256": sha256_bytes(canonical_json(files)),
        }
        write_json(self.root / "seal.json", seal)
        directory_fd = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return seal


def validate_seal(root: Path) -> tuple[bool, list[str]]:
    seal_path = root / "seal.json"
    if not seal_path.exists():
        return False, ["missing seal.json"]
    seal = read_json(seal_path)
    actual = bundle_manifest(root)
    errors: list[str] = []
    if actual != seal.get("files"):
        errors.append("bundle file manifest mismatch")
    if sha256_bytes(canonical_json(actual)) != seal.get("bundle_sha256"):
        errors.append("bundle digest mismatch")
    return not errors, errors
