from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "fixture" / "tools"))

from evaluate_candidate import ExpressionError, normalized_mse, parse_expression
from event_writer import emit, sha256_file, write_json
from memory_policy import transform_workspace

ARM_IDS = frozenset(
    {"neutral-open", "correct-open", "false-open", "false-gated", "false-purged"}
)
TASK_IDS = ("poly", "rational", "decay", "periodic", "saturation", "combined")
CANDIDATES_PER_TASK = 3
MAX_STREAM_BYTES = 16 * 1024 * 1024
CONDITION_PATTERN = re.compile(r"^PRIOR_CONDITION=([a-z]+)$", re.MULTILINE)
POLICY_PATTERN = re.compile(r"^MEMORY_POLICY=([a-z]+)$", re.MULTILINE)
CREDENTIAL_PATTERN = re.compile(
    rb"(?i)(?:sk|sess|key)-[a-z0-9_.*-]{8,}|bearer[ \t]+[a-z0-9_.*-]{8,}"
)

PRIORS = {
    "neutral": (
        "把六项过程分别在多项式、有理式、指数、周期及组合函数族间做同等强度比较；"
        "任何函数族都只作为待检验起点。"
    ),
    "correct": (
        "同平台先导测量提示：poly 优先考虑二次多项式，rational 优先考虑有理饱和，"
        "decay 优先考虑指数衰减，periodic 优先考虑周期项叠加线性趋势，"
        "saturation 优先考虑有理饱和，combined 优先考虑指数衰减与周期项组合。"
    ),
    "false": (
        "同平台先导测量提示：poly 优先考虑指数增长，rational 优先考虑线性关系，"
        "decay 优先考虑双曲衰减，periodic 优先考虑线性趋势，"
        "saturation 优先考虑指数饱和，combined 优先考虑双曲衰减。"
    ),
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sanitize_nested_stream(value: bytes) -> bytes:
    return CREDENTIAL_PATTERN.sub(b"[REDACTED_CREDENTIAL]", value)


def validate_codex_home(codex_home: Path, project: Path) -> dict[str, Any]:
    resolved = codex_home.resolve(strict=True)
    project = project.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("CODEX_HOME must name an existing directory")
    if resolved == project or project in resolved.parents:
        raise ValueError("CODEX_HOME must remain outside the project")
    info = resolved.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ValueError("CODEX_HOME must be owner-controlled and not group/world writable")
    return {"environment_name": "CODEX_HOME", "external": True}


def build_codex_argv(
    *,
    executable: Path,
    model: str,
    reasoning_effort: str,
    workspace: Path,
    schema: Path,
    last_message: Path,
) -> list[str]:
    argv = [
        str(executable),
        "exec",
        "--json",
        "--model",
        model,
        "--ephemeral",
        "--ignore-rules",
        "--skip-git-repo-check",
        "-C",
        str(workspace),
        "--output-schema",
        str(schema),
        "--output-last-message",
        str(last_message),
    ]
    for override in permission_profile_overrides(reasoning_effort=reasoning_effort):
        argv.extend(["-c", override])
    argv.append("-")
    return argv


def permission_profile_overrides(*, reasoning_effort: str | None = None) -> list[str]:
    values = [
        'approval_policy="never"',
        'default_permissions="lsm-experiment"',
        (
            'permissions.lsm-experiment={description="LSM isolated experiment workspace",'
            'filesystem={":minimal"="read",":workspace_roots"={"."="write"}},'
            "network={enabled=false}}"
        ),
        'shell_environment_policy.inherit="none"',
        "shell_environment_policy.ignore_default_excludes=false",
        'web_search="disabled"',
    ]
    if reasoning_effort:
        values.append(f'model_reasoning_effort="{reasoning_effort}"')
    return values


def _observations_for_stage(stage: str, payload: dict[str, Any]) -> dict[str, Any]:
    tasks = []
    for task in payload["tasks"]:
        visible = {"id": task["id"], "train": task["train"]}
        if stage in {"B", "C"}:
            visible["validation"] = task["validation"]
        tasks.append(visible)
    return {"tasks": tasks}


def build_stage_prompt(
    *, stage: str, arm: str, observations: dict[str, Any], memory: dict[str, Any]
) -> str:
    del memory
    if arm not in ARM_IDS:
        raise ValueError(f"unknown arm: {arm}")
    if stage not in {"A", "B", "C"}:
        raise ValueError(f"unknown stage: {stage}")
    lines = [
        (
            "你正在独立完成一次六任务符号回归阶段。只使用下面提供的可见观测；"
            "不要读取工作目录之外的文件，不要联网，也不要寻找隐藏真值。"
            "表达式只允许数字、x、+、-、*、/、**、sin、cos、exp、log。"
        ),
        "每个任务必须按 candidate_index=1,2,3 给出恰好三个依次改进的候选；保留失败思路，不要省略任务。",
        "你可以读取当前工作目录中的既有状态，并可写入 memory/working-notes.md；最终回复必须严格符合 JSON schema。",
    ]
    if stage == "A":
        condition = arm.split("-", 1)[0]
        lines.append(f"研究起点：{PRIORS[condition]}")
    elif stage == "B":
        lines.append("现在加入一批独立验证观测。重新检验当前状态中的候选、测量记录和假设，不假定早期叙述正确。")
    else:
        lines.append("根据当前全部可见证据完成恢复与提交；给出最可靠候选，同时如实记录不确定性。")
    lines.append("可见观测：" + _canonical(observations))
    return "\n\n".join(lines)


def stage_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["candidates", "summary"],
        "properties": {
            "summary": {"type": "string", "maxLength": 2000},
            "candidates": {
                "type": "array",
                "minItems": len(TASK_IDS) * CANDIDATES_PER_TASK,
                "maxItems": len(TASK_IDS) * CANDIDATES_PER_TASK,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["task_id", "candidate_index", "expression", "rationale", "confidence"],
                    "properties": {
                        "task_id": {"type": "string", "enum": list(TASK_IDS)},
                        "candidate_index": {"type": "integer", "minimum": 1, "maximum": 3},
                        "expression": {"type": "string", "minLength": 1, "maxLength": 256},
                        "rationale": {"type": "string", "maxLength": 1000},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                },
            },
        },
    }


