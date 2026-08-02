from __future__ import annotations

import asyncio
import csv
import json
import shutil
import sys
import tarfile
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml
from rich.console import Console
from rich.table import Table

from llm_status_machine.domain.models import (
    ExecutionProfile,
    ModelEndpoint,
    PromptRevision,
    StatePolicy,
    StudySpec,
    WorkspaceFixture,
)
from llm_status_machine.evaluation.scorer import score_episode
from llm_status_machine.execution.runner import RunEngine
from llm_status_machine.harnesses.base import list_adapters
from llm_status_machine.legacy.importer import import_legacy, inventory_legacy, validate_legacy
from llm_status_machine.prompts.core import freeze_prompt, lint_prompt, render_prompt
from llm_status_machine.recording.bundle import resolve_bundle_path, validate_seal
from llm_status_machine.runtimes.providers import (
    fetch_runtime,
    lock_runtime,
    probe_version,
    simulator_runtime,
)
from llm_status_machine.storage.index import IndexStore
from llm_status_machine.study.compiler import compile_study, load_plan, load_study, plan_bytes, write_plan
from llm_status_machine.utils import read_json, utc_now, write_json
from llm_status_machine.version import SCHEMA_VERSION, __version__
from llm_status_machine.workspaces.backend import build_manifest

app = typer.Typer(no_args_is_help=True, help="可审计的本地 LLM Harness 行为实验台")
harness_app = typer.Typer(no_args_is_help=True)
prompt_app = typer.Typer(no_args_is_help=True)
workspace_app = typer.Typer(no_args_is_help=True)
study_app = typer.Typer(no_args_is_help=True)
run_app = typer.Typer(no_args_is_help=True)
episode_app = typer.Typer(no_args_is_help=True)
evaluate_app = typer.Typer(no_args_is_help=True)
export_app = typer.Typer(no_args_is_help=True)
store_app = typer.Typer(no_args_is_help=True)
legacy_app = typer.Typer(no_args_is_help=True)
for name, subapp in (
    ("harness", harness_app),
    ("prompt", prompt_app),
    ("workspace", workspace_app),
    ("study", study_app),
    ("run", run_app),
    ("episode", episode_app),
    ("evaluate", evaluate_app),
    ("export", export_app),
    ("store", store_app),
    ("legacy", legacy_app),
):
    app.add_typer(subapp, name=name)

console = Console()


def _emit(value: Any, as_json: bool) -> None:
    if as_json:
        console.print_json(json.dumps(value, ensure_ascii=False, default=str))
    elif isinstance(value, dict):
        table = Table(show_header=False)
        table.add_column("key", style="cyan")
        table.add_column("value")
        for key, item in value.items():
            table.add_row(
                str(key),
                json.dumps(item, ensure_ascii=False, default=str)
                if isinstance(item, (dict, list))
                else str(item),
            )
        console.print(table)
    else:
        console.print(value)


def _find_episode(data_root: Path, episode_id: str) -> Path:
    matches = list((data_root / "runs").glob(f"*/episodes/{episode_id}"))
    if len(matches) != 1:
        raise typer.BadParameter(f"episode not found or ambiguous: {episode_id}")
    return matches[0]


@app.callback(invoke_without_command=True)
def root_callback(version: Annotated[bool, typer.Option("--version", is_eager=True)] = False) -> None:
    if version:
        console.print(__version__)
        raise typer.Exit()


