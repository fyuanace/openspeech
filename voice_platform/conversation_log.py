"""Persistent, text-only conversation history for the server."""

import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

LOG = logging.getLogger(__name__)


class ConversationLog:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.write_failed = False
        with closing(sqlite3.connect(path)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    model TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    user_text TEXT NOT NULL,
                    reply TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    UNIQUE (mode, device_id, session_id, turn_id)
                )
            """)

    def record(self, *, started_at, mode, model, identifiers, text="", reply="",
               outcome="completed", stage="", duration_ms=0):
        try:
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute("""
                    INSERT INTO turns (started_at, mode, model, device_id, session_id,
                        turn_id, user_text, reply, outcome, stage, duration_ms)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (mode, device_id, session_id, turn_id) DO NOTHING
                """, (datetime.fromtimestamp(started_at, timezone.utc).isoformat(), mode, model,
                      *identifiers, text[:4000], reply[:16000], outcome, stage, duration_ms))
            self.write_failed = False
        except sqlite3.Error:
            self.write_failed = True
            LOG.error("Could not save a conversation log record")

    def recent(self, before=None, limit=30):
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            if before is None:
                rows = db.execute("SELECT * FROM turns ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM turns WHERE id < ? ORDER BY id DESC LIMIT ?",
                                  (before, limit)).fetchall()
            return [dict(row) for row in rows]
