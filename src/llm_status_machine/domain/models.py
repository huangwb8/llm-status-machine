from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from llm_status_machine.version import STUDY_SCHEMA_VERSION, TRIAL_PLAN_SCHEMA_VERSION


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


class StudyMode(StrEnum):
    EXPLORATORY = "exploratory"
    CONFIRMATORY = "confirmatory"


class MetricType(StrEnum):
    CONTINUOUS = "continuous"
    BINARY = "binary"
    NOMINAL = "nominal"
    ORDINAL = "ordinal"


class PromptRevision(FrozenModel):
    id: str
    body: str
    variables: dict[str, str | int | float | bool] = Field(default_factory=dict)
    parent_revision: str | None = None
    transform: str | None = None
    transform_version: str | None = None
    seed: int | None = None

    @field_validator("id", "body")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be empty")
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


class DesignSpec(FrozenModel):
    randomization_algorithm: Literal["balanced-rotation"] = "balanced-rotation"
    randomization_version: str = "1"


class MetricSpec(FrozenModel):
    id: str
    type: MetricType
    lower_bound: float | None = None
    upper_bound: float | None = None
    direction: Literal["higher", "lower"]
    unit: str = "unitless"
    required: bool = True
    agreement_threshold: float | None = Field(default=None, ge=-1, le=1)

    @model_validator(mode="after")
    def valid_bounds(self) -> MetricSpec:
        if self.type == MetricType.BINARY:
            if self.lower_bound is None:
                object.__setattr__(self, "lower_bound", 0.0)
            if self.upper_bound is None:
                object.__setattr__(self, "upper_bound", 1.0)
        if (
            self.lower_bound is not None
            and self.upper_bound is not None
            and self.lower_bound >= self.upper_bound
        ):
            raise ValueError("metric lower_bound must be below upper_bound")
        return self


class PinnedFile(FrozenModel):
    path: str
    sha256: str = ""

    @field_validator("path")
    @classmethod
    def absolute_path(cls, value: str) -> str:
        if not Path(value).is_absolute():
            raise ValueError("pinned file path must be absolute")
        return value


class ScorerSpec(FrozenModel):
    id: str
    kind: Literal["execution_integrity", "command"] = "execution_integrity"
    version: str = "execution-integrity-v1"
    argv: list[str] = Field(default_factory=list)
    executable_sha256: str = ""
    rubric: PinnedFile | None = None
    support_files: list[PinnedFile] = Field(default_factory=list)
    include_prompt: bool = False
    repetitions: int = Field(default=1, ge=1)
    aggregation: Literal["mean", "median", "majority", "min", "max"] = "mean"
    timeout_seconds: float = Field(default=120.0, gt=0)
    metrics: list[MetricSpec]

    @model_validator(mode="after")
    def validate_scorer(self) -> ScorerSpec:
        if len({metric.id for metric in self.metrics}) != len(self.metrics):
            raise ValueError("metric ids must be unique within a scorer")
        if self.aggregation in {"mean", "median"} and any(
            metric.type == MetricType.NOMINAL for metric in self.metrics
        ):
            raise ValueError("numeric aggregation cannot be used with nominal metrics")
        if self.kind == "command":
            if not self.argv or not Path(self.argv[0]).is_absolute():
                raise ValueError("command scorer argv[0] must be an absolute executable")
            if Path(self.argv[0]).name.lower() in {"sh", "bash", "zsh", "cmd", "powershell", "pwsh"}:
                raise ValueError("command scorer cannot invoke a shell")
        return self