@app.command("init")
def initialize(
    project: Annotated[Path, typer.Argument()] = Path("."),
    force: Annotated[bool, typer.Option("--force")] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    project = project.resolve()
    data_root = project / ".lsm"
    study_path = project / "study.example.yml"
    if data_root.exists() and not force:
        raise typer.BadParameter(f"already initialized: {data_root}")
    for directory in (data_root / "plans", data_root / "runs", data_root / "runtime-cache"):
        directory.mkdir(parents=True, exist_ok=True)
    IndexStore(data_root / "index.sqlite3").close()
    if not study_path.exists():
        workspace = project / "tmp" / "example-workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        readme = workspace / "README.md"
        if not readme.exists():
            readme.write_text("# LSM example workspace\n", encoding="utf-8")
        spec = StudySpec(
            name="simulator-example",
            repeats=3,
            concurrency=1,
            state_policy=StatePolicy.CARRY_FORWARD,
            prompts=[PromptRevision(id="poem", body="请以“新中国的美人”为题写一首七言绝句。")],
            workspace=WorkspaceFixture(path=str(workspace.resolve())),
            runtime=simulator_runtime(),
            endpoint=ModelEndpoint(),
            profile=ExecutionProfile(),
        )
        study_path.write_text(
            yaml.safe_dump(spec.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    _emit({"project": str(project), "data_root": str(data_root), "example_study": str(study_path)}, as_json)


@app.command("doctor")
def doctor(
    data_root: Annotated[Path, typer.Option("--data-root")] = Path(".lsm"),
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    checks = {
        "python": {"ok": sys.version_info >= (3, 12), "value": sys.version.split()[0]},
        "git": {"ok": shutil.which("git") is not None, "value": shutil.which("git")},
        "data_root": {"ok": data_root.parent.exists(), "value": str(data_root.resolve())},
        "disk_free_bytes": {"ok": True, "value": shutil.disk_usage(data_root.parent).free},
        "schema_version": {"ok": True, "value": SCHEMA_VERSION},
    }
    _emit({"ok": all(check["ok"] for check in checks.values()), "checks": checks}, as_json)
    if not all(check["ok"] for check in checks.values()):
        raise typer.Exit(1)


@harness_app.command("list")
def harness_list(as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    values = [
        {"surface": name, "capabilities": sorted(adapter().capabilities)}
        for name, adapter in sorted(list_adapters().items())
    ]
    _emit(values, as_json)


@harness_app.command("probe")
def harness_probe(
    executable: Annotated[Path, typer.Argument()],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _emit(
        {"executable": str(executable.resolve(strict=True)), "version_output": probe_version(executable)},
        as_json,
    )


@harness_app.command("lock")
def harness_lock(
    surface: Annotated[str, typer.Option("--surface")],
    executable: Annotated[Path, typer.Option("--executable")],
    version: Annotated[str, typer.Option("--version")],
    output: Annotated[Path, typer.Option("--output")],
    reproducible: Annotated[bool, typer.Option("--reproducible")] = False,
) -> None:
    runtime = lock_runtime(
        surface=surface, executable=executable, requested_version=version, reproducible=reproducible
    )
    write_json(output, runtime.model_dump(mode="json"))
    _emit(runtime.model_dump(mode="json"), True)


@harness_app.command("fetch")
def harness_fetch(
    surface: Annotated[str, typer.Option("--surface")],
    version: Annotated[str, typer.Option("--version")],
    url: Annotated[str, typer.Option("--url")],
    sha256: Annotated[str, typer.Option("--sha256")],
    cache_root: Annotated[Path, typer.Option("--cache-root")] = Path(".lsm/runtime-cache"),
    offline: Annotated[bool, typer.Option("--offline")] = False,
) -> None:
    runtime = fetch_runtime(
        surface=surface,
        version=version,
        url=url,
        expected_sha256=sha256,
        cache_root=cache_root,
        offline=offline,
    )
    _emit(runtime.model_dump(mode="json"), True)


def _prompt(path: Path, prompt_id: str) -> PromptRevision:
    return PromptRevision(id=prompt_id, body=path.read_text(encoding="utf-8"))


@prompt_app.command("lint")
def prompt_lint(
    path: Path,
    prompt_id: str = "prompt",
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    errors = lint_prompt(_prompt(path, prompt_id))
    _emit({"valid": not errors, "errors": errors}, as_json)
    if errors:
        raise typer.Exit(1)


@prompt_app.command("render")
def prompt_render(path: Path, values: str = "{}", prompt_id: str = "prompt") -> None:
    console.print(render_prompt(_prompt(path, prompt_id), json.loads(values)))


@prompt_app.command("freeze")
def prompt_freeze(path: Path, output: Path, values: str = "{}", prompt_id: str = "prompt") -> None:
    frozen = freeze_prompt(_prompt(path, prompt_id), json.loads(values))
    write_json(output, frozen)
    _emit(frozen, True)


@workspace_app.command("snapshot")
def workspace_snapshot(
    path: Path,
    output: Path | None = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    manifest = build_manifest(
        path.resolve(strict=True), {".git", ".lsm", ".bensz-api", "node_modules", "__pycache__"}
    )
    if output:
        write_json(output, manifest)
    _emit(manifest, as_json)


@study_app.command("validate")
def study_validate(path: Path, as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    spec = load_study(path)
    _emit({"valid": True, "name": spec.name, "repeats": spec.repeats}, as_json)


@study_app.command("compile")
def study_compile(
    path: Path,
    output: Path,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    plan = compile_study(load_study(path))
    write_plan(plan, output)
    _emit(
        {
            "plan_id": plan.id,
            "episodes": len(plan.trials),
            "sha256": __import__("hashlib").sha256(plan_bytes(plan)).hexdigest(),
            "output": str(output),
        },
        as_json,
    )


@study_app.command("estimate")
def study_estimate(path: Path, as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    plan = compile_study(load_study(path))
    estimate = {
        "episodes": len(plan.trials),
        "max_concurrency": plan.concurrency,
        "state_policy": plan.state_policy,
        "minimum_timeout_seconds": sum(trial.profile.timeout_seconds for trial in plan.trials)
        / max(plan.concurrency, 1),
    }
    _emit(estimate, as_json)


@run_app.command("start")
def run_start(
    plan_path: Annotated[Path, typer.Argument()],
    data_root: Annotated[Path, typer.Option("--data-root")] = Path(".lsm"),
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    engine = RunEngine(data_root)
    try:
        result = asyncio.run(engine.run(load_plan(plan_path)))
    finally:
        engine.close()
    _emit(result, as_json)
    if result["status"] != "completed":
        raise typer.Exit(1)


@run_app.command("status")
def run_status(
    run_id: str,
    data_root: Path = Path(".lsm"),
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    store = IndexStore(data_root / "index.sqlite3")
    try:
        run = store.get_run(run_id)
    finally:
        store.close()
    if run is None:
        raise typer.BadParameter(f"run not found: {run_id}")
    _emit(run, as_json)


@run_app.command("reconcile")
def run_reconcile(
    data_root: Path = Path(".lsm"),
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    store = IndexStore(data_root / "index.sqlite3")
    repaired = 0
    try:
        for run in store.list_runs():
            if run["status"] != "running":
                continue
            run["status"] = "orphaned"
            run["finished_at"] = utc_now()
            path = Path(run["path"])
            write_json(path / "run.json", run)
            store.upsert_run(run, path)
            repaired += 1
    finally:
        store.close()
    _emit({"orphaned_runs": repaired}, as_json)


@episode_app.command("show")
def episode_show(
    episode_id: str,
    data_root: Path = Path(".lsm"),
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _emit(read_json(_find_episode(data_root, episode_id) / "episode.json"), as_json)


@episode_app.command("validate")
def episode_validate(
    episode_id: str,
    data_root: Path = Path(".lsm"),
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    episode_root = _find_episode(data_root, episode_id)
    episode = read_json(episode_root / "episode.json")
    valid, errors = validate_seal(resolve_bundle_path(episode_root, episode))
    _emit({"valid": valid, "errors": errors, "bundle_sha256": episode.get("bundle_sha256")}, as_json)
    if not valid:
        raise typer.Exit(1)


@episode_app.command("events")
def episode_events(episode_id: str, data_root: Path = Path(".lsm")) -> None:
    episode_root = _find_episode(data_root, episode_id)
    episode = read_json(episode_root / "episode.json")
    console.print(
        (resolve_bundle_path(episode_root, episode) / "transcript.jsonl").read_text(
            encoding="utf-8", errors="replace"
        ),
        end="",
    )


@episode_app.command("stdout")
def episode_stdout(episode_id: str, data_root: Path = Path(".lsm")) -> None:
    episode_root = _find_episode(data_root, episode_id)
    episode = read_json(episode_root / "episode.json")
    sys.stdout.buffer.write((resolve_bundle_path(episode_root, episode) / "stdout.raw").read_bytes())


@episode_app.command("diff")
def episode_diff(episode_id: str, data_root: Path = Path(".lsm")) -> None:
    episode_root = _find_episode(data_root, episode_id)
    episode = read_json(episode_root / "episode.json")
    sys.stdout.buffer.write((resolve_bundle_path(episode_root, episode) / "diff.patch").read_bytes())


@evaluate_app.command("episode")
def evaluate_episode(
    episode_id: str,
    data_root: Path = Path(".lsm"),
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _emit(score_episode(_find_episode(data_root, episode_id)), as_json)


@export_app.command("run")
def export_run(run_id: str, output: Path, data_root: Path = Path(".lsm"), format: str = "archive") -> None:
    run_root = data_root / "runs" / run_id
    if not run_root.is_dir():
        raise typer.BadParameter(f"run not found: {run_id}")
    episodes = [read_json(path) for path in sorted(run_root.glob("episodes/*/episode.json"))]
    if format == "jsonl":
        output.write_text(
            "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in episodes),
            encoding="utf-8",
        )
    elif format == "csv":
        with output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["id", "run_id", "ordinal", "status", "bundle_sha256"])
            writer.writeheader()
            writer.writerows({key: item.get(key) for key in writer.fieldnames} for item in episodes)
    elif format == "archive":
        with tarfile.open(output, "w:gz") as archive:
            archive.add(run_root, arcname=run_id, recursive=True)
    else:
        raise typer.BadParameter("format must be archive, jsonl, or csv")
    _emit({"output": str(output), "episodes": len(episodes), "format": format}, True)


@store_app.command("reindex")
def store_reindex(
    data_root: Path = Path(".lsm"),
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    store = IndexStore(data_root / "index.sqlite3")
    try:
        report = store.reindex(data_root / "runs")
    finally:
        store.close()
    _emit(report, as_json)


@store_app.command("verify")
def store_verify(
    data_root: Path = Path(".lsm"),
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    failures = []
    for episode_path in sorted((data_root / "runs").glob("*/episodes/*/episode.json")):
        episode = read_json(episode_path)
        valid, errors = validate_seal(resolve_bundle_path(episode_path.parent, episode))
        if not valid:
            failures.append({"episode": episode["id"], "errors": errors})
    _emit({"valid": not failures, "failures": failures}, as_json)
    if failures:
        raise typer.Exit(1)


@legacy_app.command("inventory")
def legacy_inventory(
    root: Annotated[
        Path,
        typer.Argument(help="旧 Node/Web file store 的显式路径。"),
    ],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _emit(inventory_legacy(root), as_json)


@legacy_app.command("validate")
def legacy_validate(
    root: Annotated[
        Path,
        typer.Argument(help="旧 Node/Web file store 的显式路径。"),
    ],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    result = validate_legacy(root)
    _emit(result, as_json)
    if not result["valid"]:
        raise typer.Exit(1)


@legacy_app.command("import")
def legacy_import(
    source: Annotated[
        Path,
        typer.Argument(help="旧 Node/Web file store 的显式路径。"),
    ],
    data_root: Path = Path(".lsm"),
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    _emit(import_legacy(source, data_root), as_json)


@app.command("smoke")
def smoke(
    root: Annotated[Path, typer.Option("--root")] = Path("tmp/core-smoke"),
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    root = root.resolve()
    workspace = root / "source"
    data_root = root / "data"
    if root.exists():
        raise typer.BadParameter(f"smoke root already exists: {root}")
    workspace.mkdir(parents=True)
    (workspace / "README.md").write_text("# Core smoke workspace\n", encoding="utf-8")
    spec = StudySpec(
        name="core-smoke",
        repeats=3,
        concurrency=1,
        state_policy=StatePolicy.CARRY_FORWARD,
        prompts=[PromptRevision(id="core-poem", body="请以“新中国的美人”为题写一首七言绝句。")],
        workspace=WorkspaceFixture(path=str(workspace)),
        runtime=simulator_runtime(),
    )
    plan = compile_study(spec)
    plan_path = root / "plan.jsonl"
    write_plan(plan, plan_path)
    engine = RunEngine(data_root)
    try:
        result = asyncio.run(engine.run(plan))
    finally:
        engine.close()
    _emit(result | {"root": str(root), "plan": str(plan_path)}, as_json)
    if result["status"] != "completed":
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
