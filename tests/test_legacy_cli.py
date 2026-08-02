from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from llm_status_machine.cli.app import app
from llm_status_machine.legacy.importer import import_legacy, inventory_legacy, validate_legacy
from llm_status_machine.recording.bundle import validate_seal
from llm_status_machine.utils import read_json


def create_legacy_episode(path: Path) -> None:
    path.mkdir(parents=True)
    for name, content in {
        "prompt.txt": "Do it",
        "stdout.txt": "ok",
        "stderr.txt": "",
        "transcript.ndjson": "{}\n",
        "diff.patch": "",
        "metadata.json": "{}",
    }.items():
        (path / name).write_text(content, encoding="utf-8")


def test_legacy_two_layouts_inventory_validate_and_import(tmp_path: Path) -> None:
    root = tmp_path / "legacy"
    (root / "runs").mkdir(parents=True)
    (root / "store.json").write_text(json.dumps({"runs": [{"id": "a"}]}), encoding="utf-8")
    create_legacy_episode(root / "runs" / "run-a" / "session-a")
    create_legacy_episode(root / "runs" / "run-b" / "state-1")
    inventory = inventory_legacy(root)
    assert inventory["layouts"] == {"state": 1, "session": 1}
    assert validate_legacy(root)["valid"] is True
    report = import_legacy(root, tmp_path / "new-data")
    assert report["imported_episodes"] == 2
    for episode_path in Path(report["destination"]).glob("episodes/*/episode.json"):
        episode = read_json(episode_path)
        assert validate_seal(Path(episode["bundle"])) == (True, [])


def test_cli_black_box_help_and_doctor(tmp_path: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["--version"]).exit_code == 0
    result = runner.invoke(app, ["doctor", "--data-root", str(tmp_path / "data"), "--json"])
    assert result.exit_code == 0
    assert '"ok": true' in result.stdout.lower()
    legacy = tmp_path / "legacy"
    (legacy / "runs").mkdir(parents=True)
    inventory = runner.invoke(app, ["legacy", "inventory", str(legacy), "--json"])
    assert inventory.exit_code == 0
    assert '"disk_episode_count": 0' in inventory.stdout


def test_legacy_cli_requires_explicit_source_path() -> None:
    runner = CliRunner()
    for command in ("inventory", "validate", "import"):
        result = runner.invoke(app, ["legacy", command])
        assert result.exit_code == 2, result.output
