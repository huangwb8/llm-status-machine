from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from filelock import FileLock

from llm_status_machine.domain.models import RuntimeBuild
from llm_status_machine.utils import atomic_write, sha256_bytes, sha256_file


def _platform_identity() -> str:
    return f"{platform.system().lower()}-{platform.machine().lower()}"


def probe_version(executable: Path, version_args: list[str] | None = None) -> str:
    result = subprocess.run(
        [str(executable), *(version_args or ["--version"])],
        check=False,
        capture_output=True,
        timeout=15,
    )
    output = (result.stdout + result.stderr).decode("utf-8", "replace").strip()
    if result.returncode != 0:
        raise ValueError(f"runtime version probe failed ({result.returncode}): {output}")
    return output


def simulator_runtime() -> RuntimeBuild:
    # Keep the virtual-environment launcher path. Resolving its symlink can
    # silently switch to the base interpreter and lose the installed package.
    executable = Path(sys.executable).absolute()
    return RuntimeBuild(
        provider="simulator",
        surface="simulator",
        executable=str(executable),
        requested="builtin",
        version=platform.python_version(),
        sha256=sha256_file(executable),
        platform=_platform_identity(),
        version_output=sys.version.splitlines()[0],
        reproducible=True,
    )


def lock_runtime(
    *,
    surface: str,
    executable: Path,
    requested_version: str,
    version_args: list[str] | None = None,
    reproducible: bool = False,
) -> RuntimeBuild:
    resolved = executable.expanduser().resolve(strict=True)
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ValueError(f"runtime is not executable: {resolved}")
    version_output = probe_version(resolved, version_args)
    if requested_version not in version_output:
        raise ValueError(f"version mismatch: expected {requested_version!r}, observed {version_output!r}")
    return RuntimeBuild(
        provider="unmanaged" if not reproducible else "managed",
        surface=surface,
        requested=requested_version,
        version=requested_version,
        executable=str(resolved),
        sha256=sha256_file(resolved),
        platform=_platform_identity(),
        version_output=version_output,
        reproducible=reproducible,
    )


def fetch_runtime(
    *,
    surface: str,
    version: str,
    url: str,
    expected_sha256: str,
    cache_root: Path,
    offline: bool = False,
) -> RuntimeBuild:
    target = cache_root / "objects" / expected_sha256 / "runtime"
    lock_path = cache_root / "locks" / f"{expected_sha256}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(lock_path, timeout=120):
        if not target.exists():
            if offline:
                raise FileNotFoundError(f"runtime not available in offline cache: {expected_sha256}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(url, timeout=60) as response:
                content = response.read()
            observed = sha256_bytes(content)
            if observed != expected_sha256:
                raise ValueError(f"runtime digest mismatch: expected {expected_sha256}, observed {observed}")
            atomic_write(target, content)
            target.chmod(0o755)
    observed = sha256_file(target)
    if observed != expected_sha256:
        raise ValueError(f"cached runtime digest mismatch: expected {expected_sha256}, observed {observed}")
    return lock_runtime(
        surface=surface,
        executable=target,
        requested_version=version,
        reproducible=True,
    ).model_copy(update={"source_url": url})


def resolve_on_path(name: str) -> Path:
    """Discovery helper only; resolved paths must be explicitly locked before a plan can execute."""
    found = shutil.which(name)
    if not found:
        raise FileNotFoundError(name)
    return Path(found).resolve()
