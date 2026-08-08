from __future__ import annotations

import asyncio
import os
import platform
import shutil
import traceback
from pathlib import Path
from typing import Any

from llm_status_machine.domain.models import AttemptOutcomes, Outcome, StatePolicy, Trial, TrialPlan
from llm_status_machine.harnesses.base import get_adapter
from llm_status_machine.recording.bundle import RawBundle
from llm_status_machine.recording.recorder import ProcessResult, record_process
from llm_status_machine.storage.index import IndexStore
from llm_status_machine.utils import sha256_file, stable_id, utc_now, write_json
from llm_status_machine.version import RAW_BUNDLE_SCHEMA_VERSION, RUN_SCHEMA_VERSION, __version__
from llm_status_machine.workspaces.backend import WorkspaceBackend, build_manifest


class RunEngine:
    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root.resolve()
        self.runs_root = self.data_root / "runs"
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self.index = IndexStore(self.data_root / "index.sqlite3")
        self._active = 0
        self._max_active = 0
        self._active_lock = asyncio.Lock()
        self._dispatch_count = 0
        self._dispatch_lock = asyncio.Lock()
        self._current_concurrency = 1

    def close(self) -> None:
        self.index.close()

    async def run(self, plan: TrialPlan) -> dict[str, Any]:
        if plan.study_mode == "confirmatory" and not plan.diagnostics.get("confirmatory_valid", False):
            raise ValueError("confirmatory plan failed preflight diagnostics")
        self._dispatch_count = 0
        self._current_concurrency = plan.concurrency
        run_id = stable_id("run", {"plan": plan.id, "started": utc_now(), "pid": os.getpid()})
        run_root = self.runs_root / run_id
        run_root.mkdir(parents=True, exist_ok=False)
        (run_root / "episodes").mkdir()
        frozen_plan_path = run_root / "plan.json"
        write_json(frozen_plan_path, plan.model_dump(mode="json"))
        started_at = utc_now()
        run_manifest: dict[str, Any] = {
            "schema_version": RUN_SCHEMA_VERSION,
            "id": run_id,
            "plan_id": plan.id,
            "plan_sha256": sha256_file(frozen_plan_path),
            "study_name": plan.study_name,
            "status": "running",
            "started_at": started_at,
            "finished_at": None,
            "concurrency": plan.concurrency,
            "state_policy": plan.state_policy,
            "study_mode": plan.study_mode,
            "design": plan.design,
            "design_diagnostics": plan.diagnostics,
            "episodes": [],
        }
        write_json(run_root / "run.json", run_manifest)
        self.index.upsert_run(run_manifest, run_root)
        results: list[dict[str, Any]] = []
        try:
            if plan.state_policy == StatePolicy.CARRY_FORWARD:
                source: Path | None = None
                for trial in plan.trials:
                    result = await self._run_trial(run_id, run_root, trial, source)
                    results.append(result)
                    if result["status"] != "completed":
                        break
                    source = Path(result["workspace"])
            else:
                semaphore = asyncio.Semaphore(plan.concurrency)

                async def bounded(trial: Trial) -> dict[str, Any]:
                    async with semaphore:
                        return await self._run_trial(run_id, run_root, trial, None)

                tasks = [asyncio.create_task(bounded(trial)) for trial in plan.trials]
                results = list(await asyncio.gather(*tasks))
            status = (
                "completed"
                if len(results) == len(plan.trials) and all(r["status"] == "completed" for r in results)
                else "failed"
            )
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except Exception:  # noqa: BLE001 - run boundary must persist every unexpected failure
            status = "failed"
            run_manifest["error"] = traceback.format_exc()
        finally:
            run_manifest.update(
                status=status,
                finished_at=utc_now(),
                episodes=[result["id"] for result in sorted(results, key=lambda item: item["ordinal"])],
                max_active_attempts=self._max_active,
            )
            write_json(run_root / "run.json", run_manifest)
            self.index.upsert_run(run_manifest, run_root)
        return run_manifest

    async def _run_trial(
        self, run_id: str, run_root: Path, trial: Trial, inherited_source: Path | None
    ) -> dict[str, Any]:
        episode_root = run_root / "episodes" / trial.id
        attempt_id = "attempt-1"
        attempt_root = episode_root / "attempts" / attempt_id
        bundle_root = attempt_root / "raw-bundle"
        episode_root.mkdir(parents=True, exist_ok=False)
        attempt_root.mkdir(parents=True)
        raw = RawBundle(bundle_root)
        actual_started_at = utc_now()
        async with self._dispatch_lock:
            self._dispatch_count += 1
            actual_dispatch_batch = (self._dispatch_count - 1) // self._current_concurrency + 1
        workspace = episode_root / "workspace"
        source = inherited_source or Path(trial.workspace.path)
        backend = WorkspaceBackend(trial.workspace.excludes)
        result: ProcessResult | None = None
        initial_manifest: dict[str, Any] = {"entries": [], "sha256": "unknown"}
        final_manifest: dict[str, Any] = {"entries": [], "sha256": "unknown"}
        changed: list[dict[str, str]] = []
        diff = b""
        workspace_error: str | None = None
        process_error: str | None = None
        raw.write_bytes("prompt.md", trial.actual_prompt.encode("utf-8"))
        raw.write_json("trial.json", trial.model_dump(mode="json"))
        raw.write_json(
            "runtime.json",
            trial.runtime.model_dump(mode="json")
            | {"observed_executable_sha256": sha256_file(Path(trial.runtime.executable))},
        )
        try:
            if sha256_file(Path(trial.runtime.executable)) != trial.runtime.sha256:
                raise ValueError("runtime executable digest changed after plan freeze")
            if inherited_source is None:
                observed_baseline = build_manifest(source, set(trial.workspace.excludes))["sha256"]
                if observed_baseline != trial.workspace.baseline_sha256:
                    raise ValueError("source workspace changed after plan freeze")
            initial_manifest = await asyncio.to_thread(backend.materialize, source, workspace)
            raw.write_json("workspace.initial.json", initial_manifest)
            adapter = get_adapter(trial.runtime.surface)
            launch = adapter.build_launch(trial, raw.root / "prompt.md", raw.root / "artifacts")
            raw.write_json(
                "launch.json",
                {
                    "argv_sha256": stable_id("argv", launch.argv),
                    "executable": launch.argv[0],
                    "argument_count": len(launch.argv),
                    "environment_names": sorted(launch.env),
                    "decoder": launch.decoder,
                },
            )
            async with self._count_active():
                result = await record_process(
                    launch=launch,
                    adapter=adapter,
                    cwd=workspace,
                    raw_bundle=raw.root,
                    run_id=run_id,
                    episode_id=trial.id,
                    attempt_id=attempt_id,
                    timeout=trial.profile.timeout_seconds,
                    terminate_grace=trial.profile.terminate_grace_seconds,
                )
        except Exception as error:  # noqa: BLE001 - attempt boundary must seal partial evidence
            process_error = f"{type(error).__name__}: {error}"
            if not (raw.root / "stdout.raw").exists():
                raw.write_bytes("stdout.raw", b"")
            if not (raw.root / "stderr.raw").exists():
                raw.write_bytes("stderr.raw", process_error.encode())
            if not (raw.root / "transcript.jsonl").exists():
                raw.write_bytes("transcript.jsonl", b"")
        finally:
            if workspace.exists() and (workspace / ".git").exists():
                try:
                    final_manifest, changed, diff = await asyncio.to_thread(backend.capture_final, workspace)
                except Exception as error:  # noqa: BLE001 - capture failures are first-class outcomes
                    workspace_error = f"{type(error).__name__}: {error}"
            else:
                workspace_error = process_error or "workspace was not materialized"
            raw.write_json("workspace.final.json", final_manifest)
            raw.write_json("changed-files.json", changed)
            raw.write_bytes("diff.patch", diff)
            artifacts = []
            for artifact in sorted((raw.root / "artifacts").rglob("*")):
                if artifact.is_file():
                    artifacts.append(
                        {
                            "path": artifact.relative_to(raw.root / "artifacts").as_posix(),
                            "size": artifact.stat().st_size,
                            "sha256": sha256_file(artifact),
                        }
                    )
            raw.write_json("artifacts.json", artifacts)
        process_status = (
            "timed_out"
            if result and result.timed_out
            else "completed"
            if result
            and result.return_code
            in get_adapter(trial.runtime.surface)
            .build_launch(trial, raw.root / "prompt.md", raw.root / "artifacts")
            .success_exit_codes
            else "failed"
        )
        protocol_status = (
            "completed" if result and result.terminal_seen and result.parse_errors == 0 else "failed"
        )
        capture_status = (
            "completed"
            if all((raw.root / name).exists() for name in ("stdout.raw", "stderr.raw", "transcript.jsonl"))
            else "failed"
        )
        workspace_status = "completed" if not workspace_error else "failed"
        outcomes = AttemptOutcomes(
            process=Outcome(status=process_status, detail=process_error),
            protocol=Outcome(
                status=protocol_status,
                detail=None
                if protocol_status == "completed"
                else f"parse_errors={result.parse_errors if result else 'unknown'}",
            ),
            capture=Outcome(status=capture_status),
            workspace=Outcome(status=workspace_status, detail=workspace_error),
        )
        raw.write_json("outcomes.json", outcomes.model_dump(mode="json"))
        actual_finished_at = utc_now()
        deviations = []
        if actual_dispatch_batch != trial.dispatch_batch:
            deviations.append(
                {
                    "kind": "dispatch_batch_mismatch",
                    "planned": trial.dispatch_batch,
                    "actual": actual_dispatch_batch,
                }
            )
        raw.write_json(
            "metadata.json",
            {
                "schema_version": RAW_BUNDLE_SCHEMA_VERSION,
                "application_version": __version__,
                "run_id": run_id,
                "episode_id": trial.id,
                "attempt_id": attempt_id,
                "host": {"platform": platform.platform(), "python": platform.python_version()},
                "source_workspace": str(source),
                "workspace": str(workspace),
                "result": result.__dict__ if result else None,
                "outcomes": outcomes.model_dump(mode="json"),
                "actual_started_at": actual_started_at,
                "actual_finished_at": actual_finished_at,
                "planned_dispatch_batch": trial.dispatch_batch,
                "actual_dispatch_batch": actual_dispatch_batch,
                "design_deviations": deviations,
            },
        )
        seal = raw.seal(run_id=run_id, episode_id=trial.id, attempt_id=attempt_id)
        status = "completed" if outcomes.completed else process_status
        episode = {
            "schema_version": RAW_BUNDLE_SCHEMA_VERSION,
            "id": trial.id,
            "run_id": run_id,
            "ordinal": trial.ordinal,
            "attempt_id": attempt_id,
            "status": status,
            "workspace": str(workspace),
            "parent_trial_id": trial.parent_trial_id,
            "arm_id": trial.arm_id,
            "comparison_set_id": trial.comparison_set_id,
            "pair_id": trial.pair_id,
            "block_id": trial.block_id,
            "sequence_position": trial.sequence_position,
            "planned_dispatch_batch": trial.dispatch_batch,
            "actual_dispatch_batch": actual_dispatch_batch,
            "actual_started_at": actual_started_at,
            "actual_finished_at": actual_finished_at,
            "design_deviations": deviations,
            "bundle": str(raw.root),
            "bundle_relative": raw.root.relative_to(episode_root).as_posix(),
            "bundle_sha256": seal["bundle_sha256"],
            "changed_files": changed,
            "created_at": utc_now(),
        }
        write_json(episode_root / "episode.json", episode)
        self.index.upsert_episode(run_id, episode, episode_root)
        return episode

    class _ActiveCounter:
        def __init__(self, engine: RunEngine) -> None:
            self.engine = engine

        async def __aenter__(self) -> None:
            async with self.engine._active_lock:
                self.engine._active += 1
                self.engine._max_active = max(self.engine._max_active, self.engine._active)

        async def __aexit__(self, *_args: object) -> None:
            async with self.engine._active_lock:
                self.engine._active -= 1

    def _count_active(self) -> _ActiveCounter:
        return self._ActiveCounter(self)


def remove_incomplete_workspace(path: Path) -> None:
    """Explicit repair helper; callers must resolve the exact episode path first."""
    if path.name != "workspace" or "episodes" not in path.parts:
        raise ValueError("refusing to remove a non-episode workspace")
    shutil.rmtree(path)
