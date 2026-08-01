from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from llm_status_machine.harnesses.base import HarnessAdapter, LaunchSpec
from llm_status_machine.utils import canonical_json, utc_now
from llm_status_machine.version import EVENT_SCHEMA_VERSION


@dataclass(frozen=True)
class ProcessResult:
    return_code: int | None
    timed_out: bool
    kill_stage: str | None
    terminal_seen: bool
    parse_errors: int
    event_count: int
    started_monotonic: float
    finished_monotonic: float


class TranscriptWriter:
    def __init__(self, path: Path, *, run_id: str, episode_id: str, attempt_id: str) -> None:
        self.handle = path.open("wb")
        self.lock = asyncio.Lock()
        self.arrival_seq = 0
        self.event_seq = 0
        self.run_id = run_id
        self.episode_id = episode_id
        self.attempt_id = attempt_id

    async def append(self, payload: dict[str, Any], *, canonical_event: bool = False) -> int:
        async with self.lock:
            self.arrival_seq += 1
            if canonical_event:
                self.event_seq += 1
                payload["seq"] = self.event_seq
            payload.update(
                {
                    "schema_version": EVENT_SCHEMA_VERSION,
                    "run_id": self.run_id,
                    "episode_id": self.episode_id,
                    "attempt_id": self.attempt_id,
                    "arrival_seq": self.arrival_seq,
                    "recorded_at": utc_now(),
                    "monotonic": time.monotonic(),
                }
            )
            self.handle.write(canonical_json(payload))
            return self.arrival_seq

    def close(self) -> None:
        self.handle.flush()
        os.fsync(self.handle.fileno())
        self.handle.close()


async def record_process(
    *,
    launch: LaunchSpec,
    adapter: HarnessAdapter,
    cwd: Path,
    raw_bundle: Path,
    run_id: str,
    episode_id: str,
    attempt_id: str,
    timeout: float,
    terminate_grace: float,
) -> ProcessResult:
    stdout_path = raw_bundle / "stdout.raw"
    stderr_path = raw_bundle / "stderr.raw"
    transcript = TranscriptWriter(
        raw_bundle / "transcript.jsonl", run_id=run_id, episode_id=episode_id, attempt_id=attempt_id
    )
    process = await asyncio.create_subprocess_exec(
        *launch.argv,
        cwd=cwd,
        env=launch.env,
        stdin=asyncio.subprocess.PIPE if launch.stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=os.name == "posix",
    )
    if launch.stdin is not None and process.stdin is not None:
        process.stdin.write(launch.stdin)
        await process.stdin.drain()
        process.stdin.close()
    await transcript.append(
        {
            "record_type": "process.started",
            "pid": process.pid,
            "argv_sha256": hashlib.sha256("\0".join(launch.argv).encode()).hexdigest(),
        }
    )
    terminal_seen = False
    parse_errors = 0
    started = time.monotonic()

    async def pump(stream_name: str, reader: asyncio.StreamReader, destination: Path) -> None:
        nonlocal terminal_seen, parse_errors
        offset = 0
        stream_seq = 0
        buffer = bytearray()
        with destination.open("wb") as raw:
            while True:
                chunk = await reader.read(64 * 1024)
                if not chunk:
                    break
                raw.write(chunk)
                stream_seq += 1
                chunk_offset = offset
                offset += len(chunk)
                await transcript.append(
                    {
                        "record_type": "stream.chunk",
                        "source": stream_name,
                        "stream_seq": stream_seq,
                        "raw_offset": chunk_offset,
                        "raw_length": len(chunk),
                        "raw_sha256": hashlib.sha256(chunk).hexdigest(),
                    }
                )
                if stream_name != "stdout":
                    continue
                buffer.extend(chunk)
                while newline := buffer.find(b"\n") + 1:
                    line = bytes(buffer[:newline])
                    del buffer[:newline]
                    await decode_line(line, chunk_offset + len(chunk) - len(buffer) - len(line))
            if buffer and stream_name == "stdout":
                await decode_line(bytes(buffer), offset - len(buffer))
            raw.flush()
            os.fsync(raw.fileno())

    async def decode_line(line: bytes, raw_offset: int) -> None:
        nonlocal terminal_seen, parse_errors
        if launch.decoder == "text":
            await transcript.append(
                {
                    "record_type": "event",
                    "source": "stdout",
                    "kind": "output.text",
                    "vendor_kind": "text",
                    "raw_offset": raw_offset,
                    "raw_length": len(line),
                    "raw_sha256": hashlib.sha256(line).hexdigest(),
                    "payload": {"text": line.decode("utf-8", "replace")},
                },
                canonical_event=True,
            )
            return
        try:
            decoded = line.decode("utf-8", "strict")
            vendor = json.loads(decoded)
            if not isinstance(vendor, dict):
                raise TypeError("JSONL event is not an object")
            vendor_kind = str(vendor.get("type", "unknown"))
            kind, payload = adapter.normalize_event(vendor)
            terminal_seen = terminal_seen or vendor_kind in launch.terminal_kinds
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            parse_errors += 1
            vendor_kind = "parse_error"
            kind = "protocol.parse_error"
            payload = {"error": str(error)}
        await transcript.append(
            {
                "record_type": "event",
                "source": "stdout",
                "kind": kind,
                "vendor_kind": vendor_kind,
                "raw_offset": raw_offset,
                "raw_length": len(line),
                "raw_sha256": hashlib.sha256(line).hexdigest(),
                "payload": payload,
            },
            canonical_event=True,
        )

    assert process.stdout is not None and process.stderr is not None
    stdout_task = asyncio.create_task(pump("stdout", process.stdout, stdout_path))
    stderr_task = asyncio.create_task(pump("stderr", process.stderr, stderr_path))
    timed_out = False
    kill_stage: str | None = None
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout)
    except TimeoutError:
        timed_out = True
        kill_stage = "term"
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=terminate_grace)
        except TimeoutError:
            kill_stage = "kill"
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            await process.wait()
    except asyncio.CancelledError:
        kill_stage = "cancel"
        if process.returncode is None:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            await process.wait()
        raise
    finally:
        await asyncio.gather(stdout_task, stderr_task)
        await transcript.append(
            {
                "record_type": "process.finished",
                "return_code": process.returncode,
                "timed_out": timed_out,
                "kill_stage": kill_stage,
            }
        )
        transcript.close()
    return ProcessResult(
        return_code=process.returncode,
        timed_out=timed_out,
        kill_stage=kill_stage,
        terminal_seen=terminal_seen or not launch.terminal_kinds,
        parse_errors=parse_errors,
        event_count=transcript.event_seq,
        started_monotonic=started,
        finished_monotonic=time.monotonic(),
    )
