from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from llm_status_machine.version import SCHEMA_VERSION


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StatePolicy(StrEnum):
    INDEPENDENT = "independent"
    CARRY_FORWARD = "carry_forward"
    BRANCH = "branch"


class Design(StrEnum):
    FULL_FACTORIAL = "full_factorial"
    MATCHED_PAIR = "matched_pair"
    BLOCK = "block"


class PromptRevision(FrozenModel):
    id: str
    body: str
    variables: dict[str, str | int | float | bool] = Field(default_factory=dict)
    parent_revision: str | None = None
    transform: str | None = None
    transform_version: str | None = None
    seed: int | None = None

    @field_validator("body")
    @classmethod
    def body_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt body must not be empty")
        return value


class RuntimeBuild(FrozenModel):
    provider: Literal["managed", "oci", "unmanaged", "simulator"] = "simulator"
    surface: str = "simulator"
    requested: str = "builtin"
    version: str = "builtin"
    executable: str
    sha256: str
    platform: str
    version_output: str
    reproducible: bool = True
    source_url: str | None = None
    oci_digest: str | None = None

    @field_validator("executable")
    @classmethod
    def executable_is_absolute(cls, value: str) -> str:
        if not Path(value).is_absolute():
            raise ValueError("runtime executable must be an absolute path")
        return value


class ModelEndpoint(FrozenModel):
    model_id: str = "simulator"
    provider: str = "local"
    base_url: str | None = None
    credential_ref: str | None = None
    server_metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionProfile(FrozenModel):
    name: str = "workspace_native"
    config_mode: Literal["hermetic", "workspace_native", "ambient"] = "workspace_native"
    research_mode: Literal["ecological", "controlled"] = "ecological"
    timeout_seconds: float = Field(default=120.0, gt=0)
    terminate_grace_seconds: float = Field(default=3.0, ge=0)
    reasoning_effort: str | None = None
    network: Literal["inherit", "disabled"] = "inherit"
    permissions: Literal["read-only", "workspace-write", "danger-full-access"] = "workspace-write"
    ephemeral: bool = False
    env_allowlist: list[str] = Field(default_factory=list)
    custom_argv: list[str] = Field(default_factory=list)
    prompt_transport: Literal["argument", "stdin", "file"] = "argument"
    success_exit_codes: list[int] = Field(default_factory=lambda: [0])
    decoder: Literal["text", "jsonl"] = "jsonl"

    @field_validator("custom_argv")
    @classmethod
    def no_shell_tokens(cls, value: list[str]) -> list[str]:
        forbidden = {"sh", "bash", "zsh", "cmd", "powershell", "pwsh"}
        if value and Path(value[0]).name.lower() in forbidden:
            raise ValueError("custom command cannot invoke a shell")
        return value


class WorkspaceFixture(FrozenModel):
    path: str
    baseline_sha256: str = ""
    excludes: list[str] = Field(
        default_factory=lambda: [
            ".git",
            ".lsm",
            ".bensz-api",
            ".venv",
            "node_modules",
            "__pycache__",
        ]
    )

    @field_validator("path")
    @classmethod
    def path_is_absolute(cls, value: str) -> str:
        if not Path(value).is_absolute():
            raise ValueError("workspace path must be absolute")
        return value


class StudySpec(FrozenModel):
    schema_version: int = SCHEMA_VERSION
    name: str
    seed: int = 0
    repeats: int = Field(default=1, ge=1)
    concurrency: int = Field(default=1, ge=1)
    state_policy: StatePolicy = StatePolicy.INDEPENDENT
    design: Design = Design.FULL_FACTORIAL
    prompts: list[PromptRevision]
    factors: dict[str, list[str | int | float | bool]] = Field(default_factory=dict)
    blocks: list[dict[str, str | int | float | bool]] = Field(default_factory=list)
    workspace: WorkspaceFixture
    runtime: RuntimeBuild
    endpoint: ModelEndpoint = Field(default_factory=ModelEndpoint)
    profile: ExecutionProfile = Field(default_factory=ExecutionProfile)

    @model_validator(mode="after")
    def validate_semantics(self) -> StudySpec:
        if not self.prompts:
            raise ValueError("study must contain at least one prompt")
        if self.state_policy == StatePolicy.CARRY_FORWARD and self.concurrency != 1:
            raise ValueError("carry_forward requires concurrency=1")
        if self.design == Design.MATCHED_PAIR and len(self.prompts) != 2:
            raise ValueError("matched_pair requires exactly two prompts")
        if self.design == Design.BLOCK and not self.blocks:
            raise ValueError("block design requires blocks")
        return self


class Trial(FrozenModel):
    schema_version: int = SCHEMA_VERSION
    id: str
    ordinal: int
    repetition: int
    prompt: PromptRevision
    actual_prompt: str
    prompt_sha256: str
    condition: dict[str, str | int | float | bool]
    block: dict[str, str | int | float | bool] = Field(default_factory=dict)
    workspace: WorkspaceFixture
    runtime: RuntimeBuild
    endpoint: ModelEndpoint
    profile: ExecutionProfile
    parent_trial_id: str | None = None


class TrialPlan(FrozenModel):
    schema_version: int = SCHEMA_VERSION
    id: str
    study_name: str
    seed: int
    concurrency: int
    state_policy: StatePolicy
    design: Design
    spec_sha256: str
    trials: list[Trial]


class Outcome(FrozenModel):
    status: Literal["completed", "failed", "cancelled", "timed_out"]
    detail: str | None = None


class AttemptOutcomes(FrozenModel):
    process: Outcome
    protocol: Outcome
    capture: Outcome
    workspace: Outcome

    @property
    def completed(self) -> bool:
        return all(
            outcome.status == "completed"
            for outcome in (self.process, self.protocol, self.capture, self.workspace)
        )
