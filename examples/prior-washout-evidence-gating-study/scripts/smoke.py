from __future__ import annotations

import argparse
import asyncio
import json
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parents[1]
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
from llm_status_machine.recording.bundle import resolve_bundle_path, validate_seal
from llm_status_machine.runtimes.providers import lock_runtime
from llm_status_machine.study.compiler import compile_study, write_plan
from llm_status_machine.utils import read_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one deterministic LSM episode for this instance.")
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    output = args.root.resolve()
    if output.exists():
        raise FileExistsError(f"smoke root already exists: {output}")
    output.mkdir(parents=True)

    runtime = lock_runtime(
        surface="custom_command",
        executable=Path(sys.executable),
        requested_version=platform.python_version(),
    )
    prompt = (ROOT / "prompts/neutral-open.md").read_text(encoding="utf-8")
    spec = StudySpec(
        name="prior-washout-evidence-gating-smoke",
        repeats=1,
        concurrency=1,
        state_policy=StatePolicy.INDEPENDENT,
        prompts=[PromptRevision(id="neutral-open", body=prompt)],
        workspace=WorkspaceFixture(path=str((ROOT / "fixture").resolve())),
        runtime=runtime,
        endpoint=ModelEndpoint(provider="local", model_id="deterministic-qualification"),
        profile=ExecutionProfile(
            name="one-episode-qualification",
            config_mode="hermetic",
            research_mode="controlled",
            timeout_seconds=30,
            permissions="workspace-write",
            custom_argv=[
                runtime.executable,
                "{workspace}/tools/qualification_runner.py",
                "--prompt-file",
                "{prompt_file}",
                "--artifacts-dir",
                "{artifacts_dir}",
            ],
            prompt_transport="file",
            decoder="jsonl",
        ),
    )
    plan = compile_study(spec)
    write_plan(plan, output / "plan.jsonl")
    engine = RunEngine(output / "data")
    try:
        run = asyncio.run(engine.run(plan))
    finally:
        engine.close()
    episode_root = output / "data/runs" / run["id"] / "episodes" / run["episodes"][0]
    episode = read_json(episode_root / "episode.json")
    seal_valid, seal_errors = validate_seal(resolve_bundle_path(episode_root, episode))
    if run["status"] != "completed" or len(run["episodes"]) != 1 or not seal_valid:
        raise RuntimeError(f"instance smoke failed: run={run}, seal_errors={seal_errors}")
    print(json.dumps({"status": "completed", "run_id": run["id"], "episodes": 1, "seal_valid": True}))


if __name__ == "__main__":
    main()
