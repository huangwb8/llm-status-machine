from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

from llm_status_machine.utils import canonical_json, sha256_bytes, sha256_file


def build_manifest(root: Path, excludes: set[str] | None = None) -> dict[str, Any]:
    root = root.resolve()
    excludes = excludes or set()
    entries: list[dict[str, Any]] = []
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        walk_directories = []
        for name in sorted(name for name in directories if name not in excludes):
            path = current_path / name
            if path.is_symlink():
                info = path.lstat()
                entries.append(
                    {
                        "path": path.relative_to(root).as_posix(),
                        "mode": stat.S_IMODE(info.st_mode),
                        "size": info.st_size,
                        "mtime_ns": info.st_mtime_ns,
                        "type": "symlink",
                        "target": os.readlink(path),
                    }
                )
            else:
                walk_directories.append(name)
        directories[:] = walk_directories
        for name in sorted(files):
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if any(part in excludes for part in path.relative_to(root).parts):
                continue
            info = path.lstat()
            entry: dict[str, Any] = {
                "path": relative,
                "mode": stat.S_IMODE(info.st_mode),
                "size": info.st_size,
                "mtime_ns": info.st_mtime_ns,
            }
            if path.is_symlink():
                entry.update(type="symlink", target=os.readlink(path))
            elif path.is_file():
                entry.update(type="file", sha256=sha256_file(path))
            else:
                entry.update(type="special")
            entries.append(entry)
    payload = {"root_name": root.name, "entries": entries}
    payload["sha256"] = sha256_bytes(canonical_json(entries))
    return payload


def _run_git(workspace: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "LLM Status Machine",
        "GIT_AUTHOR_EMAIL": "lsm@localhost",
        "GIT_COMMITTER_NAME": "LLM Status Machine",
        "GIT_COMMITTER_EMAIL": "lsm@localhost",
    }
    return subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=workspace,
        env=env,
        check=check,
        capture_output=True,
    )


class WorkspaceBackend:
    def __init__(self, excludes: list[str]) -> None:
        self.excludes = set(excludes)

    def validate_source(self, source: Path) -> Path:
        resolved = source.resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError(f"workspace is not a directory: {source}")
        for current, directories, files in os.walk(resolved, topdown=True, followlinks=False):
            directories[:] = [name for name in directories if name not in self.excludes]
            for name in [*directories, *files]:
                path = Path(current) / name
                if not path.is_symlink():
                    continue
                target = Path(os.readlink(path))
                if target.is_absolute():
                    raise ValueError(f"absolute workspace symlink is not allowed: {path}")
                resolved_target = (path.parent / target).resolve(strict=False)
                if resolved_target != resolved and resolved not in resolved_target.parents:
                    raise ValueError(f"workspace symlink escapes source: {path}")
        return resolved

    def materialize(self, source: Path, destination: Path) -> dict[str, Any]:
        source = self.validate_source(source)
        if destination.exists():
            raise FileExistsError(destination)

        def ignore(_directory: str, names: list[str]) -> set[str]:
            return {name for name in names if name in self.excludes}

        shutil.copytree(source, destination, symlinks=True, ignore=ignore)
        initial = build_manifest(destination, {".git"})
        _run_git(destination, "init", "--initial-branch=main", "--quiet")
        # WorkspaceFixture.excludes is the evidence boundary; host/user Git ignore files are not.
        _run_git(destination, "add", "--all", "--force")
        _run_git(destination, "commit", "--allow-empty", "--quiet", "-m", "initial snapshot")
        initial["git_commit"] = _run_git(destination, "rev-parse", "HEAD").stdout.decode().strip()
        return initial

    def capture_final(self, workspace: Path) -> tuple[dict[str, Any], list[dict[str, str]], bytes]:
        # Keep the final commit byte-for-byte aligned with the manifest, including ignored evidence.
        _run_git(workspace, "add", "--all", "--force")
        diff = _run_git(workspace, "diff", "--cached", "--binary", "--full-index", "HEAD", "--", ".").stdout
        status_output = _run_git(workspace, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
        changed: list[dict[str, str]] = []
        records = [record for record in status_output.split(b"\0") if record]
        index = 0
        while index < len(records):
            record = records[index]
            decoded = record.decode("utf-8", "surrogateescape")
            item = {"status": decoded[:2], "path": decoded[3:]}
            if "R" in decoded[:2] or "C" in decoded[:2]:
                index += 1
                if index < len(records):
                    item["original_path"] = records[index].decode("utf-8", "surrogateescape")
            changed.append(item)
            index += 1
        final = build_manifest(workspace, {".git"})
        _run_git(workspace, "commit", "--allow-empty", "--quiet", "-m", "final snapshot")
        final["git_commit"] = _run_git(workspace, "rev-parse", "HEAD").stdout.decode().strip()
        return final, changed, diff
