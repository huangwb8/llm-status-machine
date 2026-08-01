from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from conftest import make_study

from llm_status_machine.domain.models import PromptRevision, StatePolicy
from llm_status_machine.evaluation.scorer import score_episode
from llm_status_machine.execution.runner import RunEngine
from llm_status_machine.recording.bundle import validate_seal
from llm_status_machine.study.compiler import compile_study
from llm_status_machine.utils import read_json, sha256_file
from llm_status_machine.workspaces.backend import WorkspaceBackend, build_manifest


async def execute(spec, data_root: Path):
    engine = RunEngine(data_root)
    try:
        run = await engine.run(compile_study(spec))
    finally:
        engine.close()
    return run


async def test_core_three_episode_carry_forward_smoke(source_workspace: Path, tmp_path: Path) -> None:
    before = build_manifest(source_workspace, {".git"})["sha256"]
    spec = make_study(
        source_workspace,
        repeats=3,
        state_policy=StatePolicy.CARRY_FORWARD,
        prompts=[PromptRevision(id="poem", body="请以“新中国的美人”为题写一首七言绝句。")],
    )
    data_root = tmp_path / "data"
    run = await execute(spec, data_root)
    assert run["status"] == "completed"
    assert len(run["episodes"]) == 3
    assert build_manifest(source_workspace, {".git"})["sha256"] == before
    run_root = data_root / "runs" / run["id"]
    previous_workspace = None
    for episode_id in run["episodes"]:
        episode_root = run_root / "episodes" / episode_id
        episode = read_json(episode_root / "episode.json")
        bundle = Path(episode["bundle"])
        assert episode["status"] == "completed"
        assert validate_seal(bundle) == (True, [])
        required = {
            "prompt.md",
            "stdout.raw",
            "stderr.raw",
            "transcript.jsonl",
            "metadata.json",
            "artifacts.json",
            "workspace.initial.json",
            "workspace.final.json",
            "changed-files.json",
            "diff.patch",
        }
        assert required <= {path.name for path in bundle.iterdir()}
        assert (bundle / "artifacts" / "behavior-summary.md").exists()
        assert "llm-simulator-notes.md" in (bundle / "diff.patch").read_text(encoding="utf-8")
        metadata = read_json(bundle / "metadata.json")
        if previous_workspace:
            assert metadata["source_workspace"] == previous_workspace
        previous_workspace = episode["workspace"]


async def test_independent_episodes_are_isolated(source_workspace: Path, tmp_path: Path) -> None:
    spec = make_study(source_workspace, repeats=3, concurrency=3, state_policy=StatePolicy.INDEPENDENT)
    data_root = tmp_path / "data"
    run = await execute(spec, data_root)
    assert run["status"] == "completed"
    run_root = data_root / "runs" / run["id"]
    contents = []
    for episode_id in run["episodes"]:
        notes = run_root / "episodes" / episode_id / "workspace" / "llm-simulator-notes.md"
        contents.append(notes.read_text(encoding="utf-8"))
    assert all(content.count("episode ") == 1 for content in contents)
    assert run["max_active_attempts"] <= 3


async def test_failure_and_parse_error_keep_sealed_raw_evidence(
    source_workspace: Path, tmp_path: Path
) -> None:
    spec = make_study(
        source_workspace,
        prompts=[PromptRevision(id="bad", body="[SIMULATOR_INVALID_JSON] [SIMULATOR_FAIL]")],
    )
    data_root = tmp_path / "data"
    run = await execute(spec, data_root)
    assert run["status"] == "failed"
    episode_path = next((data_root / "runs" / run["id"] / "episodes").iterdir())
    episode = read_json(episode_path / "episode.json")
    bundle = Path(episode["bundle"])
    assert validate_seal(bundle) == (True, [])
    assert b"\xff" in (bundle / "stdout.raw").read_bytes()
    assert "protocol.parse_error" in (bundle / "transcript.jsonl").read_text(encoding="utf-8")
    assert read_json(bundle / "outcomes.json")["process"]["status"] == "failed"


