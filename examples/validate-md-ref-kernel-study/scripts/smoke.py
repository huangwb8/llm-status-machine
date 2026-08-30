from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one dry integration episode for this instance.")
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    output = args.root.resolve()
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/run.py"),
            "--dry-run",
            "--repeats",
            "1",
            "--output-root",
            str(output),
            "--skills-root",
            str(output / "external-skills"),
        ],
        cwd=REPOSITORY,
        check=True,
    )


if __name__ == "__main__":
    main()