class EvaluationSpec(FrozenModel):
    scorers: list[ScorerSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_scorers(self) -> EvaluationSpec:
        if len({scorer.id for scorer in self.scorers}) != len(self.scorers):
            raise ValueError("scorer ids must be unique")
        return self


class OutcomeSpec(FrozenModel):
    id: str
    scorer_id: str
    metric_id: str
    role: Literal["primary", "secondary"] = "secondary"
    type: MetricType
    direction: Literal["higher", "lower"]
    lower_bound: float | None = None
    upper_bound: float | None = None
    failure_policy: Literal["value", "worst_case", "missing"]
    failure_value: float | None = None
    missing_policy: Literal["value", "worst_case", "descriptive", "error"]
    missing_value: float | None = None

    @model_validator(mode="after")
    def explicit_policy_values(self) -> OutcomeSpec:
        if self.failure_policy == "value" and self.failure_value is None:
            raise ValueError("failure_value is required for failure_policy=value")
        if self.missing_policy == "value" and self.missing_value is None:
            raise ValueError("missing_value is required for missing_policy=value")
        if self.failure_policy == "worst_case" or self.missing_policy == "worst_case":
            bound = self.lower_bound if self.direction == "higher" else self.upper_bound
            if bound is None:
                raise ValueError("worst_case policy requires the adverse metric bound")
        for name, value in (
            ("failure_value", self.failure_value),
            ("missing_value", self.missing_value),
        ):
            if value is None:
                continue
            if self.type == MetricType.BINARY and value not in {0.0, 1.0}:
                raise ValueError(f"{name} must be within metric bounds and binary")
            if self.lower_bound is not None and value < self.lower_bound:
                raise ValueError(f"{name} must be within metric bounds")
            if self.upper_bound is not None and value > self.upper_bound:
                raise ValueError(f"{name} must be within metric bounds")
        return self


class ContrastSpec(FrozenModel):
    id: str
    outcome_id: str
    treatment_arm: str
    control_arm: str
    estimand: Literal["mean_difference", "risk_difference"]
    family: str = "primary"

    @model_validator(mode="after")
    def distinct_arms(self) -> ContrastSpec:
        if self.treatment_arm == self.control_arm:
            raise ValueError("contrast treatment and control arms must differ")
        return self


class AnalysisSpec(FrozenModel):
    outcomes: list[OutcomeSpec] = Field(default_factory=list)
    contrasts: list[ContrastSpec] = Field(default_factory=list)
    alpha: float = Field(default=0.05, gt=0, lt=1)
    permutations: int = Field(default=10_000, ge=100)
    bootstrap_samples: int = Field(default=2_000, ge=100)
    seed: int = 0
    multiplicity: Literal["holm", "none"] = "holm"

    @model_validator(mode="after")
    def unique_ids(self) -> AnalysisSpec:
        if len({item.id for item in self.outcomes}) != len(self.outcomes):
            raise ValueError("outcome ids must be unique")
        if len({item.id for item in self.contrasts}) != len(self.contrasts):
            raise ValueError("contrast ids must be unique")
        return self


class StudySpec(FrozenModel):
    schema_version: int = STUDY_SCHEMA_VERSION
    name: str
    study_mode: StudyMode = StudyMode.EXPLORATORY
    seed: int = 0
    repeats: int = Field(default=1, ge=1)
    concurrency: int = Field(default=1, ge=1)
    state_policy: StatePolicy = StatePolicy.INDEPENDENT
    design: Design = Design.FULL_FACTORIAL
    design_spec: DesignSpec = Field(default_factory=DesignSpec)
    prompts: list[PromptRevision]
    factors: dict[str, list[str | int | float | bool]] = Field(default_factory=dict)
    blocks: list[dict[str, str | int | float | bool]] = Field(default_factory=list)
    workspace: WorkspaceFixture
    runtime: RuntimeBuild
    endpoint: ModelEndpoint = Field(default_factory=ModelEndpoint)
    profile: ExecutionProfile = Field(default_factory=ExecutionProfile)
    evaluation: EvaluationSpec = Field(default_factory=EvaluationSpec)
    analysis: AnalysisSpec = Field(default_factory=AnalysisSpec)
    migration_warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_semantics(self) -> StudySpec:
        if self.schema_version != STUDY_SCHEMA_VERSION:
            raise ValueError(f"unsupported study schema_version: {self.schema_version}")
        if not self.prompts:
            raise ValueError("study must contain at least one prompt")
        arm_ids = [prompt.id for prompt in self.prompts]
        if len(set(arm_ids)) != len(arm_ids):
            raise ValueError("prompt ids must be unique")
        if self.state_policy == StatePolicy.CARRY_FORWARD and self.concurrency != 1:
            raise ValueError("carry_forward requires concurrency=1")
        if self.design == Design.MATCHED_PAIR and len(self.prompts) != 2:
            raise ValueError("matched_pair requires exactly two prompts")
        if self.design == Design.BLOCK and not self.blocks:
            raise ValueError("block design requires blocks")
        if len({str(sorted(block.items())) for block in self.blocks}) != len(self.blocks):
            raise ValueError("blocks must be unique")
        for name, values in self.factors.items():
            if len({repr(value) for value in values}) != len(values):
                raise ValueError(f"factor values must be unique: {name}")
        scorer_metrics = {
            (scorer.id, metric.id): metric for scorer in self.evaluation.scorers for metric in scorer.metrics
        }
        outcomes = {outcome.id: outcome for outcome in self.analysis.outcomes}
        for outcome in outcomes.values():
            metric = scorer_metrics.get((outcome.scorer_id, outcome.metric_id))
            if metric is None:
                raise ValueError(f"outcome references unknown scorer metric: {outcome.id}")
            if (outcome.type, outcome.direction) != (metric.type, metric.direction):
                raise ValueError(f"outcome metric contract mismatch: {outcome.id}")
            if (outcome.lower_bound, outcome.upper_bound) != (
                metric.lower_bound,
                metric.upper_bound,
            ):
                raise ValueError(f"outcome metric bounds mismatch: {outcome.id}")
            if outcome.type not in {MetricType.CONTINUOUS, MetricType.BINARY}:
                raise ValueError(f"analysis outcomes must be continuous or binary: {outcome.id}")
        for contrast in self.analysis.contrasts:
            if contrast.outcome_id not in outcomes:
                raise ValueError(f"contrast references unknown outcome: {contrast.id}")
            if contrast.treatment_arm not in arm_ids or contrast.control_arm not in arm_ids:
                raise ValueError(f"contrast references unknown prompt arm: {contrast.id}")
            outcome = outcomes[contrast.outcome_id]
            expected_estimand = "risk_difference" if outcome.type == MetricType.BINARY else "mean_difference"
            if contrast.estimand != expected_estimand:
                raise ValueError(f"contrast estimand does not match outcome type: {contrast.id}")
        if self.study_mode == StudyMode.CONFIRMATORY:
            if self.state_policy != StatePolicy.INDEPENDENT:
                raise ValueError("confirmatory studies require state_policy=independent")
            if self.concurrency != 1:
                raise ValueError("confirmatory studies require concurrency=1")
            if not any(outcome.role == "primary" for outcome in outcomes.values()):
                raise ValueError("confirmatory studies require a primary outcome")
            if not self.analysis.contrasts:
                raise ValueError("confirmatory studies require a predeclared contrast")
            covered_outcomes = {contrast.outcome_id for contrast in self.analysis.contrasts}
            uncovered_primary = {
                outcome.id
                for outcome in self.analysis.outcomes
                if outcome.role == "primary" and outcome.id not in covered_outcomes
            }
            if uncovered_primary:
                raise ValueError(
                    "confirmatory primary outcomes require predeclared contrasts: "
                    f"{sorted(uncovered_primary)}"
                )
            if len(self.prompts) < 2:
                raise ValueError("confirmatory studies require at least two prompt arms")
            if self.repeats % len(self.prompts) != 0:
                raise ValueError("confirmatory repeats must balance every arm across sequence positions")
        return self


class RandomizationRecord(FrozenModel):
    algorithm: str
    version: str
    derived_seed: int
    draw: int


class Trial(FrozenModel):
    schema_version: int = TRIAL_PLAN_SCHEMA_VERSION
    id: str
    ordinal: int
    repetition: int
    prompt: PromptRevision
    actual_prompt: str
    prompt_sha256: str
    condition: dict[str, str | int | float | bool]
    block: dict[str, str | int | float | bool] = Field(default_factory=dict)
    arm_id: str = ""
    comparison_set_id: str = ""
    pair_id: str | None = None
    block_id: str | None = None
    sequence_position: int = 1
    dispatch_batch: int = 1
    randomization: RandomizationRecord | None = None
    workspace: WorkspaceFixture
    runtime: RuntimeBuild
    endpoint: ModelEndpoint
    profile: ExecutionProfile
    parent_trial_id: str | None = None

    @model_validator(mode="after")
    def valid_trial_contract(self) -> Trial:
        if self.schema_version != TRIAL_PLAN_SCHEMA_VERSION:
            raise ValueError(f"unsupported trial schema_version: {self.schema_version}")
        if self.arm_id and self.arm_id != self.prompt.id:
            raise ValueError("trial arm_id must match prompt revision id")
        return self


class TrialPlan(FrozenModel):
    schema_version: int = TRIAL_PLAN_SCHEMA_VERSION
    id: str
    study_name: str
    study_mode: StudyMode = StudyMode.EXPLORATORY
    seed: int
    concurrency: int
    state_policy: StatePolicy
    design: Design
    design_spec: DesignSpec = Field(default_factory=DesignSpec)
    spec_sha256: str
    evaluation: EvaluationSpec = Field(default_factory=EvaluationSpec)
    analysis: AnalysisSpec = Field(default_factory=AnalysisSpec)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    migration_warnings: list[str] = Field(default_factory=list)
    trials: list[Trial]

    @model_validator(mode="after")
    def supported_and_complete(self) -> TrialPlan:
        if self.schema_version != TRIAL_PLAN_SCHEMA_VERSION:
            raise ValueError(f"unsupported trial plan schema_version: {self.schema_version}")
        if len({trial.id for trial in self.trials}) != len(self.trials):
            raise ValueError("trial ids must be unique")
        if [trial.ordinal for trial in self.trials] != list(range(1, len(self.trials) + 1)):
            raise ValueError("trial ordinals must be contiguous")
        scorer_metrics = {
            (scorer.id, metric.id): metric for scorer in self.evaluation.scorers for metric in scorer.metrics
        }
        outcomes = {outcome.id: outcome for outcome in self.analysis.outcomes}
        if len(outcomes) != len(self.analysis.outcomes):
            raise ValueError("plan outcome ids must be unique")
        arms = {trial.arm_id for trial in self.trials}
        for outcome in outcomes.values():
            metric = scorer_metrics.get((outcome.scorer_id, outcome.metric_id))
            if metric is None:
                raise ValueError(f"plan outcome references unknown scorer metric: {outcome.id}")
            if (
                outcome.type,
                outcome.direction,
                outcome.lower_bound,
                outcome.upper_bound,
            ) != (
                metric.type,
                metric.direction,
                metric.lower_bound,
                metric.upper_bound,
            ):
                raise ValueError(f"plan outcome metric contract mismatch: {outcome.id}")
        for contrast in self.analysis.contrasts:
            if contrast.outcome_id not in outcomes:
                raise ValueError(f"plan contrast references unknown outcome: {contrast.id}")
            if contrast.treatment_arm not in arms or contrast.control_arm not in arms:
                raise ValueError(f"plan contrast references unknown arm: {contrast.id}")
            outcome = outcomes[contrast.outcome_id]
            expected_estimand = (
                "risk_difference" if outcome.type == MetricType.BINARY else "mean_difference"
            )
            if contrast.estimand != expected_estimand:
                raise ValueError(f"plan contrast estimand mismatch: {contrast.id}")
        if self.study_mode == StudyMode.CONFIRMATORY:
            if not self.diagnostics.get("confirmatory_valid", False):
                raise ValueError("confirmatory plan failed design diagnostics")
            if self.state_policy != StatePolicy.INDEPENDENT or self.concurrency != 1:
                raise ValueError("confirmatory plan requires independent state and concurrency=1")
            if not any(outcome.role == "primary" for outcome in self.analysis.outcomes):
                raise ValueError("confirmatory plan requires a primary outcome")
            if not self.analysis.contrasts:
                raise ValueError("confirmatory plan requires a contrast")
            covered_outcomes = {contrast.outcome_id for contrast in self.analysis.contrasts}
            uncovered_primary = {
                outcome.id
                for outcome in self.analysis.outcomes
                if outcome.role == "primary" and outcome.id not in covered_outcomes
            }
            if uncovered_primary:
                raise ValueError(
                    "confirmatory primary outcomes require predeclared contrasts: "
                    f"{sorted(uncovered_primary)}"
                )
            sets: dict[str, list[Trial]] = {}
            positions: dict[str, list[int]] = {}
            for trial in self.trials:
                if not trial.comparison_set_id or trial.randomization is None:
                    raise ValueError("confirmatory trial assignment provenance is incomplete")
                sets.setdefault(trial.comparison_set_id, []).append(trial)
                positions.setdefault(trial.arm_id, []).append(trial.sequence_position)
            if any(
                len(trials) != len(arms) or {trial.arm_id for trial in trials} != arms
                for trials in sets.values()
            ):
                raise ValueError("confirmatory comparison sets require exactly one trial per arm")
            if self.design == Design.MATCHED_PAIR and any(
                None in {trial.pair_id for trial in trials}
                or len({trial.pair_id for trial in trials}) != 1
                for trials in sets.values()
            ):
                raise ValueError("matched_pair comparison sets require one stable pair_id")
            if self.design == Design.BLOCK and any(trial.block_id is None for trial in self.trials):
                raise ValueError("block trials require block_id")
            expected_positions = set(range(1, len(arms) + 1))
            if any(
                set(value) != expected_positions
                or len({value.count(position) for position in expected_positions}) != 1
                for value in positions.values()
            ):
                raise ValueError("confirmatory arm sequence positions are incomplete")
        return self


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
