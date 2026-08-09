from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parents[1]


def _load_runner() -> ModuleType:
    path = ROOT / "harness/phase_runner.py"
    spec = importlib.util.spec_from_file_location("prior_washout_phase_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load phase runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PROBE = """\
#!/bin/sh
readable() {
  if /bin/dd if="$1" of=/dev/null bs=1 count=1 2>/dev/null; then
    printf true
  else
    printf false
  fi
}
local_read=$(readable "$1")
external_read=$(readable "$2")
hidden_read=$(readable "$3")
if printf ok > write-canary.txt 2>/dev/null; then workspace_write=true; else workspace_write=false; fi
if /usr/bin/curl --max-time 2 -fsS https://example.com -o /dev/null 2>/dev/null; then
  network=true
else
  network=false
fi
printf '{"external_read":{"allowed":%s,"error_type":"DeniedOrMissing"},' "$external_read"
printf '"hidden_read":{"allowed":%s,"error_type":"DeniedOrMissing"},' "$hidden_read"
printf '"local_read":{"allowed":%s,"error_type":null},' "$local_read"
printf '"network":{"allowed":%s,"error_type":"DeniedOrUnavailable"},' "$network"
printf '"workspace_write":{"allowed":%s,"error_type":null}}\n' "$workspace_write"
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex-executable", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    codex_home_value = os.environ.get("CODEX_HOME")
    if not codex_home_value:
        raise SystemExit("CODEX_HOME must reference the existing external Codex configuration")
    runner = _load_runner()
    credential = runner.validate_codex_home(Path(codex_home_value), REPOSITORY)
    discovered = shutil.which("codex")
    codex = args.codex_executable or (Path(discovered) if discovered else None)
    if codex is None:
        raise FileNotFoundError("codex executable was not found")

    with tempfile.TemporaryDirectory(
        prefix=".lsm-codex-preflight-", dir=REPOSITORY.parent
    ) as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        workspace.mkdir()
        local = workspace / "local-canary.txt"
        local.write_text("local", encoding="utf-8")
        external = root / "external-canary.txt"
        external.write_text("external", encoding="utf-8")
        probe = workspace / "probe.py"
        probe.write_text(PROBE, encoding="utf-8")
        empty_codex_home = root / "empty-codex-home"
        empty_codex_home.mkdir(mode=0o700)
        command = [str(codex)]
        for override in runner.permission_profile_overrides():
            command.extend(["-c", override])
        command.extend(
            [
                "sandbox",
                "--permission-profile",
                "lsm-experiment",
                "--cd",
                str(workspace),
                "/bin/sh",
                str(probe),
                str(local),
                str(external),
                str(ROOT / "oracle_tests/hidden_tasks.json"),
            ]
        )
        result = subprocess.run(
            command,
            cwd=workspace,
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "LANG": os.environ.get("LANG", "C.UTF-8"),
                "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
                "CODEX_HOME": str(empty_codex_home),
            },
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        observed = json.loads(result.stdout)
    valid = (
        observed["local_read"]["allowed"]
        and observed["workspace_write"]["allowed"]
        and not observed["external_read"]["allowed"]
        and not observed["hidden_read"]["allowed"]
        and not observed["network"]["allowed"]
    )
    payload = {
        "status": "completed" if valid else "failed",
        "valid": valid,
        "credential": credential,
        "checks": observed,
        "credential_ref": "env:CODEX_HOME",
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    if not valid:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
