from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SEED = 20260808
ARMS = {
    "neutral-open": {
        "PRIOR_CONDITION": "neutral",
        "PRIOR_TEXT": "以同等注意力比较多项式、有理式、指数、周期和组合函数族。",
        "MEMORY_POLICY": "open",
        "MEMORY_TEXT": "阶段之间保留全部候选、测量记录、代码和叙述性笔记。",
    },
    "correct-open": {
        "PRIOR_CONDITION": "correct",
        "PRIOR_TEXT": "优先探索与已验证测量证据一致的任务特异函数族。",
        "MEMORY_POLICY": "open",
        "MEMORY_TEXT": "阶段之间保留全部候选、测量记录、代码和叙述性笔记。",
    },
    "false-open": {
        "PRIOR_CONDITION": "false",
        "PRIOR_TEXT": "优先探索局部拟合合理但方向错误的替代函数族。",
        "MEMORY_POLICY": "open",
        "MEMORY_TEXT": "阶段之间保留全部候选、测量记录、代码和叙述性笔记。",
    },
    "false-gated": {
        "PRIOR_CONDITION": "false",
        "PRIOR_TEXT": "优先探索局部拟合合理但方向错误的替代函数族。",
        "MEMORY_POLICY": "gated",
        "MEMORY_TEXT": "阶段转换只保留外部 evaluator 签出的候选、数值和失败类型。",
    },
    "false-purged": {
        "PRIOR_CONDITION": "false",
        "PRIOR_TEXT": "优先探索局部拟合合理但方向错误的替代函数族。",
        "MEMORY_POLICY": "purged",
        "MEMORY_TEXT": "阶段转换清除模型状态，并写入等量的中性占位记录。",
    },
}
REGIONS = re.compile(
    r"<!-- (PRIOR|MEMORY)_START -->.*?<!-- \1_END -->", re.DOTALL
)


def materialize_prompts() -> list[dict[str, str]]:
    template = (ROOT / "prompts/prompt-template.md").read_text(encoding="utf-8")
    prompts = []
    normalized_hashes = set()
    for arm_id, values in ARMS.items():
        body = template
        for name, value in values.items():
            body = body.replace("{{" + name + "}}", value)
        if "{{" in body or "}}" in body:
            raise ValueError(f"unrendered prompt variable in {arm_id}")
        normalized = REGIONS.sub(lambda match: f"<!-- {match.group(1)}_REGION -->", body)
        normalized_hashes.add(hashlib.sha256(normalized.encode()).hexdigest())
        path = ROOT / "prompts" / f"{arm_id}.md"
        path.write_text(body, encoding="utf-8")
        prompts.append({"id": arm_id, "body": body})
    if len(normalized_hashes) != 1:
        raise ValueError("prompts differ outside preregistered PRIOR/MEMORY regions")
    lengths = [len(prompt["body"]) for prompt in prompts]
    if max(lengths) - min(lengths) > 12:
        raise ValueError(f"prompt character counts are not approximately matched: {lengths}")
    return prompts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    runtime = json.loads(args.runtime.read_text(encoding="utf-8"))
    if runtime.get("surface") != "custom_command":
        raise ValueError("qualification study requires a custom_command runtime lock")
    scorer = (ROOT / "oracle_tests/symbolic_oracle.py").resolve()
    hidden = (ROOT / "oracle_tests/hidden_tasks.json").resolve()
    evaluator = (ROOT / "fixture/tools/evaluate_candidate.py").resolve()
    runner = "{workspace}/tools/qualification_runner.py"
    python = str(Path(runtime["executable"]).absolute())
    study = {
        "schema_version": 2,
        "name": "prior-washout-evidence-gating-qualification",
        "study_mode": "confirmatory",
        "seed": SEED,
        "repeats": 5,
        "concurrency": 1,
        "state_policy": "independent",
        "design": "full_factorial",
        "prompts": materialize_prompts(),
        "workspace": {
            "path": str((ROOT / "fixture").resolve()),
            "excludes": [".git", ".lsm", ".bensz-api", ".venv", "node_modules", "__pycache__", ".DS_Store"],
        },
        "runtime": runtime,
        "endpoint": {"provider": "local", "model_id": "deterministic-protocol-qualification"},
        "profile": {
            "name": "three-phase-qualification",
            "config_mode": "hermetic",
            "research_mode": "controlled",
            "timeout_seconds": 30,
            "terminate_grace_seconds": 2,
            "network": "inherit",
            "permissions": "workspace-write",
            "ephemeral": True,
            "custom_argv": [python, runner, "--prompt-file", "{prompt_file}", "--artifacts-dir", "{artifacts_dir}"],
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
                    "repetitions": 2,
                    "aggregation": "mean",
                    "metrics": [
                        {"id": name, "type": "binary", "lower_bound": 0, "upper_bound": 1, "direction": "higher"}
                        for name in ("process_completed", "protocol_completed", "capture_completed", "workspace_completed")
                    ],
                },
                {
                    "id": "symbolic_oracle",
                    "kind": "command",
                    "version": "symbolic-oracle-v1",
                    "argv": [str(Path(sys.executable).absolute()), str(scorer), "{manifest}"],
                    "support_files": [{"path": str(scorer)}, {"path": str(hidden)}, {"path": str(evaluator)}],
                    "include_prompt": False,
                    "repetitions": 2,
                    "aggregation": "mean",
                    "timeout_seconds": 30,
                    "metrics": [
                        {"id": "ifo_auc", "type": "continuous", "lower_bound": 0, "upper_bound": 1, "direction": "lower"},
                        {"id": "structure_recovery", "type": "continuous", "lower_bound": 0, "upper_bound": 1, "direction": "higher"},
                        {"id": "ood_nmse", "type": "continuous", "lower_bound": 0, "upper_bound": 100, "direction": "lower"},
                        {"id": "protocol_complete", "type": "binary", "lower_bound": 0, "upper_bound": 1, "direction": "higher"},
                    ],
                },
            ]
        },
        "analysis": {
            "outcomes": [
                {"id": "primary_ifo_auc", "scorer_id": "symbolic_oracle", "metric_id": "ifo_auc", "role": "primary", "type": "continuous", "direction": "lower", "lower_bound": 0, "upper_bound": 1, "failure_policy": "worst_case", "missing_policy": "error"},
                {"id": "secondary_structure_recovery", "scorer_id": "symbolic_oracle", "metric_id": "structure_recovery", "role": "secondary", "type": "continuous", "direction": "higher", "lower_bound": 0, "upper_bound": 1, "failure_policy": "worst_case", "missing_policy": "error"},
            ],
            "contrasts": [
                {"id": "false-open-v-neutral-open", "outcome_id": "primary_ifo_auc", "treatment_arm": "false-open", "control_arm": "neutral-open", "estimand": "mean_difference", "family": "primary"},
                {"id": "false-gated-v-false-open", "outcome_id": "primary_ifo_auc", "treatment_arm": "false-gated", "control_arm": "false-open", "estimand": "mean_difference", "family": "primary"},
                {"id": "false-purged-v-false-open", "outcome_id": "primary_ifo_auc", "treatment_arm": "false-purged", "control_arm": "false-open", "estimand": "mean_difference", "family": "primary"},
            ],
            "alpha": 0.05,
            "permutations": 500,
            "bootstrap_samples": 200,
            "seed": SEED,
            "multiplicity": "holm",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(yaml.safe_dump(study, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "arms": sorted(ARMS), "python": platform.python_version()}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
