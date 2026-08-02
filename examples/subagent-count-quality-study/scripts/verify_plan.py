from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

COUNT_LINE = re.compile(r"^EVALUATOR_COUNT = (3|6|9)$", re.MULTILINE)
EXPECTED_SEED = 20260810
EXPECTED_ORDER = [9, 3, 6]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    records = [json.loads(line) for line in args.plan.read_text(encoding="utf-8").splitlines()]
    header, trials = records[0], records[1:]
    if header["seed"] != EXPECTED_SEED:
        raise ValueError(f"pilot seed must be {EXPECTED_SEED}")
    if header["concurrency"] != 1 or header["state_policy"] != "independent":
        raise ValueError("study must be serial at the top level with independent workspaces")
    if len(trials) != 3:
        raise ValueError("pilot must contain exactly three trials")
    counts = []
    normalized_hashes = set()
    controls = []
    for trial in trials:
        body = trial["actual_prompt"]
        matches = COUNT_LINE.findall(body)
        if len(matches) != 1:
            raise ValueError(f"invalid treatment in {trial['id']}")
        counts.append(int(matches[0]))
        normalized = COUNT_LINE.sub("EVALUATOR_COUNT = {{EVALUATOR_COUNT}}", body)
        normalized_hashes.add(hashlib.sha256(normalized.encode()).hexdigest())
        controls.append(
            json.dumps(
                {
                    "runtime": trial["runtime"],
                    "endpoint": trial["endpoint"],
                    "profile": trial["profile"],
                    "workspace": trial["workspace"],
                },
                sort_keys=True,
            )
        )
    if sorted(counts) != [3, 6, 9]:
        raise ValueError(f"unexpected treatments: {counts}")
    if counts != EXPECTED_ORDER:
        raise ValueError(f"unexpected randomized execution order: {counts}")
    if len(normalized_hashes) != 1 or len(set(controls)) != 1:
        raise ValueError("trials differ outside the evaluator-count treatment")
    print(
        json.dumps(
            {
                "valid": True,
                "execution_order": counts,
                "normalized_prompt_sha256": next(iter(normalized_hashes)),
                "model": trials[0]["endpoint"]["model_id"],
                "reasoning_effort": trials[0]["profile"]["reasoning_effort"],
                "sandbox": trials[0]["profile"]["permissions"],
                "ephemeral": trials[0]["profile"]["ephemeral"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
