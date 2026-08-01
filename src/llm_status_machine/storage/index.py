from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from llm_status_machine.utils import read_json
from llm_status_machine.version import SCHEMA_VERSION

SCHEMA = """
create table if not exists metadata (
  key text primary key,
  value text not null
);
create table if not exists runs (
  id text primary key,
  plan_id text not null,
  status text not null,
  started_at text not null,
  finished_at text,
  path text not null,
  body text not null
);
create table if not exists episodes (
  id text primary key,
  run_id text not null references runs(id) on delete cascade,
  ordinal integer not null,
  status text not null,
  bundle_sha256 text,
  path text not null,
  body text not null
);
create index if not exists episodes_run on episodes(run_id, ordinal);
"""


class IndexStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("pragma journal_mode=WAL")
        self.connection.execute("pragma foreign_keys=ON")
        self.connection.executescript(SCHEMA)
        self.connection.execute(
            "insert or replace into metadata(key, value) values('schema_version', ?)", (str(SCHEMA_VERSION),)
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def upsert_run(self, run: dict[str, Any], path: Path) -> None:
        self.connection.execute(
            """insert into runs(id, plan_id, status, started_at, finished_at, path, body)
               values(?, ?, ?, ?, ?, ?, ?)
               on conflict(id) do update set status=excluded.status, finished_at=excluded.finished_at,
                 path=excluded.path, body=excluded.body""",
            (
                run["id"],
                run["plan_id"],
                run["status"],
                run["started_at"],
                run.get("finished_at"),
                str(path),
                json.dumps(run, ensure_ascii=False, sort_keys=True),
            ),
        )
        self.connection.commit()

    def upsert_episode(self, run_id: str, episode: dict[str, Any], path: Path) -> None:
        self.connection.execute(
            """insert into episodes(id, run_id, ordinal, status, bundle_sha256, path, body)
               values(?, ?, ?, ?, ?, ?, ?)
               on conflict(id) do update set status=excluded.status, bundle_sha256=excluded.bundle_sha256,
                 path=excluded.path, body=excluded.body""",
            (
                episode["id"],
                run_id,
                episode["ordinal"],
                episode["status"],
                episode.get("bundle_sha256"),
                str(path),
                json.dumps(episode, ensure_ascii=False, sort_keys=True),
            ),
        )
        self.connection.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("select body from runs where id = ?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list_runs(self) -> list[dict[str, Any]]:
        return [
            json.loads(row[0])
            for row in self.connection.execute("select body from runs order by started_at desc")
        ]

    def reindex(self, runs_root: Path) -> dict[str, int]:
        counts = {"runs": 0, "episodes": 0, "skipped": 0}
        for run_path in sorted(runs_root.glob("*/run.json")):
            try:
                run = read_json(run_path)
                self.upsert_run(run, run_path.parent)
                counts["runs"] += 1
                for episode_path in sorted((run_path.parent / "episodes").glob("*/episode.json")):
                    episode = read_json(episode_path)
                    self.upsert_episode(run["id"], episode, episode_path.parent)
                    counts["episodes"] += 1
            except (OSError, KeyError, ValueError, json.JSONDecodeError):
                counts["skipped"] += 1
        return counts
