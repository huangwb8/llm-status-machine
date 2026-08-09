from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parents[1]
LSM = [sys.executable, "-m", "llm_status_machine"]


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=10)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait()


def _run_process(
    arguments: list[str],
    *,
    environment: dict[str, str],
    allowed_returncodes: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        arguments,
        cwd=REPOSITORY,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate()
    except BaseException:
        _terminate_process_tree(process)
        raise
    result = subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)
    if result.returncode not in allowed_returncodes:
        raise subprocess.CalledProcessError(
            result.returncode,
            arguments,
            output=result.stdout,
            stderr=result.stderr,
        )
    return result


def run_json(
    arguments: list[str],
    *,
    environment: dict[str, str],
    allowed_returncodes: tuple[int, ...] = (0,),
) -> Any:
    result = _run_process(
        arguments,
        environment=environment,
        allowed_returncodes=allowed_returncodes,
    )
    return json.loads(result.stdout)


def run(arguments: list[str], *, environment: dict[str, str]) -> None:
    _run_process(
        arguments,
        environment=environment,
    )


def credential_scan(root: Path, codex_home: Path) -> dict[str, Any]:
    forbidden_names = {"auth.json", "config.toml", ".codex"}
    forbidden_path = str(codex_home.resolve()).encode()
    bad_names = []
    path_leaks = []
    credential_fragments = []
    credential_pattern = re.compile(rb"(?i)(?:sk|sess|key)-[a-z0-9_.*-]{8,}")
    for path in root.rglob("*"):
        if path.name in forbidden_names:
            bad_names.append(path.relative_to(root).as_posix())
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 32 * 1024 * 1024:
            continue
        content = path.read_bytes()
        if forbidden_path in content:
            path_leaks.append(path.relative_to(root).as_posix())
        if credential_pattern.search(content):
            credential_fragments.append(path.relative_to(root).as_posix())
    if bad_names or path_leaks or credential_fragments:
        raise ValueError("credential boundary scan failed")
    return {
        "valid": True,
        "forbidden_name_count": 0,
        "external_path_leak_count": 0,
        "credential_fragment_count": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("shakedown", "pilot", "confirmatory"), required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--stage-timeout", type=float, default=900)
    parser.add_argument("--repeats", type=int)
    parser.add_argument("--codex-executable", type=Path)
    args = parser.parse_args()
    output = args.root.resolve()
    if output.exists():
        raise FileExistsError(f"real study root already exists: {output}")
    codex_home_value = os.environ.get("CODEX_HOME")
    if not codex_home_value:
        raise SystemExit("CODEX_HOME must reference the existing external Codex configuration")
    codex_home = Path(codex_home_value).resolve(strict=True)
    if codex_home == REPOSITORY or REPOSITORY in codex_home.parents:
        raise ValueError("CODEX_HOME must remain outside the repository")
    output.mkdir(parents=True)
    data_root = output / "data"
    outer_runtime = output / "outer-runtime.json"
    nested_runtime = output / "nested-runtime.json"
    study = output / f"study.{args.mode}.yml"
    plan = output / f"plan.{args.mode}.jsonl"
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "CODEX_HOME": str(codex_home),
    }
    python_version = run_json([*LSM, "harness", "probe", sys.executable, "--json"], environment=environment)[
        "version_output"
    ].split()[1]
    discovered = shutil.which("codex", path=environment["PATH"])
    codex = args.codex_executable or (Path(discovered) if discovered else None)
    if codex is None:
        raise FileNotFoundError("codex executable was not found on PATH")
    codex_version_output = subprocess.run(
        [str(codex), "--version"],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    codex_version = codex_version_output.split()[1]
    run(
        [
            *LSM,
            "harness",
            "lock",
            "--surface",
            "custom_command",
            "--executable",
            sys.executable,
            "--version",
            python_version,
            "--output",
            str(outer_runtime),
        ],
        environment=environment,
    )
    run(
        [
            *LSM,
            "harness",
            "lock",
            "--surface",
            "codex_exec_cli",
            "--executable",
            str(codex),
            "--version",
            codex_version,
            "--output",
            str(nested_runtime),
        ],
        environment=environment,
    )
    prepare = [
        sys.executable,
        str(ROOT / "scripts/prepare_real_study.py"),
        "--runtime",
        str(outer_runtime),
        "--nested-runtime",
        str(nested_runtime),
        "--output",
        str(study),
        "--mode",
        args.mode,
        "--model",
        args.model,
        "--reasoning-effort",
        args.reasoning_effort,
        "--stage-timeout",
        str(args.stage_timeout),
    ]
    if args.repeats:
        prepare.extend(["--repeats", str(args.repeats)])
    run_json(prepare, environment=environment)
    validation = run_json([*LSM, "study", "validate", str(study), "--json"], environment=environment)
    run_json([*LSM, "study", "compile", str(study), str(plan), "--json"], environment=environment)
    run_record = run_json(
        [*LSM, "run", "start", str(plan), "--data-root", str(data_root), "--json"],
        environment=environment,
        allowed_returncodes=(0, 1),
    )
    if run_record.get("status") not in {"completed", "failed"} or not isinstance(
        run_record.get("episodes"), list
    ):
        raise ValueError("run start did not return a sealed terminal run record")
    run_id = run_record["id"]
    for episode_id in run_record["episodes"]:
        seal = run_json(
            [*LSM, "episode", "validate", episode_id, "--data-root", str(data_root), "--json"],
            environment=environment,
        )
        if not seal["valid"]:
            raise ValueError(f"invalid episode seal: {episode_id}")
    evaluation = run_json(
        [*LSM, "evaluate", "run", run_id, "--data-root", str(data_root), "--json"],
        environment=environment,
    )
    dataset = run_json(
        [*LSM, "research", "dataset", run_id, "--data-root", str(data_root), "--json"],
        environment=environment,
    )
    scan = credential_scan(output, codex_home)
    summary = {
        "status": run_record["status"],
        "mode": args.mode,
        "scope": "real three-stage Codex run",
        "run_id": run_id,
        "episode_count": len(run_record["episodes"]),
        "evaluation_run_id": evaluation["run_id"],
        "dataset": dataset,
        "confirmatory_valid": validation["confirmatory_valid"],
        "credential_scan": scan,
        "credential_ref": "env:CODEX_HOME",
    }
    (output / "run-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
