"""Standard, discoverable source packages for LSM studies and integrations."""

from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from llm_status_machine.domain.models import (
    ExecutionProfile,
    PromptRevision,
    StatePolicy,
    StudySpec,
    WorkspaceFixture,
)
from llm_status_machine.runtimes.providers import simulator_runtime
from llm_status_machine.version import INSTANCE_SCHEMA_VERSION

STANDARD_COMPONENTS = (
    "prompts",
    "fixture",
    "harness",
    "oracle_tests",
    "scripts",
    "results",
)
OPTIONAL_COMPONENTS = ("analysis", "assets", "docs")
KNOWN_COMPONENTS = frozenset((*STANDARD_COMPONENTS, *OPTIONAL_COMPONENTS))
COMPONENT_DESCRIPTIONS = {
    "prompts": "Prompt revisions and templates.",
    "fixture": "Source workspace copied for each episode.",
    "harness": "Instance-specific harness and runtime support.",
    "oracle_tests": "Scorers, hidden tests, or the declared absence of a business oracle.",
    "scripts": "Preparation, run, verification, and one-episode smoke entrypoints.",
    "results": "Sanitized, reviewable result summaries; never local RawBundles.",
}


class InstanceContractError(ValueError):
    """Raised when an instance package violates the public layout contract."""


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StudyDeclaration(ContractModel):
    mode: Literal["static", "generated", "embedded"]
    path: str | None = None
    generator: str | None = None
    entrypoint: str | None = None

    @model_validator(mode="after")
    def required_source(self) -> StudyDeclaration:
        required = {
            "static": ("path",),
            "generated": ("path", "generator"),
            "embedded": ("entrypoint",),
        }[self.mode]
        missing = [name for name in required if not getattr(self, name)]
        if missing:
            raise ValueError(f"study mode {self.mode} requires: {', '.join(missing)}")
        return self


class SmokeDeclaration(ContractModel):
    script: str
    expected_episodes: Literal[1] = 1


