from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from llm_status_machine.domain.models import ExecutionProfile, PromptRevision, StudySpec, WorkspaceFixture
from llm_status_machine.execution.runner import RunEngine
from llm_status_machine.recording.bundle import validate_seal
from llm_status_machine.runtimes.providers import simulator_runtime
from llm_status_machine.study.compiler import compile_study
from llm_status_machine.utils import read_json

SCENARIOS = {
    "normal-framing-and-unknown": {
        "prompt": "[SIMULATOR_PARTIAL_JSON] [SIMULATOR_UNKNOWN_EVENT]",
        "timeout": 5.0,
        "expected": "completed",
        "raw_marker": b'framing"}',
    },
    "invalid-utf8-and-nonzero": {
        "prompt": "[SIMULATOR_INVALID_JSON] [SIMULATOR_FAIL]",
        "timeout": 5.0,
        "expected": "failed",
        "raw_marker": b"\xff",
    },
    "large-streams": {
        "prompt": "[SIMULATOR_LARGE_JSON]",
        "timeout": 5.0,
        "expected": "completed",
        "raw_marker": b"x" * 1024,
    },
    "timeout": {
        "prompt": "[SIMULATOR_TIMEOUT]",
        "timeout": 0.15,
        "expected": "timed_out",
        "raw_marker": b'"type": "started"',
    },
}


async def qualify(root: Path) -> dict[str, object]:
    if root.exists():
        raise FileExistsError(root)
    root.mkdir(parents=True)
    results = []
    for name, scenario in SCENARIOS.items():
        source = root / name / "source"
        source.mkdir(parents=True)
        (source / "README.md").write_text(f"# Recorder qualification: {name}\n", encoding="utf-8")
        spec = StudySpec(
            name=f"recorder-{name}",
            prompts=[PromptRevision(id=name, body=str(scenario["prompt"]))],
            workspace=WorkspaceFixture(path=str(source.resolve())),
            runtime=simulator_runtime(),
            profile=ExecutionProfile(
                timeout_seconds=float(scenario["timeout"]), terminate_grace_seconds=0.1
            ),
        )
        data_root = root / name / "data"
        engine = RunEngine(data_root)
        try:
            run = await engine.run(compile_study(spec))
        finally:
            engine.close()
        episode_root = next((data_root / "runs" / run["id"] / "episodes").iterdir())
        episode = read_json(episode_root / "episode.json")
        bundle = Path(episode["bundle"])
        valid, errors = validate_seal(bundle)
        outcomes = read_json(bundle / "outcomes.json")
        expected = str(scenario["expected"])
        observed = outcomes["process"]["status"] if expected == "timed_out" else episode["status"]
        if not valid or observed != expected or bytes(scenario["raw_marker"]) not in (bundle / "stdout.raw").read_bytes():
            raise ValueError(f"recorder qualification failed: {name}: {errors}, {observed}")
        results.append(
            {
                "scenario": name,
                "episode_status": episode["status"],
                "process_status": outcomes["process"]["status"],
                "protocol_status": outcomes["protocol"]["status"],
                "seal_valid": valid,
                "raw_bytes": (bundle / "stdout.raw").stat().st_size,
            }
        )
    return {"status": "completed", "scenario_count": len(results), "scenarios": results}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(qualify(args.root.resolve())), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

