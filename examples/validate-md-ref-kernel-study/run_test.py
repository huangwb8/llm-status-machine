"""Run the validate-md-ref / bensz-skill-kernel scenario through LSM.

The external integration remains a real Codex workflow, but LSM owns the
StudySpec, frozen TrialPlan, episode scheduling, workspace snapshots and
sealed RawBundles. Each LSM episode invokes this file in ``--episode-worker``
mode as a custom-command harness.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# The LSM custom-command environment is intentionally minimal and may not
# carry the caller's PYTHONPATH. Make the checkout importable when this file
# is re-entered as an episode worker.
REPOSITORY = Path(__file__).resolve().parents[2]
if str(REPOSITORY / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY / "src"))

from llm_status_machine.domain.models import (
    ExecutionProfile,
    ModelEndpoint,
    PromptRevision,
    StatePolicy,
    StudySpec,
    WorkspaceFixture,
)
from llm_status_machine.execution.runner import RunEngine
from llm_status_machine.runtimes.providers import lock_runtime
from llm_status_machine.study.compiler import compile_study, write_plan

SCRIPT = Path(__file__).resolve()
DEFAULT_SKILLS_ROOT = Path("/Volumes/2T01/Github/skills")
DEFAULT_ARTICLE = Path(
    "/Volumes/2T01/winE/我的坚果云/样式备份/网站/blognas.hwb0307.com/blog/new02/ai/"
    "GPT-5.6系列模型的社区反馈、基准表现和使用建议.md"
)
TASK_ID_PATTERN = re.compile(
    r"TaskID\s*[=:：]\s*([0-9]{4}-[0-9]{2}-[0-9]{2}(?:-[0-9]{2}){2,3})", re.IGNORECASE
)
CREDENTIAL_PATTERN = re.compile(r"(?i)(?<![a-z0-9])(?:sk|sess|key)-[a-z0-9_.*-]{8,}")


def _resolve_codex_home(explicit: Path | None = None) -> Path | None:
    """Resolve a Codex config root without exposing any config contents."""

    candidate = explicit or (
        Path(os.environ["CODEX_HOME"]) if os.environ.get("CODEX_HOME") else Path.home() / ".codex"
    )
    candidate = candidate.expanduser().resolve()
    if not candidate.is_dir():
        if explicit is None and not os.environ.get("CODEX_HOME"):
            return None
        raise ValueError(f"CODEX_HOME must reference an existing directory: {candidate}")
    return candidate


def _resolve_codex_executable(explicit: Path | None = None) -> Path:
    """Resolve an executable path while preserving PATH-based commands."""

    requested = str(explicit or os.environ.get("CODEX_EXECUTABLE", "codex"))
    discovered = shutil.which(requested)
    if discovered:
        return Path(discovered).expanduser().resolve()
    candidate = Path(requested).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    candidate = candidate.resolve()
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    raise ValueError(f"Codex executable was not found or is not executable: {requested}")


def _redact(value: str, codex_home: str | None = None) -> str:
    value = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    value = CREDENTIAL_PATTERN.sub("[REDACTED_CREDENTIAL]", value)
    if codex_home:
        value = value.replace(str(Path(codex_home).resolve()), "[CODEX_HOME]")
    return value


def _task_id_from_output(stdout: str) -> str | None:
    match = TASK_ID_PATTERN.search(stdout)
    return match.group(1) if match else None


def _allocate_task_id(workspace: Path) -> str:
    candidate_time = datetime.now(UTC).replace(microsecond=0)
    existing = {
        path.name.removeprefix("task-validate-md-ref-") for path in workspace.glob("task-validate-md-ref-*")
    }
    while True:
        candidate = candidate_time.strftime("%Y-%m-%d-%H-%M-%S")
        if candidate not in existing:
            return candidate
        candidate_time += timedelta(seconds=1)


def _codex_command(
    executable: Path,
    *,
    model: str,
    reasoning_effort: str,
    workspace: Path,
    writable_dirs: list[Path],
    prompt: str,
) -> list[str]:
    command = [
        str(executable),
        "exec",
        "--json",
        "--model",
        model,
        "--sandbox",
        "workspace-write",
        "--cd",
        str(workspace.resolve()),
    ]
    for directory in writable_dirs:
        command.extend(("--add-dir", str(directory.resolve())))
    command.extend(("-c", f'model_reasoning_effort="{reasoning_effort}"', "--ephemeral", prompt))
    return command


def run_codex(
    prompt: str,
    *,
    stage_dir: Path,
    executable: Path,
    model: str,
    reasoning_effort: str,
    workspace: Path,
    writable_dirs: list[Path],
    timeout: float,
    dry_run: bool,
    codex_home: str | None,
) -> dict[str, Any]:
    stage_dir.mkdir(parents=True, exist_ok=True)
    (stage_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    if dry_run:
        stdout, stderr, returncode, command = "dry-run completed", "", 0, ["<dry-run>"]
    else:
        command = _codex_command(
            executable,
            model=model,
            reasoning_effort=reasoning_effort,
            workspace=workspace,
            writable_dirs=writable_dirs,
            prompt=prompt,
        )
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        }
        if codex_home:
            environment["CODEX_HOME"] = codex_home
        try:
            result = subprocess.run(
                command,
                cwd=REPOSITORY,
                env=environment,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            stdout, stderr, returncode = result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired as error:
            stdout = error.stdout or ""
            stderr = error.stderr or ""
            stderr = stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else stderr
            stderr += "\nCodex invocation timed out"
            returncode = 124
    safe_stdout, safe_stderr = _redact(stdout, codex_home), _redact(stderr, codex_home)
    (stage_dir / "stdout.txt").write_text(safe_stdout, encoding="utf-8")
    (stage_dir / "stderr.txt").write_text(safe_stderr, encoding="utf-8")
    result = {
        "command": command,
        "returncode": returncode,
        "ok": returncode == 0,
        "stdout": safe_stdout,
        "stderr": safe_stderr,
    }
    (stage_dir / "result.json").write_text(
        json.dumps(
            {k: v for k, v in result.items() if k not in {"stdout", "stderr"}}, ensure_ascii=False, indent=2
        )
        + "\n",
        encoding="utf-8",
    )
    return result


def build_prompts(task_id: str, workspace: Path, article: Path, skills_root: Path) -> dict[str, str]:
    plan = skills_root / "docs/plans" / f"plan-validate-md-ref-{task_id}.md"
    return {
        "update": f"使用 install-bensz-skills 安装 {skills_root / 'skills/beta/validate-md-ref'} 。更新本机 bensz-skill-kernel 这个python包至最新版；源代码在 {skills_root / 'packages/bensz-skill-kernel'} 。",
        "task_id": "生成一个标签作为本次测试的唯一ID：TaskID={yyyy-mm-dd-HH-mm-ss}。这里就是时间戳；每次测试都开一个新的；但如果用户的多轮对话在同一个会话里，不能重复地建。",
        "validate": f"使用 {skills_root / 'skills/beta/validate-md-ref'} skill 检查 {article} 这个博客文章的参考文献。中间的运行过程保存在 {workspace}",
        "evaluate": f"请调查{workspace}里状态机和验证器是否生效；如果生效，如何协作；对于整个过程你有什么看法（比如，这个实例有没有暴露出 {skills_root / 'packages/bensz-skill-kernel'} 存在的源代码缺陷）？如果 {skills_root / 'packages/bensz-skill-kernel'} 或者 {skills_root / 'skills/beta/validate-md-ref'} 确实有缺陷，请你写个源代码优化计划，保存在 {plan}；如果没有缺陷，请客观评价并跳过修改源代码，且不要写优化计划。",
        "optimize": f"根据 {plan} 优化 {skills_root / 'packages/bensz-skill-kernel'} 或 {skills_root / 'skills/beta/validate-md-ref'} 的源代码。",
    }


def episode_worker(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="LSM custom-command worker")
    parser.add_argument("--episode-worker", action="store_true")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    parser.add_argument("--article", type=Path, required=True)
    parser.add_argument("--skills-root", type=Path, required=True)
    parser.add_argument("--codex-executable", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning-effort", required=True)
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    task_id = _allocate_task_id(args.workspace)
    task_workspace = args.workspace / f"task-validate-md-ref-{task_id}"
    task_workspace.mkdir(parents=True)
    prompts = build_prompts(task_id, task_workspace, args.article, args.skills_root)
    writable = [REPOSITORY, args.skills_root, task_workspace, args.article.parent]
    try:
        codex_home_path = _resolve_codex_home()
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    if not args.dry_run and codex_home_path is None:
        print("CODEX_HOME is not configured and ~/.codex does not exist", file=sys.stderr)
        return 2
    codex_home = str(codex_home_path) if codex_home_path else None
    stages: dict[str, dict[str, Any]] = {}
    for stage in ("update", "task_id", "validate", "evaluate"):
        result = run_codex(
            prompts[stage],
            stage_dir=task_workspace / "stages" / stage,
            executable=args.codex_executable,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            workspace=task_workspace,
            writable_dirs=writable,
            timeout=args.timeout,
            dry_run=args.dry_run,
            codex_home=codex_home,
        )
        stages[stage] = {"ok": result["ok"], "returncode": result["returncode"]}
        if stage == "task_id":
            stages[stage]["reported_task_id"] = _task_id_from_output(result["stdout"])
        print(
            json.dumps(
                {"type": "stage.completed", "stage": stage, "task_id": task_id, "ok": result["ok"]},
                ensure_ascii=False,
            ),
            flush=True,
        )
        if not result["ok"]:
            summary = {"status": "failed", "task_id": task_id, "stages": stages}
            args.artifacts_dir.mkdir(parents=True, exist_ok=True)
            (args.artifacts_dir / "workflow-summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(json.dumps({"type": "workflow.failed", "task_id": task_id}, ensure_ascii=False), flush=True)
            return 1
    plan_path = args.skills_root / "docs/plans" / f"plan-validate-md-ref-{task_id}.md"
    optimization = "not-needed"
    if plan_path.exists():
        result = run_codex(
            prompts["optimize"],
            stage_dir=task_workspace / "stages" / "optimize",
            executable=args.codex_executable,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            workspace=task_workspace,
            writable_dirs=writable,
            timeout=args.timeout,
            dry_run=args.dry_run,
            codex_home=codex_home,
        )
        stages["optimize"] = {"ok": result["ok"], "returncode": result["returncode"]}
        optimization = "completed" if result["ok"] else "failed"
        print(
            json.dumps(
                {"type": "stage.completed", "stage": "optimize", "task_id": task_id, "ok": result["ok"]},
                ensure_ascii=False,
            ),
            flush=True,
        )
        if not result["ok"]:
            return 1
    args.artifacts_dir.mkdir(parents=True, exist_ok=True)
    summary = {"status": "completed", "task_id": task_id, "optimization": optimization, "stages": stages}
    (args.artifacts_dir / "workflow-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"type": "workflow.completed", "task_id": task_id, "optimization": optimization},
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


def build_study(
    output_root: Path,
    *,
    repeats: int,
    article: Path,
    skills_root: Path,
    executable: Path,
    model: str,
    reasoning_effort: str,
    timeout: float,
    dry_run: bool,
) -> StudySpec:
    source = output_root / "source"
    source.mkdir(parents=True)
    (source / "README.md").write_text("# validate-md-ref LSM integration fixture\n", encoding="utf-8")
    runtime = lock_runtime(
        surface="custom_command", executable=Path(sys.executable), requested_version=platform.python_version()
    )
    custom_argv = [
        runtime.executable,
        str(SCRIPT),
        "--episode-worker",
        "--workspace",
        "{workspace}",
        "--artifacts-dir",
        "{artifacts_dir}",
        "--article",
        str(article.resolve()),
        "--skills-root",
        str(skills_root.resolve()),
        "--codex-executable",
        str(executable),
        "--model",
        model,
        "--reasoning-effort",
        reasoning_effort,
        "--timeout",
        str(timeout),
    ]
    if dry_run:
        custom_argv.append("--dry-run")
    return StudySpec(
        name="validate-md-ref-kernel-study",
        repeats=repeats,
        concurrency=1,
        state_policy=StatePolicy.CARRY_FORWARD,
        prompts=[
            PromptRevision(
                id="validate-md-ref-kernel",
                body="运行 validate-md-ref / bensz-skill-kernel 外部集成回归工作流，并保留完整阶段证据。",
            )
        ],
        workspace=WorkspaceFixture(path=str(source.resolve())),
        runtime=runtime,
        endpoint=ModelEndpoint(model_id=model, provider="external-codex"),
        profile=ExecutionProfile(
            name="external-integration",
            permissions="workspace-write",
            custom_argv=custom_argv,
            prompt_transport="file",
            decoder="jsonl",
            timeout_seconds=timeout,
            terminate_grace_seconds=3.0,
            env_allowlist=["CODEX_HOME"],
        ),
    )


def main(argv: list[str] | None = None) -> int:
    raw_argv = argv or sys.argv[1:]
    if "--episode-worker" in raw_argv:
        return episode_worker(raw_argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output-root", type=Path, default=Path("tmp/validate-md-ref-kernel-study"))
    parser.add_argument("--article", type=Path, default=DEFAULT_ARTICLE)
    parser.add_argument("--skills-root", type=Path, default=DEFAULT_SKILLS_ROOT)
    parser.add_argument("--codex-executable", type=Path)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--reasoning-effort", default="high")
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--episode-worker", action="store_true")
    parser.add_argument(
        "--codex-home", type=Path, help="Codex config directory; defaults to CODEX_HOME or ~/.codex"
    )
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    try:
        codex_home = _resolve_codex_home(args.codex_home)
    except ValueError as error:
        parser.error(str(error))
    if args.dry_run and args.codex_executable is None and not os.environ.get("CODEX_EXECUTABLE"):
        codex_executable = Path("codex")
    else:
        try:
            codex_executable = _resolve_codex_executable(args.codex_executable)
        except ValueError as error:
            parser.error(str(error))
    if not args.dry_run and codex_home is None:
        parser.error("CODEX_HOME must reference an existing external Codex configuration")
    if codex_home:
        os.environ["CODEX_HOME"] = str(codex_home)
    output_root = args.output_root if args.output_root.is_absolute() else REPOSITORY / args.output_root
    if output_root.exists():
        parser.error(f"output root already exists: {output_root}")
    output_root.mkdir(parents=True)
    spec = build_study(
        output_root,
        repeats=args.repeats,
        article=args.article,
        skills_root=args.skills_root,
        executable=codex_executable,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        timeout=args.timeout,
        dry_run=args.dry_run,
    )
    plan = compile_study(spec)
    plan_path = output_root / "plan.jsonl"
    write_plan(plan, plan_path)
    data_root = output_root / "data"
    engine = RunEngine(data_root)
    try:
        run = asyncio.run(engine.run(plan))
    finally:
        engine.close()
    summary = {
        "schema_version": 1,
        "status": run["status"],
        "run_id": run["id"],
        "plan_id": run["plan_id"],
        "plan": str(plan_path),
        "data_root": str(data_root),
        "repeats": args.repeats,
        "concurrency": run["concurrency"],
        "state_policy": run["state_policy"],
        "episodes": run["episodes"],
    }
    summary_path = output_root / "validate-md-ref-kernel-study.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": run["status"],
                "run_id": run["id"],
                "manifest": str(summary_path),
                "episodes": len(run["episodes"]),
            },
            ensure_ascii=False,
        )
    )
    return 0 if run["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
