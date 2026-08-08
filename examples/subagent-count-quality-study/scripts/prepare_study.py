from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COUNTS = (3, 6, 9)
PILOT_SEED = 20260810
COUNT_LINE = re.compile(r"^EVALUATOR_COUNT = (3|6|9)$", re.MULTILINE)


def materialize_prompts() -> list[dict[str, str]]:
    template = (ROOT / "prompts" / "prompt-template.md").read_text(encoding="utf-8")
    rendered = []
    normalized_hashes = set()
    for count in COUNTS:
        body = template.replace("{{EVALUATOR_COUNT}}", str(count))
        if len(COUNT_LINE.findall(body)) != 1:
            raise ValueError(f"prompt-{count} must contain exactly one evaluator count line")
        normalized = COUNT_LINE.sub("EVALUATOR_COUNT = {{EVALUATOR_COUNT}}", body)
        normalized_hashes.add(hashlib.sha256(normalized.encode()).hexdigest())
        path = ROOT / "prompts" / f"prompt-{count}.md"
        path.write_text(body, encoding="utf-8")
        rendered.append({"id": f"evaluators-{count}", "body": body})
    if len(normalized_hashes) != 1:
        raise ValueError("materialized prompts differ outside EVALUATOR_COUNT")
    return rendered


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not os.environ.get("CODEX_HOME"):
        raise SystemExit("CODEX_HOME must point to the existing external Codex configuration")
    runtime = json.loads(args.runtime.read_text(encoding="utf-8"))
    scorer = (ROOT / "oracle_tests" / "lsm_scorer.py").resolve()
    oracle = (ROOT / "oracle_tests" / "score_cache.py").resolve()
    scorer_python = Path(sys.executable).absolute()
    category_bounds = {
        "basic_ttl": 15,
        "lru_capacity": 15,
        "single_flight": 20,
        "failure_cancel": 20,
        "invalidation_race": 20,
        "api_quality": 10,
    }
    study = {
        "schema_version": 2,
        "name": "subagent-count-quality-confirmatory",
        "study_mode": "confirmatory",
        # The pre-registered seed determines comparison-set order while balanced rotation
        # places every arm once in each sequence position across three repetitions.
        "seed": PILOT_SEED,
        "repeats": 3,
        "concurrency": 1,
        "state_policy": "independent",
        "design": "full_factorial",
        "prompts": materialize_prompts(),
        "workspace": {"path": str((ROOT / "fixture").resolve())},
        "runtime": runtime,
        "endpoint": {"provider": "openai", "model_id": "gpt-5.6-sol"},
        "profile": {
            "name": "codex-subagent-quality-pilot",
            "config_mode": "workspace_native",
            "research_mode": "ecological",
            "timeout_seconds": 1800,
            "terminate_grace_seconds": 10,
            "reasoning_effort": "medium",
            "network": "inherit",
            "permissions": "workspace-write",
            "ephemeral": True,
            "env_allowlist": ["CODEX_HOME"],
        },
        "evaluation": {
            "scorers": [
                {
                    "id": "oracle",
                    "kind": "command",
                    "version": "async-ttl-cache-v2",
                    "argv": [str(scorer_python), str(scorer)],
                    "support_files": [{"path": str(scorer)}, {"path": str(oracle)}],
                    "include_prompt": False,
                    "repetitions": 1,
                    "aggregation": "mean",
                    "timeout_seconds": 180,
                    "metrics": [
                        {
                            "id": "quality_score",
                            "type": "continuous",
                            "lower_bound": 0,
                            "upper_bound": 100,
                            "direction": "higher",
                        },
                        *[
                            {
                                "id": f"category_{name}",
                                "type": "continuous",
                                "lower_bound": 0,
                                "upper_bound": upper,
                                "direction": "higher",
                            }
                            for name, upper in category_bounds.items()
                        ],
                    ],
                }
            ]
        },
        "analysis": {
            "outcomes": [
                {
                    "id": "primary_quality",
                    "scorer_id": "oracle",
                    "metric_id": "quality_score",
                    "role": "primary",
                    "type": "continuous",
                    "direction": "higher",
                    "lower_bound": 0,
                    "upper_bound": 100,
                    "failure_policy": "worst_case",
                    "missing_policy": "error",
                }
            ],
            "contrasts": [
                {
                    "id": "evaluators-6-v-3",
                    "outcome_id": "primary_quality",
                    "treatment_arm": "evaluators-6",
                    "control_arm": "evaluators-3",
                    "estimand": "mean_difference",
                    "family": "primary",
                },
                {
                    "id": "evaluators-9-v-3",
                    "outcome_id": "primary_quality",
                    "treatment_arm": "evaluators-9",
                    "control_arm": "evaluators-3",
                    "estimand": "mean_difference",
                    "family": "primary",
                },
            ],
            "alpha": 0.05,
            "permutations": 10000,
            "bootstrap_samples": 5000,
            "seed": PILOT_SEED,
            "multiplicity": "holm",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(yaml.safe_dump(study, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "prompts": list(COUNTS)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
