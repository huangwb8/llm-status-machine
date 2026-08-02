from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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
    study = {
        "schema_version": 1,
        "name": "subagent-count-quality-pilot",
        # Pre-registered seed gives the non-monotonic order 9 -> 3 -> 6,
        # avoiding the original pilot's count/time confounding on reruns.
        "seed": PILOT_SEED,
        "repeats": 1,
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
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        yaml.safe_dump(study, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    print(json.dumps({"output": str(args.output), "prompts": list(COUNTS)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
