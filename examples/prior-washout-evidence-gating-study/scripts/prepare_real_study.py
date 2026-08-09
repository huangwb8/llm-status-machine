from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = "gpt-5.6-sol"
PILOT_SEED = 20260811
CONFIRMATORY_SEED = 20260812


def _load_prepare() -> ModuleType:
    path = ROOT / "scripts/prepare_study.py"
    spec = importlib.util.spec_from_file_location("prior_washout_prepare", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load qualification StudySpec generator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _analysis() -> dict[str, Any]:
    return {
        "outcomes": [
            {
                "id": "primary_ifo_auc",
                "scorer_id": "symbolic_oracle",
                "metric_id": "ifo_auc",
                "role": "primary",
                "type": "continuous",
                "direction": "lower",
                "lower_bound": 0,
                "upper_bound": 1,
                "failure_policy": "worst_case",
                "missing_policy": "error",
            },
            {
                "id": "secondary_structure_recovery",
                "scorer_id": "symbolic_oracle",
                "metric_id": "structure_recovery",
                "role": "secondary",
                "type": "continuous",
                "direction": "higher",
                "lower_bound": 0,
                "upper_bound": 1,
                "failure_policy": "worst_case",
                "missing_policy": "error",
            },
        ],
        "contrasts": [
            {
                "id": "false-open-v-neutral-open",
                "outcome_id": "primary_ifo_auc",
                "treatment_arm": "false-open",
                "control_arm": "neutral-open",
                "estimand": "mean_difference",
                "family": "primary",
            },
            {
                "id": "false-gated-v-false-open",
                "outcome_id": "primary_ifo_auc",
                "treatment_arm": "false-gated",
                "control_arm": "false-open",
                "estimand": "mean_difference",
                "family": "primary",
            },
            {
                "id": "false-purged-v-false-open",
                "outcome_id": "primary_ifo_auc",
                "treatment_arm": "false-purged",
                "control_arm": "false-open",
                "estimand": "mean_difference",
                "family": "primary",
            },
        ],
        "alpha": 0.05,
        "permutations": 2000,
        "bootstrap_samples": 1000,
        "seed": PILOT_SEED,
        "multiplicity": "holm",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--nested-runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("shakedown", "pilot", "confirmatory"), required=True)
    parser.add_argument("--repeats", type=int)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--stage-timeout", type=float, default=900)
    parser.add_argument("--seed", type=int)
    args = parser.parse_args()
    if "CODEX_HOME" not in os.environ:
        raise SystemExit("CODEX_HOME must reference an existing external Codex configuration")
    if args.stage_timeout <= 0:
        raise ValueError("stage timeout must be positive")
    runtime = json.loads(args.runtime.read_text(encoding="utf-8"))
    nested = json.loads(args.nested_runtime.read_text(encoding="utf-8"))
    if runtime.get("surface") != "custom_command":
        raise ValueError("real three-stage study requires a custom_command outer runtime")
    if nested.get("surface") != "codex_exec_cli":
        raise ValueError("nested runtime must use codex_exec_cli")

    prepare = _load_prepare()
    prompts = prepare.materialize_prompts()
    if args.mode == "shakedown":
        prompts = [item for item in prompts if item["id"] == "neutral-open"]
        repeats = 1
        study_mode = "exploratory"
    elif args.mode == "pilot":
        repeats = args.repeats or 5
        study_mode = "exploratory"
    else:
        repeats = args.repeats or 35
        if repeats % 5:
            raise ValueError("confirmatory repeats per arm must be a multiple of five")
        study_mode = "confirmatory"
    if repeats <= 0:
        raise ValueError("repeats must be positive")

    scorer = (ROOT / "oracle_tests/symbolic_oracle.py").resolve()
    hidden = (ROOT / "oracle_tests/hidden_tasks.json").resolve()
    evaluator = (ROOT / "fixture/tools/evaluate_candidate.py").resolve()
    phase_runner = (ROOT / "harness/phase_runner.py").resolve()
    python = str(Path(runtime["executable"]).absolute())
    analysis = _analysis()
    analysis["seed"] = args.seed or (CONFIRMATORY_SEED if args.mode == "confirmatory" else PILOT_SEED)
    if args.mode == "shakedown":
        analysis["outcomes"] = []
        analysis["contrasts"] = []
    study = {
        "schema_version": 2,
        "name": f"prior-washout-evidence-gating-real-{args.mode}",
        "study_mode": study_mode,
        "seed": args.seed or (CONFIRMATORY_SEED if args.mode == "confirmatory" else PILOT_SEED),
        "repeats": repeats,
        "concurrency": 1,
        "state_policy": "independent",
        "design": "full_factorial",
        "prompts": prompts,
        "workspace": {
            "path": str((ROOT / "fixture").resolve()),
            "excludes": [
                ".git",
                ".lsm",
                ".bensz-api",
                ".venv",
                "node_modules",
                "__pycache__",
                ".DS_Store",
                ".codex",
                "auth.json",
                "config.toml",
                ".env",
            ],
        },
        "runtime": runtime,
        "endpoint": {"provider": "openai", "model_id": args.model},
        "profile": {
            "name": f"three-stage-real-{args.mode}",
            "config_mode": "hermetic",
            "research_mode": "controlled",
            "timeout_seconds": args.stage_timeout * 3 + 120,
            "terminate_grace_seconds": 10,
            "reasoning_effort": args.reasoning_effort,
            "network": "inherit",
            "permissions": "workspace-write",
            "ephemeral": True,
            "env_allowlist": ["CODEX_HOME"],
            "custom_argv": [
                python,
                str(phase_runner),
                "--prompt-file",
                "{prompt_file}",
                "--artifacts-dir",
                "{artifacts_dir}",
                "--nested-runtime",
                str(args.nested_runtime.resolve()),
                "--model",
                "{model}",
                "--reasoning-effort",
                args.reasoning_effort,
                "--stage-timeout",
                str(args.stage_timeout),
            ],
            "prompt_transport": "file",
            "success_exit_codes": [0],
            "decoder": "jsonl",
        },
        "evaluation": {
            "scorers": [
                {
                    "id": "execution_integrity",
                    "kind": "execution_integrity",
                    "version": "execution-integrity-v1",
                    "repetitions": 1,
                    "aggregation": "mean",
                    "metrics": [
                        {
                            "id": name,
                            "type": "binary",
                            "lower_bound": 0,
                            "upper_bound": 1,
                            "direction": "higher",
                        }
                        for name in (
                            "process_completed",
                            "protocol_completed",
                            "capture_completed",
                            "workspace_completed",
                        )
                    ],
                },
                {
                    "id": "symbolic_oracle",
                    "kind": "command",
                    "version": "symbolic-oracle-v2",
                    "argv": [str(Path(sys.executable).absolute()), str(scorer), "{manifest}"],
                    "support_files": [
                        {"path": str(scorer)},
                        {"path": str(hidden)},
                        {"path": str(evaluator)},
                    ],
                    "include_prompt": False,
                    "repetitions": 2,
                    "aggregation": "mean",
                    "timeout_seconds": 30,
                    "metrics": [
                        {
                            "id": "ifo_auc",
                            "type": "continuous",
                            "lower_bound": 0,
                            "upper_bound": 1,
                            "direction": "lower",
                        },
                        {
                            "id": "structure_recovery",
                            "type": "continuous",
                            "lower_bound": 0,
                            "upper_bound": 1,
                            "direction": "higher",
                        },
                        {
                            "id": "ood_nmse",
                            "type": "continuous",
                            "lower_bound": 0,
                            "upper_bound": 100,
                            "direction": "lower",
                        },
                        {
                            "id": "protocol_complete",
                            "type": "binary",
                            "lower_bound": 0,
                            "upper_bound": 1,
                            "direction": "higher",
                        },
                    ],
                },
            ]
        },
        "analysis": analysis,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        yaml.safe_dump(study, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "mode": args.mode,
                "arms": [item["id"] for item in prompts],
                "episodes": repeats * len(prompts),
                "credential_ref": "env:CODEX_HOME",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
