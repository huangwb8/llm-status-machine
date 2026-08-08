from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ARM_IDS = {"neutral-open", "correct-open", "false-open", "false-gated", "false-purged"}
REGIONS = re.compile(r"<!-- (PRIOR|MEMORY)_START -->.*?<!-- \1_END -->", re.DOTALL)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    records = [json.loads(line) for line in args.plan.read_text(encoding="utf-8").splitlines()]
    header, trials = records[0], records[1:]
    if header["study_mode"] != "confirmatory" or not header["diagnostics"]["confirmatory_valid"]:
        raise ValueError("qualification plan must pass the confirmatory design gate")
    if header["concurrency"] != 1 or header["state_policy"] != "independent":
        raise ValueError("qualification episodes must be serial and independent")
    if len(trials) != 25 or Counter(trial["arm_id"] for trial in trials) != Counter({arm: 5 for arm in ARM_IDS}):
        raise ValueError("qualification plan must contain five balanced repetitions of five arms")
    positions: dict[str, list[int]] = defaultdict(list)
    normalized_hashes = set()
    controls = set()
    for trial in trials:
        positions[trial["arm_id"]].append(trial["sequence_position"])
        normalized = REGIONS.sub(lambda match: f"<!-- {match.group(1)}_REGION -->", trial["actual_prompt"])
        normalized_hashes.add(hashlib.sha256(normalized.encode()).hexdigest())
        controls.add(
            json.dumps(
                {name: trial[name] for name in ("runtime", "endpoint", "profile", "workspace")},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        if trial.get("randomization") is None or not trial["comparison_set_id"]:
            raise ValueError("randomization provenance is incomplete")
    if any(sorted(value) != [1, 2, 3, 4, 5] for value in positions.values()):
        raise ValueError(f"sequence positions are not balanced: {dict(positions)}")
    if len(normalized_hashes) != 1 or len(controls) != 1:
        raise ValueError("trials differ outside preregistered prompt regions")
    oracle = next(item for item in header["evaluation"]["scorers"] if item["id"] == "symbolic_oracle")
    if oracle["include_prompt"] or not oracle["executable_sha256"]:
        raise ValueError("symbolic oracle must be blind and pinned")
    if any(not item["sha256"] for item in oracle["support_files"]):
        raise ValueError("symbolic oracle support files must be pinned")
    print(
        json.dumps(
            {
                "valid": True,
                "episodes": len(trials),
                "arms": sorted(ARM_IDS),
                "sequence_positions": {key: sorted(value) for key, value in sorted(positions.items())},
                "normalized_prompt_sha256": next(iter(normalized_hashes)),
                "scorer_blind": True,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

