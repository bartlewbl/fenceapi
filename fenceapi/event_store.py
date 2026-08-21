from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_EVENTS_DB = Path("data/events.sqlite")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventStore:
    """SQLite cache for calendar lists, tournament pages, and FWW snapshots."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_EVENTS_DB
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()

    def close(self) -> None:
        self._conn.close()

    def _init(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS blobs (
              kind TEXT NOT NULL,
              cache_key TEXT NOT NULL,
              payload TEXT NOT NULL,
              fetched_at TEXT NOT NULL,
              PRIMARY KEY (kind, cache_key)
            );
            CREATE INDEX IF NOT EXISTS idx_blobs_fetched ON blobs(kind, fetched_at);
            """
        )
        self._conn.commit()

    def get(self, kind: str, cache_key: str) -> tuple[Any, str] | None:
        row = self._conn.execute(
            "SELECT payload, fetched_at FROM blobs WHERE kind=? AND cache_key=?",
            (kind, cache_key),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["payload"]), row["fetched_at"]

    def put(self, kind: str, cache_key: str, payload: Any, fetched_at: str | None = None) -> str:
        stamp = fetched_at or utcnow()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO blobs(kind, cache_key, payload, fetched_at)
                VALUES(?,?,?,?)
                ON CONFLICT(kind, cache_key) DO UPDATE SET
                  payload=excluded.payload,
                  fetched_at=excluded.fetched_at
                """,
                (kind, cache_key, json.dumps(payload, ensure_ascii=False), stamp),
            )
            self._conn.commit()
        return stamp

    def stats(self) -> dict[str, Any]:
        rows = self._conn.execute(
            """
            SELECT kind, COUNT(*) AS n, MIN(fetched_at) AS oldest, MAX(fetched_at) AS newest
            FROM blobs GROUP BY kind
            """
        ).fetchall()
        by_kind = {
            row["kind"]: {"count": row["n"], "oldest": row["oldest"], "newest": row["newest"]}
            for row in rows
        }
        return {"db": str(self.path), "kinds": by_kind}