async def test_partial_large_and_unknown_events_keep_monotonic_records(
    source_workspace: Path, tmp_path: Path
) -> None:
    prompt = "[SIMULATOR_PARTIAL_JSON] [SIMULATOR_LARGE_JSON] [SIMULATOR_UNKNOWN_EVENT]"
    data_root = tmp_path / "data"
    run = await execute(
        make_study(source_workspace, prompts=[PromptRevision(id="streams", body=prompt)]), data_root
    )
    episode_path = next((data_root / "runs" / run["id"] / "episodes").iterdir())
    bundle = Path(read_json(episode_path / "episode.json")["bundle"])
    records = [json.loads(line) for line in (bundle / "transcript.jsonl").read_text().splitlines()]
    arrivals = [record["arrival_seq"] for record in records]
    events = [record for record in records if record["record_type"] == "event"]
    assert arrivals == list(range(1, len(arrivals) + 1))
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert any(event["kind"] == "vendor.unknown" for event in events)
    assert len((bundle / "stdout.raw").read_bytes()) > 200_000


async def test_timeout_seals_workspace_and_kills_process_group(
    source_workspace: Path, tmp_path: Path
) -> None:
    spec = make_study(
        source_workspace,
        prompts=[PromptRevision(id="timeout", body="[SIMULATOR_TIMEOUT]")],
        profile={"timeout_seconds": 0.1, "terminate_grace_seconds": 0.1},
    )
    data_root = tmp_path / "data"
    run = await execute(spec, data_root)
    episode_path = next((data_root / "runs" / run["id"] / "episodes").iterdir())
    episode = read_json(episode_path / "episode.json")
    bundle = Path(episode["bundle"])
    assert read_json(bundle / "outcomes.json")["process"]["status"] == "timed_out"
    assert validate_seal(bundle) == (True, [])


async def test_timeout_terminates_descendant_process(source_workspace: Path, tmp_path: Path) -> None:
    spec = make_study(
        source_workspace,
        prompts=[PromptRevision(id="child-timeout", body="[SIMULATOR_CHILD_TIMEOUT]")],
        profile={"timeout_seconds": 0.3, "terminate_grace_seconds": 0.1},
    )
    data_root = tmp_path / "data"
    run = await execute(spec, data_root)
    episode_path = next((data_root / "runs" / run["id"] / "episodes").iterdir())
    bundle = Path(read_json(episode_path / "episode.json")["bundle"])
    pid = int((bundle / "artifacts" / "child.pid").read_text())
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError(f"descendant process still alive: {pid}")


async def test_rescoring_does_not_modify_raw_bundle(source_workspace: Path, tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    run = await execute(make_study(source_workspace), data_root)
    episode_path = next((data_root / "runs" / run["id"] / "episodes").iterdir())
    bundle = Path(read_json(episode_path / "episode.json")["bundle"])
    before = sha256_file(bundle / "seal.json")
    score = score_episode(episode_path)
    assert score["passed"] is True
    assert sha256_file(bundle / "seal.json") == before
    assert score_episode(episode_path)["id"] == score["id"]


def test_workspace_rejects_escaping_symlink(source_workspace: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("protected", encoding="utf-8")
    (source_workspace / "escape").symlink_to(outside)
    backend = WorkspaceBackend([])
    with pytest.raises(ValueError, match="symlink"):
        backend.materialize(source_workspace, tmp_path / "copy")


def test_git_rename_records_original_path(source_workspace: Path, tmp_path: Path) -> None:
    (source_workspace / "old.txt").write_text("same content", encoding="utf-8")
    backend = WorkspaceBackend([])
    workspace = tmp_path / "copy"
    backend.materialize(source_workspace, workspace)
    (workspace / "old.txt").rename(workspace / "new.txt")
    _, changed, _ = backend.capture_final(workspace)
    rename = next(item for item in changed if "R" in item["status"])
    assert rename["path"] == "new.txt"
    assert rename["original_path"] == "old.txt"
