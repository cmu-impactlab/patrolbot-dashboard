"""SQLite persistence via aiosqlite.

Deliberately a thin SQL repository: swapping to PostgreSQL at Phase 5 means
reimplementing this module only. (SQLAlchemy async was skipped because the
dev machine runs Python 3.14 where greenlet wheels are a moving target.)
"""
from __future__ import annotations

import json
import os
from typing import Any

import aiosqlite

from ..protocol.messages import EventData
from .presets import PRESET_LAYOUTS

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS layouts (
    id INTEGER PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    name TEXT NOT NULL,
    is_preset INTEGER NOT NULL DEFAULT 0,
    layout_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, name)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    robot_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS battery_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    robot_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    voltage REAL NOT NULL,
    percentage REAL,
    current REAL,
    charging INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS command_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    robot_id TEXT NOT NULL,
    command_id TEXT NOT NULL UNIQUE,
    command TEXT NOT NULL,
    goal TEXT,
    requested_at TEXT NOT NULL DEFAULT (datetime('now')),
    outcome TEXT,
    detail TEXT,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS recordings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    robot_id TEXT NOT NULL,
    name TEXT NOT NULL,
    channels TEXT NOT NULL DEFAULT '["pose","battery","event"]',
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at TEXT,
    status TEXT NOT NULL DEFAULT 'recording',
    sample_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS recording_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recording_id INTEGER NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_battery_ts ON battery_samples(robot_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(robot_id, ts);
CREATE INDEX IF NOT EXISTS idx_samples_rec ON recording_samples(recording_id, id);
"""


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        # Migration for databases created before channel selection existed.
        async with self._db.execute("PRAGMA table_info(recordings)") as cur:
            columns = [row[1] for row in await cur.fetchall()]
        if "channels" not in columns:
            await self._db.execute(
                "ALTER TABLE recordings ADD COLUMN channels TEXT NOT NULL "
                "DEFAULT '[\"pose\",\"battery\",\"event\"]'")
        await self._db.execute(
            "INSERT OR IGNORE INTO users (id, username, display_name) VALUES (1, 'local', 'Local Operator')"
        )
        # Presets are stored under user_id 0 (no real user has id 0); SQLite
        # treats NULLs as distinct in UNIQUE constraints, which would allow
        # duplicate preset rows on every startup.
        for name, layout in PRESET_LAYOUTS.items():
            await self._db.execute(
                "INSERT INTO layouts (user_id, name, is_preset, layout_json) VALUES (0, ?, 1, ?) "
                "ON CONFLICT(user_id, name) DO UPDATE SET layout_json = excluded.layout_json",
                (name, json.dumps(layout)),
            )
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "Database not initialized"
        return self._db

    # -- users ----------------------------------------------------------------

    async def get_or_create_user(self, username: str, display_name: str) -> int:
        await self.db.execute(
            "INSERT INTO users (username, display_name) VALUES (?, ?) "
            "ON CONFLICT(username) DO UPDATE SET display_name = excluded.display_name",
            (username, display_name))
        await self.db.commit()
        async with self.db.execute("SELECT id FROM users WHERE username = ?", (username,)) as cur:
            row = await cur.fetchone()
            return int(row["id"])

    # -- events ---------------------------------------------------------------

    async def next_event_id(self) -> int:
        async with self.db.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM events") as cur:
            row = await cur.fetchone()
            return int(row[0])

    async def add_event(self, robot_id: str, event: EventData) -> None:
        await self.db.execute(
            "INSERT OR REPLACE INTO events (id, robot_id, ts, severity, title, message) VALUES (?,?,?,?,?,?)",
            (event.id, robot_id, event.ts, event.severity, event.title, event.message),
        )
        await self.db.execute(
            "DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 5000)"
        )
        await self.db.commit()

    async def get_events(self, limit: int = 100) -> list[dict[str, Any]]:
        async with self.db.execute(
            "SELECT id, robot_id, ts, severity, title, message FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]

    # -- battery history --------------------------------------------------------

    async def add_battery_sample(self, robot_id: str, ts: str, voltage: float,
                                 percentage: float | None, current: float | None, charging: bool) -> None:
        await self.db.execute(
            "INSERT INTO battery_samples (robot_id, ts, voltage, percentage, current, charging) VALUES (?,?,?,?,?,?)",
            (robot_id, ts, voltage, percentage, current, int(charging)),
        )
        await self.db.execute(
            "DELETE FROM battery_samples WHERE ts < datetime('now', '-7 days')"
        )
        await self.db.commit()

    async def get_battery_history(self, minutes: int = 120, limit: int = 2000) -> list[dict[str, Any]]:
        async with self.db.execute(
            "SELECT ts, voltage, percentage, current, charging FROM battery_samples "
            "WHERE ts >= datetime('now', ?) ORDER BY ts DESC LIMIT ?",
            (f"-{minutes} minutes", limit),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
        rows.reverse()
        return rows

    # -- recordings --------------------------------------------------------------

    async def start_recording(self, robot_id: str, name: str,
                              channels: list[str]) -> dict[str, Any] | None:
        """Create a recording; returns None when one is already in progress."""
        async with self.db.execute("SELECT id FROM recordings WHERE status = 'recording'") as cur:
            if await cur.fetchone() is not None:
                return None
        cur = await self.db.execute(
            "INSERT INTO recordings (robot_id, name, channels) VALUES (?, ?, ?)",
            (robot_id, name, json.dumps(channels)))
        await self.db.commit()
        return await self.get_recording(cur.lastrowid)

    async def stop_recording(self, recording_id: int) -> bool:
        cur = await self.db.execute(
            "UPDATE recordings SET status = 'done', ended_at = datetime('now') "
            "WHERE id = ? AND status = 'recording'", (recording_id,))
        await self.db.commit()
        return cur.rowcount > 0

    async def add_recording_sample(self, recording_id: int, ts: str, kind: str, data: str) -> None:
        await self.db.execute(
            "INSERT INTO recording_samples (recording_id, ts, kind, data) VALUES (?,?,?,?)",
            (recording_id, ts, kind, data))
        await self.db.execute(
            "UPDATE recordings SET sample_count = sample_count + 1 WHERE id = ?", (recording_id,))
        await self.db.commit()

    async def list_recordings(self, limit: int = 50) -> list[dict[str, Any]]:
        async with self.db.execute(
            "SELECT id, robot_id, name, channels, started_at, ended_at, status, sample_count "
            "FROM recordings ORDER BY id DESC LIMIT ?", (limit,)
        ) as cur:
            return [self._recording_row(row) for row in await cur.fetchall()]

    async def get_recording(self, recording_id: int) -> dict[str, Any] | None:
        async with self.db.execute(
            "SELECT id, robot_id, name, channels, started_at, ended_at, status, sample_count "
            "FROM recordings WHERE id = ?", (recording_id,)
        ) as cur:
            row = await cur.fetchone()
            return self._recording_row(row) if row else None

    @staticmethod
    def _recording_row(row: Any) -> dict[str, Any]:
        out = dict(row)
        out["channels"] = json.loads(out["channels"])
        return out

    async def get_recording_samples(self, recording_id: int) -> list[dict[str, Any]]:
        async with self.db.execute(
            "SELECT ts, kind, data FROM recording_samples WHERE recording_id = ? ORDER BY id",
            (recording_id,)
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]

    async def delete_recording(self, recording_id: int) -> bool:
        await self.db.execute("DELETE FROM recording_samples WHERE recording_id = ?", (recording_id,))
        cur = await self.db.execute("DELETE FROM recordings WHERE id = ?", (recording_id,))
        await self.db.commit()
        return cur.rowcount > 0

    # -- command audit -----------------------------------------------------------

    async def add_command_audit(self, robot_id: str, command_id: str, command: str,
                                goal: dict[str, Any] | None) -> None:
        await self.db.execute(
            "INSERT OR IGNORE INTO command_audit (robot_id, command_id, command, goal) VALUES (?,?,?,?)",
            (robot_id, command_id, command, json.dumps(goal) if goal else None),
        )
        await self.db.commit()

    async def complete_command_audit(self, command_id: str, outcome: str, detail: str) -> None:
        await self.db.execute(
            "UPDATE command_audit SET outcome = ?, detail = ?, completed_at = datetime('now') "
            "WHERE command_id = ?",
            (outcome, detail, command_id),
        )
        await self.db.commit()

    async def get_command_audit(self, limit: int = 50) -> list[dict[str, Any]]:
        async with self.db.execute(
            "SELECT robot_id, command_id, command, goal, requested_at, outcome, detail, completed_at "
            "FROM command_audit ORDER BY id DESC LIMIT ?", (limit,)
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]

    # -- layouts -----------------------------------------------------------------

    async def get_layouts(self, user_id: int) -> list[dict[str, Any]]:
        async with self.db.execute(
            "SELECT name, is_preset, layout_json, updated_at FROM layouts "
            "WHERE user_id = ? OR user_id = 0 ORDER BY is_preset DESC, name", (user_id,)
        ) as cur:
            return [
                {"name": row["name"], "is_preset": bool(row["is_preset"]),
                 "layout": json.loads(row["layout_json"]), "updated_at": row["updated_at"]}
                for row in await cur.fetchall()
            ]

    async def get_layout(self, user_id: int, name: str) -> dict[str, Any] | None:
        async with self.db.execute(
            "SELECT name, is_preset, layout_json, updated_at FROM layouts "
            "WHERE name = ? AND (user_id = ? OR user_id = 0) ORDER BY user_id = 0 LIMIT 1",
            (name, user_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return {"name": row["name"], "is_preset": bool(row["is_preset"]),
                "layout": json.loads(row["layout_json"]), "updated_at": row["updated_at"]}

    async def save_layout(self, user_id: int, name: str, layout: dict[str, Any]) -> None:
        await self.db.execute(
            "INSERT INTO layouts (user_id, name, is_preset, layout_json, updated_at) "
            "VALUES (?, ?, 0, ?, datetime('now')) "
            "ON CONFLICT(user_id, name) DO UPDATE SET layout_json = excluded.layout_json, updated_at = datetime('now')",
            (user_id, name, json.dumps(layout)),
        )
        await self.db.commit()

    async def delete_layout(self, user_id: int, name: str) -> bool:
        cur = await self.db.execute(
            "DELETE FROM layouts WHERE user_id = ? AND name = ? AND is_preset = 0", (user_id, name)
        )
        await self.db.commit()
        return cur.rowcount > 0
