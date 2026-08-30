from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from llm_status_machine.cli.app import app
from llm_status_machine.instances import (
    STANDARD_COMPONENTS,
    InstanceContractError,
    scaffold_instance,
    validate_instance,
)
from llm_status_machine.recording.bundle import resolve_bundle_path, validate_seal
from llm_status_machine.utils import read_json

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
INSTANCE_NAMES = (
    "prior-washout-evidence-gating-study",
    "subagent-count-quality-study",
    "validate-md-ref-kernel-study",
)


@pytest.mark.parametrize("name", INSTANCE_NAMES)
def test_examples_follow_the_standard_instance_contract(name: str) -> None:
    report = validate_instance(EXAMPLES / name)

    assert report.valid is True
    assert report.errors == ()
    assert set(report.manifest.components) >= set(STANDARD_COMPONENTS)
    assert report.manifest.smoke.expected_episodes == 1
    assert report.paths["smoke"].is_file()


def test_instance_contract_rejects_paths_outside_the_package(tmp_path: Path) -> None:
    package = tmp_path / "example"
    package.mkdir()
    (package / "README.md").write_text("# Example\n", encoding="utf-8")
    for component in STANDARD_COMPONENTS:
        (package / component).mkdir()
    (package / "scripts/smoke.py").write_text("# smoke\n", encoding="utf-8")
    (package / "lsm.yml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "id": "example",
                "kind": "integration",
                "study": {"mode": "embedded", "entrypoint": "../worker.py"},
                "components": {name: name for name in STANDARD_COMPONENTS},
                "smoke": {"script": "scripts/smoke.py", "expected_episodes": 1},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(InstanceContractError, match="must stay within the instance root"):
        validate_instance(package)


def test_scaffold_creates_a_valid_standard_instance(tmp_path: Path) -> None:
    package = tmp_path / "new-study"

    report = scaffold_instance(package, instance_id="new-study", kind="study")

    assert report.valid is True
    assert (package / "lsm.yml").is_file()
    assert (package / "README.md").is_file()
    assert (package / "study.yml").is_file()
    assert all((package / name).is_dir() for name in STANDARD_COMPONENTS)
    assert all((package / name / "README.md").is_file() for name in STANDARD_COMPONENTS)
    assert (package / "scripts/smoke.py").is_file()


def test_scaffold_does_not_overwrite_an_existing_directory(tmp_path: Path) -> None:
    package = tmp_path / "existing"
    package.mkdir()

    with pytest.raises(FileExistsError, match="already exists"):
        scaffold_instance(package, instance_id="existing", kind="study")


def test_scaffold_rejects_a_mismatched_id_without_creating_files(tmp_path: Path) -> None:
    package = tmp_path / "directory-name"

    with pytest.raises(InstanceContractError, match="must match the directory name"):
        scaffold_instance(package, instance_id="different-name", kind="study")

    assert package.exists() is False


def test_example_cli_can_scaffold_and_validate_an_instance(tmp_path: Path) -> None:
    runner = CliRunner()
    package = tmp_path / "cli-study"

    created = runner.invoke(
        app,
        ["example", "init", str(package), "--id", "cli-study", "--json"],
    )
    validated = runner.invoke(app, ["example", "validate", str(package), "--json"])

    assert created.exit_code == 0, created.output
    assert validated.exit_code == 0, validated.output
    assert '"valid": true' in validated.output


def test_scaffold_smoke_runs_exactly_one_sealed_episode(tmp_path: Path) -> None:
    package = tmp_path / "generated-study"
    report = scaffold_instance(package, instance_id="generated-study", kind="study")
    output = tmp_path / "smoke-output"

    subprocess.run(
        [sys.executable, str(report.paths["smoke"]), "--root", str(output)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )

    episode_paths = list((output / "data/runs").glob("*/episodes/*/episode.json"))
    assert len(episode_paths) == 1
    episode = read_json(episode_paths[0])
    seal_valid, seal_errors = validate_seal(resolve_bundle_path(episode_paths[0].parent, episode))
    assert episode["status"] == "completed"
    assert seal_valid is True, seal_errors


@pytest.mark.parametrize("name", INSTANCE_NAMES)
def test_example_smoke_runs_exactly_one_sealed_episode(name: str, tmp_path: Path) -> None:
    report = validate_instance(EXAMPLES / name)
    output = tmp_path / name

    subprocess.run(
        [sys.executable, str(report.paths["smoke"]), "--root", str(output)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )

    run_roots = list((output / "data/runs").iterdir())
    assert len(run_roots) == 1
    episode_roots = list((run_roots[0] / "episodes").iterdir())
    assert len(episode_roots) == 1
    episode = read_json(episode_roots[0] / "episode.json")
    seal_valid, seal_errors = validate_seal(resolve_bundle_path(episode_roots[0], episode))
    assert episode["status"] == "completed"
    assert seal_valid is True, seal_errors
