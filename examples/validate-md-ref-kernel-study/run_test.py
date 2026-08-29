"""Run the validate-md-ref / bensz-skill-kernel regression workflow.

The workflow is intentionally kept as a small, auditable orchestrator rather
than a StudySpec: each iteration is a fresh Codex invocation and has its own
TaskID and workspace.  Use ``--dry-run`` in CI or when the external inputs are
not available.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[2]
DEFAULT_SKILLS_ROOT = Path("/Volumes/2T01/Github/skills")
DEFAULT_ARTICLE = Path(
    "/Volumes/2T01/winE/我的坚果云/样式备份/网站/blognas.hwb0307.com/blog/new02/ai/"
    "GPT-5.6系列模型的社区反馈、基准表现和使用建议.md"
)
TASK_ID_PATTERN = re.compile(
    r"TaskID\s*[=:：]\s*([0-9]{4}-[0-9]{2}-[0-9]{2}(?:-[0-9]{2}){2,3})",
    re.IGNORECASE,
)
CREDENTIAL_PATTERN = re.compile(r"(?i)(?<![a-z0-9])(?:sk|sess|key)-[a-z0-9_.*-]{8,}")


def _redact(value: str, codex_home: str | None = None) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    value = CREDENTIAL_PATTERN.sub("[REDACTED_CREDENTIAL]", value)
    if codex_home:
        value = value.replace(str(Path(codex_home).resolve()), "[CODEX_HOME]")
    return value


def _allocate_task_id(output_root: Path, seen: set[str]) -> str:
    """Return a timestamp ID that cannot reuse an existing iteration workspace."""

    candidate_time = datetime.now(UTC).replace(microsecond=0)
    while True:
        candidate = candidate_time.strftime("%Y-%m-%d-%H-%M-%S")
        workspace = output_root / f"task-validate-md-ref-{candidate}"
        if candidate not in seen and not workspace.exists():
            return candidate
        candidate_time += timedelta(seconds=1)


def _load_seen_task_ids(output_root: Path) -> set[str]:
    seen: set[str] = set()
    for manifest in output_root.glob("validate-md-ref-kernel-study*.json"):
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for iteration in payload.get("iterations", []):
            task_id = iteration.get("task_id")
            if isinstance(task_id, str):
                seen.add(task_id)
    return seen


def _task_id_from_output(stdout: str) -> str | None:
    match = TASK_ID_PATTERN.search(stdout)
    return match.group(1) if match else None


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
        stdout = "dry-run completed"
        stderr = ""
        returncode = 0
        command: list[str] = ["<dry-run>"]
    else:
        command = _codex_command(
            executable,
            model=model,
            reasoning_effort=reasoning_effort,
            workspace=stage_dir.parent.parent,
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
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            stderr += "\nCodex invocation timed out"
            returncode = 124
    safe_stdout = _redact(stdout, codex_home)
    safe_stderr = _redact(stderr, codex_home)
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
        json.dumps({k: v for k, v in result.items() if k not in {"stdout", "stderr"}}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return result


def build_prompts(task_id: str, workspace: Path, article: Path, skills_root: Path) -> dict[str, str]:
    plan = REPOSITORY / "docs/plans" / f"plan-validate-md-ref-{task_id}.md"
    return {
        "update": (
            "使用 install-bensz-skills 安装 "
            f"{skills_root / 'skills/beta/validate-md-ref'} 。更新本机 bensz-skill-kernel "
            "这个python包至最新版；源代码在 "
            f"{skills_root / 'packages/bensz-skill-kernel'} 。"
        ),
        "task_id": "生成一个标签作为本次测试的唯一ID：TaskID={yyyy-mm-dd-HH-mm-ss}。这里就是时间戳；每次测试都开一个新的；但如果用户的多轮对话在同一个会话里，不能重复地建。",
        "validate": (
            f"使用 {skills_root / 'skills/beta/validate-md-ref'} skill 检查 {article} "
            f"这个博客文章的参考文献。中间的运行过程保存在 {workspace}"
        ),
        "evaluate": (
            f"请调查{workspace}里状态机和验证器是否生效；如果生效，如何协作；对于整个过程你有什么看法 "
            f"（比如，这个实例有没有暴露出 {skills_root / 'packages/bensz-skill-kernel'} 存在的源代码缺陷）？"
            f"如果 {skills_root / 'packages/bensz-skill-kernel'} 或者 {skills_root / 'skills/beta/validate-md-ref'} "
            f"确实有缺陷，请你写个源代码优化计划，保存在 {plan}；如果没有缺陷，请客观评价并跳过修改源代码，且不要写优化计划。"
        ),
        "optimize": (
            f"根据 {plan} 优化 {skills_root / 'packages/bensz-skill-kernel'} 或 "
            f"{skills_root / 'skills/beta/validate-md-ref'} 的源代码。"
        ),
    }


def run_iteration(
    *,
    iteration: int,
    output_root: Path,
    executable: Path,
    model: str,
    reasoning_effort: str,
    article: Path,
    skills_root: Path,
    timeout: float,
    dry_run: bool,
    seen_ids: set[str],
    codex_home: str | None,
) -> dict[str, Any]:
    task_id = _allocate_task_id(output_root, seen_ids)
    seen_ids.add(task_id)
    workspace = output_root / f"task-validate-md-ref-{task_id}"
    workspace.mkdir(parents=True)
    prompts = build_prompts(task_id, workspace, article, skills_root)
    record: dict[str, Any] = {
        "iteration": iteration,
        "task_id": task_id,
        "workspace": str(workspace),
        "plan": str(REPOSITORY / "docs/plans" / f"plan-validate-md-ref-{task_id}.md"),
        "stages": {},
    }
    writable = [REPOSITORY, skills_root, workspace, article.parent]
    for stage in ("update", "task_id", "validate", "evaluate"):
        stage_result = run_codex(
            prompts[stage],
            stage_dir=workspace / "stages" / stage,
            executable=executable,
            model=model,
            reasoning_effort=reasoning_effort,
            workspace=workspace,
            writable_dirs=writable,
            timeout=timeout,
            dry_run=dry_run,
            codex_home=codex_home,
        )
        record["stages"][stage] = {"ok": stage_result["ok"], "returncode": stage_result["returncode"]}
        if stage == "task_id":
            reported_task_id = _task_id_from_output(stage_result["stdout"])
            record["reported_task_id"] = reported_task_id
            if reported_task_id and reported_task_id != task_id:
                reported_workspace = output_root / f"task-validate-md-ref-{reported_task_id}"
                if reported_task_id not in seen_ids and not reported_workspace.exists():
                    workspace.rename(reported_workspace)
                    seen_ids.discard(task_id)
                    seen_ids.add(reported_task_id)
                    task_id = reported_task_id
                    workspace = reported_workspace
                    prompts = build_prompts(task_id, workspace, article, skills_root)
                    record["task_id"] = task_id
                    record["workspace"] = str(workspace)
                    record["plan"] = str(REPOSITORY / "docs/plans" / f"plan-validate-md-ref-{task_id}.md")
                else:
                    record["task_id_warning"] = "Codex output TaskID conflicted with an existing iteration"
        if not stage_result["ok"]:
            record["status"] = "failed"
            return record

    plan_path = Path(record["plan"])
    if plan_path.exists():
        stage_result = run_codex(
            prompts["optimize"],
            stage_dir=workspace / "stages" / "optimize",
            executable=executable,
            model=model,
            reasoning_effort=reasoning_effort,
            workspace=workspace,
            writable_dirs=writable,
            timeout=timeout,
            dry_run=dry_run,
            codex_home=codex_home,
        )
        record["stages"]["optimize"] = {"ok": stage_result["ok"], "returncode": stage_result["returncode"]}
        record["optimization"] = "completed" if stage_result["ok"] else "failed"
    else:
        record["optimization"] = "not-needed"
    record["status"] = "completed"
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3, help="number of fresh iterations (default: 3)")
    parser.add_argument("--output-root", type=Path, default=Path(".bensz-api"))
    parser.add_argument("--article", type=Path, default=DEFAULT_ARTICLE)
    parser.add_argument("--skills-root", type=Path, default=DEFAULT_SKILLS_ROOT)
    parser.add_argument("--codex-executable", type=Path, default=Path(os.environ.get("CODEX_EXECUTABLE", "codex")))
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--reasoning-effort", default="high")
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--dry-run", action="store_true", help="record the complete workflow without invoking Codex")
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    output_root = args.output_root if args.output_root.is_absolute() else REPOSITORY / args.output_root
    output_root.mkdir(parents=True, exist_ok=True)
    codex_home = os.environ.get("CODEX_HOME")
    if not args.dry_run and not codex_home:
        parser.error("CODEX_HOME must reference an existing external Codex configuration")
    seen_ids = _load_seen_task_ids(output_root)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "repeats": args.repeats,
        "dry_run": args.dry_run,
        "iterations": [],
    }
    for iteration in range(1, args.repeats + 1):
        result = run_iteration(
            iteration=iteration,
            output_root=output_root,
            executable=args.codex_executable,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            article=args.article,
            skills_root=args.skills_root,
            timeout=args.timeout,
            dry_run=args.dry_run,
            seen_ids=seen_ids,
            codex_home=codex_home,
        )
        manifest["iterations"].append(result)
        if result["status"] == "failed":
            manifest["status"] = "failed"
            break
    else:
        manifest["status"] = "completed"
    manifest_path = output_root / "validate-md-ref-kernel-study.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "manifest": str(manifest_path), "iterations": len(manifest["iterations"])}, ensure_ascii=False))
    return 0 if manifest["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
