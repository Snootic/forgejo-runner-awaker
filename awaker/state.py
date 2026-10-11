import sqlite3
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import cast
from uuid import uuid4

from .models import StateEntry

type SQLValue = str | int | float | bytes | None


class State:
    def __init__(self, path: str, url: str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.url = url
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.execute("""CREATE TABLE IF NOT EXISTS runners (
            name TEXT PRIMARY KEY, url TEXT NOT NULL, repo TEXT NOT NULL,
            handle TEXT NOT NULL, runner_id INTEGER, container_name TEXT,
            pending INTEGER NOT NULL DEFAULT 0, released INTEGER NOT NULL DEFAULT 0,
            runner_scope TEXT NOT NULL DEFAULT ''
        )""")
        if "runner_scope" not in {
            row["name"] for row in self.execute("PRAGMA table_info(runners)")
        }:
            self.execute(
                "ALTER TABLE runners ADD COLUMN runner_scope TEXT NOT NULL DEFAULT ''"
            )

    def execute(
        self, sql: str, parameters: Sequence[SQLValue] = ()
    ) -> list[sqlite3.Row]:
        with self.lock, self.db:
            return self.db.execute(sql, parameters).fetchall()

    def begin(
        self,
        repo: str,
        handle: str,
        runner_id: int | None = None,
        container_name: str | None = None,
        runner_scope: str | None = None,
    ) -> str:
        name = f"awaker-{uuid4().hex}"
        self.execute(
            """INSERT INTO runners (
                name, url, repo, handle, runner_id, container_name, runner_scope
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                name,
                self.url,
                repo,
                handle,
                runner_id,
                container_name or name,
                runner_scope or repo,
            ),
        )
        return name

    def registered(self, name: str, runner_id: int) -> None:
        self.execute("UPDATE runners SET runner_id=? WHERE name=?", (runner_id, name))

    def mark_pending(self, name: str) -> None:
        self.execute("UPDATE runners SET pending=1 WHERE name=?", (name,))

    def mark_released(self, name: str) -> None:
        self.execute("UPDATE runners SET released=1 WHERE name=?", (name,))

    def forget(self, name: str) -> None:
        self.execute("DELETE FROM runners WHERE name=?", (name,))

    def entries(self) -> list[StateEntry]:
        return [
            cast(StateEntry, dict(row))
            for row in self.execute("SELECT * FROM runners WHERE url=?", (self.url,))
        ]

    def get(self, name: str) -> StateEntry | None:
        rows = self.execute(
            "SELECT * FROM runners WHERE name=? AND url=?", (name, self.url)
        )
        return cast(StateEntry, dict(rows[0])) if rows else None