def _validate_stage_result(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
        raise TypeError("stage result is not a candidate object")
    seen = {(item.get("task_id"), item.get("candidate_index")) for item in payload["candidates"]}
    expected = {(task_id, index) for task_id in TASK_IDS for index in range(1, 4)}
    if seen != expected or len(payload["candidates"]) != len(expected):
        raise ValueError("stage result must contain exactly three candidates per task")
    return payload


def _evaluate_candidates(
    stage: str, result: dict[str, Any], observations: dict[str, Any]
) -> list[dict[str, Any]]:
    tasks = {task["id"]: task for task in observations["tasks"]}
    records = []
    for item in sorted(result["candidates"], key=lambda value: (value["task_id"], value["candidate_index"])):
        expression = str(item["expression"])
        error = None
        train_nmse = None
        validation_nmse = None
        try:
            parse_expression(expression)
            task = tasks[item["task_id"]]
            train_nmse = normalized_mse(expression, task["train"])
            if stage in {"B", "C"}:
                validation_nmse = normalized_mse(expression, task["validation"])
        except (ExpressionError, ArithmeticError, ValueError) as caught:
            error = type(caught).__name__
        records.append(
            {
                "stage": stage,
                "task_id": item["task_id"],
                "candidate_index": item["candidate_index"],
                "expression": expression,
                "train_nmse": train_nmse,
                "validation_nmse": validation_nmse,
                "evaluation_error": error,
            }
        )
    return records


def _load_nested_runtime(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    runtime = payload.get("runtime_build", payload)
    executable = Path(runtime["executable"])
    if runtime.get("surface") != "codex_exec_cli" or not executable.is_absolute():
        raise ValueError("nested runtime must be an absolute codex_exec_cli lock")
    if sha256_file(executable) != runtime["sha256"]:
        raise ValueError("nested Codex executable digest drifted")
    observed = subprocess.run(
        [str(executable), "--version"], check=True, capture_output=True, text=True
    ).stdout.strip()
    if observed != runtime["version_output"]:
        raise ValueError("nested Codex version output drifted")
    return runtime


def _extract_thread_id(stdout_path: Path) -> str | None:
    for line in stdout_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started":
            return event.get("thread_id") or event.get("thread", {}).get("id")
    return None


def _run_codex_stage(
    *,
    executable: Path,
    model: str,
    reasoning_effort: str,
    workspace: Path,
    schema: Path,
    last_message: Path,
    stdout_path: Path,
    stderr_path: Path,
    prompt: str,
    timeout: float,
) -> dict[str, Any]:
    argv = build_codex_argv(
        executable=executable,
        model=model,
        reasoning_effort=reasoning_effort,
        workspace=workspace,
        schema=schema,
        last_message=last_message,
    )
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "CODEX_HOME": os.environ["CODEX_HOME"],
    }
    started = time.monotonic()
    timed_out = False
    with tempfile.TemporaryDirectory(prefix="lsm-nested-stream-") as temporary:
        private_root = Path(temporary)
        private_stdout = private_root / "stdout.raw"
        private_stderr = private_root / "stderr.raw"
        with private_stdout.open("wb") as stdout, private_stderr.open("wb") as stderr:
            process = subprocess.Popen(
                argv,
                cwd=workspace,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
            try:
                process.communicate(prompt.encode("utf-8"), timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
        for source, target in ((private_stdout, stdout_path), (private_stderr, stderr_path)):
            if source.stat().st_size > MAX_STREAM_BYTES:
                raise ValueError(f"nested stream exceeded {MAX_STREAM_BYTES} bytes")
            target.write_bytes(sanitize_nested_stream(source.read_bytes()))
    if timed_out:
        raise TimeoutError("nested Codex stage timed out")
    if process.returncode != 0:
        raise RuntimeError(f"nested Codex stage exited with code {process.returncode}")
    result = _validate_stage_result(json.loads(last_message.read_text(encoding="utf-8")))
    return {
        "pid": process.pid,
        "process_group": process.pid,
        "thread_id": _extract_thread_id(stdout_path),
        "return_code": process.returncode,
        "duration_seconds": time.monotonic() - started,
        "result": result,
    }


def _arm_from_prompt(prompt: str) -> tuple[str, str, str]:
    condition_match = CONDITION_PATTERN.search(prompt)
    policy_match = POLICY_PATTERN.search(prompt)
    if condition_match is None or policy_match is None:
        raise ValueError("experiment prompt is missing allocation markers")
    condition, policy = condition_match.group(1), policy_match.group(1)
    arm = f"{condition}-{policy}"
    if arm not in ARM_IDS:
        raise ValueError(f"unsupported arm allocation: {arm}")
    return arm, condition, policy


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    parser.add_argument("--nested-runtime", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--stage-timeout", type=float, default=900)
    args = parser.parse_args()

    validate_codex_home(Path(os.environ["CODEX_HOME"]), PROJECT)
    runtime = _load_nested_runtime(args.nested_runtime)
    prompt = args.prompt_file.read_text(encoding="utf-8")
    arm, _condition, policy = _arm_from_prompt(prompt)
    visible = json.loads((ROOT / "fixture/datasets/visible-tasks.json").read_text(encoding="utf-8"))
    workspace = Path.cwd().resolve()
    evidence = workspace / ".experiment-evidence"
    stages_root = workspace / "agent-stages"
    pristine = workspace / ".experiment-pristine"
    if any(path.exists() for path in (evidence, stages_root, pristine)):
        raise FileExistsError("reserved experiment namespace already exists")
    evidence.mkdir()
    stages_root.mkdir()
    pristine.mkdir()
    (pristine / "README.md").write_text(
        "This workspace contains only the current stage state and visible evidence.\n",
        encoding="utf-8",
    )
    args.artifacts_dir.mkdir(parents=True, exist_ok=True)
    schema_path = args.artifacts_dir / "stage-result.schema.json"
    write_json(schema_path, stage_schema())
    emit("nested_runtime.verified", surface="codex_exec_cli", credential_ref="env:CODEX_HOME")

    all_records: list[dict[str, Any]] = []
    identities: list[dict[str, Any]] = []
    previous = pristine
    for stage in ("A", "B", "C"):
        stage_key = stage.lower()
        stage_workspace = stages_root / f"stage-{stage_key}"
        if stage == "A":
            transform_workspace("gated", pristine, pristine, stage_workspace, [])
            evidence_file = stage_workspace / "evidence/validated-ledger.json"
            if evidence_file.exists():
                evidence_file.unlink()
                evidence_file.parent.rmdir()
        else:
            transition = transform_workspace(policy, pristine, previous, stage_workspace, all_records)
            write_json(evidence / f"memory-before-{stage_key}.json", transition)
            emit("memory.transformed", stage=stage, retained=transition["retained"])
        observations = _observations_for_stage(stage, visible)
        stage_prompt = build_stage_prompt(stage=stage, arm=arm, observations=observations, memory={})
        write_json(
            evidence / f"prompt-{stage_key}.json",
            {"stage": stage, "sha256": hashlib.sha256(stage_prompt.encode()).hexdigest()},
        )
        stage_artifacts = args.artifacts_dir / f"stage-{stage_key}"
        stage_artifacts.mkdir()
        last_message = stage_artifacts / "last-message.json"
        stdout_path = stage_artifacts / "stdout.raw"
        stderr_path = stage_artifacts / "stderr.raw"
        emit("phase.started", phase=stage, context_mode="fresh-codex-exec")
        execution = _run_codex_stage(
            executable=Path(runtime["executable"]),
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            workspace=stage_workspace,
            schema=schema_path,
            last_message=last_message,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            prompt=stage_prompt,
            timeout=args.stage_timeout,
        )
        records = _evaluate_candidates(stage, execution.pop("result"), observations)
        all_records.extend(records)
        identities.append({"stage": stage, **execution})
        memory = stage_workspace / "memory"
        memory.mkdir(exist_ok=True)
        write_json(memory / f"evaluated-output-{stage_key}.json", records)
        write_json(evidence / f"phase-{stage_key}.json", records)
        for record in records:
            emit(
                "candidate.evaluated",
                phase=stage,
                task_id=record["task_id"],
                candidate_index=record["candidate_index"],
                evaluation_error=record["evaluation_error"],
            )
        emit("phase.completed", phase=stage, candidate_count=len(records))
        previous = stage_workspace

    thread_ids = [item["thread_id"] for item in identities]
    fresh = len({item["pid"] for item in identities}) == 3 and len(set(thread_ids)) == 3 and None not in thread_ids
    candidates_path = workspace / "protocol/candidates.jsonl"
    candidates_path.parent.mkdir(exist_ok=True)
    candidates_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in all_records),
        encoding="utf-8",
    )
    final_records = [record for record in all_records if record["stage"] == "C" and record["candidate_index"] == 3]
    result = {
        "protocol_version": "real-codex-v1",
        "phase_count": 3,
        "candidate_count": len(all_records),
        "fresh_contexts": fresh,
        "final_expressions": {record["task_id"]: record["expression"] for record in final_records},
    }
    write_json(workspace / "result/final.json", result)
    write_json(evidence / "nested-identities.json", identities)
    if not fresh:
        raise RuntimeError("nested Codex contexts were not provably fresh")
    emit("result", status="completed", protocol_version="real-codex-v1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