class InstanceManifest(ContractModel):
    schema_version: int = INSTANCE_SCHEMA_VERSION
    id: str = Field(min_length=1, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    kind: Literal["study", "integration", "benchmark"]
    study: StudyDeclaration
    components: dict[str, str]
    smoke: SmokeDeclaration
    external_dependencies: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def standard_components(self) -> InstanceManifest:
        missing = set(STANDARD_COMPONENTS) - set(self.components)
        unknown = set(self.components) - KNOWN_COMPONENTS
        if missing:
            raise ValueError(f"missing standard components: {sorted(missing)}")
        if unknown:
            raise ValueError(f"unknown components: {sorted(unknown)}")
        noncanonical = {
            name: path for name, path in self.components.items() if path != name
        }
        if noncanonical:
            raise ValueError(f"component paths must use canonical names: {noncanonical}")
        return self


@dataclass(frozen=True)
class InstanceValidationReport:
    root: Path
    manifest: InstanceManifest
    paths: dict[str, Path]
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.errors


def _contained_path(root: Path, value: str, *, role: str) -> Path:
    relative = Path(value)
    if relative.is_absolute():
        raise InstanceContractError(f"{role} must be relative to the instance root: {value}")
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise InstanceContractError(f"{role} must stay within the instance root: {value}")
    return candidate


def load_instance(root: Path) -> InstanceManifest:
    root = root.expanduser().resolve()
    manifest_path = root / "lsm.yml"
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise TypeError("lsm.yml must contain a mapping")
        manifest = InstanceManifest.model_validate(raw)
    except (OSError, TypeError, ValueError) as error:
        raise InstanceContractError(f"invalid instance manifest {manifest_path}: {error}") from error
    if manifest.schema_version != INSTANCE_SCHEMA_VERSION:
        raise InstanceContractError(
            f"unsupported instance schema_version: {manifest.schema_version}"
        )
    return manifest


def validate_instance(root: Path) -> InstanceValidationReport:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise InstanceContractError(f"instance root is not a directory: {root}")
    manifest = load_instance(root)
    if manifest.id != root.name:
        raise InstanceContractError(
            f"instance id must match the directory name: {manifest.id!r} != {root.name!r}"
        )
    readme = root / "README.md"
    if not readme.is_file():
        raise InstanceContractError(f"missing required instance README: {readme}")

    paths: dict[str, Path] = {"manifest": root / "lsm.yml", "readme": readme}
    for name, value in manifest.components.items():
        component = _contained_path(root, value, role=f"component {name}")
        if not component.is_dir():
            raise InstanceContractError(f"component {name} is not a directory: {component}")
        paths[name] = component

    study_values = {
        "study": manifest.study.path,
        "study_generator": manifest.study.generator,
        "study_entrypoint": manifest.study.entrypoint,
    }
    for role, value in study_values.items():
        if value is None:
            continue
        path = _contained_path(root, value, role=role)
        if (role != "study" or manifest.study.mode == "static") and not path.is_file():
            raise InstanceContractError(f"{role} is not a file: {path}")
        paths[role] = path

    smoke = _contained_path(root, manifest.smoke.script, role="smoke script")
    if not smoke.is_file():
        raise InstanceContractError(f"smoke script is not a file: {smoke}")
    if not smoke.is_relative_to(paths["scripts"]):
        raise InstanceContractError("smoke script must be inside the scripts component")
    paths["smoke"] = smoke

    warnings = tuple(
        f"local runtime output should not be part of the source package: {name}"
        for name in (".lsm", "tmp", "exports")
        if (root / name).exists()
    )
    return InstanceValidationReport(root=root, manifest=manifest, paths=paths, warnings=warnings)


def scaffold_instance(
    root: Path,
    *,
    instance_id: str,
    kind: Literal["study", "integration", "benchmark"] = "study",
) -> InstanceValidationReport:
    root = root.expanduser().resolve()
    if root.exists():
        raise FileExistsError(f"instance root already exists: {root}")
    if instance_id != root.name:
        raise InstanceContractError(
            f"instance id must match the directory name: {instance_id!r} != {root.name!r}"
        )
    root.mkdir(parents=True)
    for component in STANDARD_COMPONENTS:
        directory = root / component
        directory.mkdir()
        title = component.replace("_", " ").title()
        (directory / "README.md").write_text(
            f"# {title}\n\n{COMPONENT_DESCRIPTIONS[component]}\n", encoding="utf-8"
        )

    workspace = root / "fixture"
    spec = StudySpec(
        name=instance_id,
        repeats=1,
        concurrency=1,
        state_policy=StatePolicy.INDEPENDENT,
        prompts=[PromptRevision(id="example", body="Complete the requested task.")],
        workspace=WorkspaceFixture(path=str(workspace)),
        runtime=simulator_runtime(),
        profile=ExecutionProfile(timeout_seconds=120),
    )
    (root / "study.yml").write_text(
        yaml.safe_dump(spec.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (root / "README.md").write_text(f"# {instance_id}\n", encoding="utf-8")
    smoke = root / "scripts" / "smoke.py"
    smoke.write_text(
        """from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parents[1]
if str(REPOSITORY / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY / "src"))

from llm_status_machine.execution.runner import RunEngine
from llm_status_machine.recording.bundle import resolve_bundle_path, validate_seal
from llm_status_machine.study.compiler import compile_study, load_study
from llm_status_machine.utils import read_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one sealed episode for this instance.")
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    output = args.root.resolve()
    if output.exists():
        raise FileExistsError(f"smoke root already exists: {output}")
    output.mkdir(parents=True)
    plan = compile_study(load_study(ROOT / "study.yml"))
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
    print(run["id"])


if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    smoke.chmod(smoke.stat().st_mode | stat.S_IXUSR)
    manifest = {
        "schema_version": INSTANCE_SCHEMA_VERSION,
        "id": instance_id,
        "kind": kind,
        "study": {"mode": "static", "path": "study.yml"},
        "components": {name: name for name in STANDARD_COMPONENTS},
        "smoke": {"script": "scripts/smoke.py", "expected_episodes": 1},
        "external_dependencies": [],
    }
    (root / "lsm.yml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return validate_instance(root)


__all__ = [
    "INSTANCE_SCHEMA_VERSION",
    "OPTIONAL_COMPONENTS",
    "STANDARD_COMPONENTS",
    "InstanceContractError",
    "InstanceManifest",
    "InstanceValidationReport",
    "load_instance",
    "scaffold_instance",
    "validate_instance",
]
