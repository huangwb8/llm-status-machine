from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def emit(event: dict[str, object]) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    args = parser.parse_args()
    prompt = args.prompt_file.read_text(encoding="utf-8")
    emit({"type": "started", "prompt_chars": len(prompt)})
    if "[SIMULATOR_PARTIAL_JSON]" in prompt:
        sys.stdout.buffer.write(b'{"type":"message","text":"partial')
        sys.stdout.buffer.flush()
        sys.stdout.buffer.write(b' framing"}\n')
        sys.stdout.buffer.flush()
    if "[SIMULATOR_LARGE_JSON]" in prompt:
        emit({"type": "message", "text": "x" * 200_000})
    if "[SIMULATOR_UNKNOWN_EVENT]" in prompt:
        emit({"unexpected": True})
    if "[SIMULATOR_INVALID_JSON]" in prompt:
        sys.stdout.buffer.write(b'{"type":"broken"\xff\n')
        sys.stdout.buffer.flush()
    if "[SIMULATOR_TIMEOUT]" in prompt:
        time.sleep(3600)
    if "[SIMULATOR_CHILD_TIMEOUT]" in prompt:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3600)"])
        args.artifacts_dir.mkdir(parents=True, exist_ok=True)
        (args.artifacts_dir / "child.pid").write_text(str(child.pid), encoding="ascii")
        time.sleep(3600)
    notes = Path.cwd() / "llm-simulator-notes.md"
    with notes.open("a", encoding="utf-8") as handle:
        handle.write(f"\n- episode {os.environ.get('LLM_STATUS_MACHINE_EPISODE_ID', 'unknown')}: {prompt}\n")
    args.artifacts_dir.mkdir(parents=True, exist_ok=True)
    (args.artifacts_dir / "behavior-summary.md").write_text(
        "# Simulator behavior\n\nWorkspace updated and evidence emitted.\n", encoding="utf-8"
    )
    print("simulator diagnostic", file=sys.stderr, flush=True)
    if "[SIMULATOR_FAIL]" in prompt:
        emit({"type": "result", "status": "failed"})
        return 7
    emit({"type": "message", "text": "山河新貌映华年，美人风骨立云巅。赤心共绘神州景，笑看春光满大千。"})
    emit({"type": "result", "status": "completed"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
