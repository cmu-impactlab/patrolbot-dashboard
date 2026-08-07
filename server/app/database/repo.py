"""SQLite persistence via aiosqlite.

Deliberately a thin SQL repository: swapping to PostgreSQL at Phase 5 means
reimplementing this module only. (SQLAlchemy async was skipped because the
dev machine runs Python 3.14 where greenlet wheels are a moving target.)
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
from typing import Any

import aiosqlite

from ..protocol.messages import EventData
from .presets import PRESET_LAYOUTS

log = logging.getLogger("database")

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
    id INTEGER NOT NULL,
    robot_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    -- Keyed by robot as well as id. Ids are allocated globally by the hub
    -- now, but the key must not depend on that: a restart re-seeds from
    -- MAX(id), and two robots whose sessions were seeded independently used
    -- to overwrite each other's rows here.
    PRIMARY KEY (robot_id, id)
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
    completed_at TEXT,
    user_id INTEGER,
    username TEXT,
    source_ip TEXT,
    session_generation INTEGER,
    rejection_reason TEXT
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
CREATE TABLE IF NOT EXISTS robot_state (
    robot_id TEXT PRIMARY KEY,
    last_pose TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
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
        await self._migrate_events_primary_key()
        # Migration for command_audit identity columns (added during hardening).
        async with self._db.execute("PRAGMA table_info(command_audit)") as cur:
            audit_columns = {row[1] for row in await cur.fetchall()}
        for column, decl in (("user_id", "INTEGER"), ("username", "TEXT"),
                             ("source_ip", "TEXT"), ("session_generation", "INTEGER"),
                             ("rejection_reason", "TEXT")):
            if column not in audit_columns:
                await self._db.execute(
                    f"ALTER TABLE command_audit ADD COLUMN {column} {decl}")
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

    async def _migrate_events_primary_key(self) -> None:
        """Rebuild events with PRIMARY KEY (robot_id, id) if it predates it.

        SQLite cannot ALTER a primary key, so the table is recreated and
        copied. Existing rows cannot conflict: the old key was id alone, so
        (robot_id, id) is at least as unique.

        This runs against a live deployment's database, so it is all-or-
        nothing: one explicit transaction, a row count checked against what was
        there before the swap, and a rollback that leaves the original table
        untouched if anything at all disagrees. `executescript` is deliberately
        not used — it COMMITs before running, which would have made a failure
        halfway through unrecoverable.

        The collision this defends against is already prevented upstream by the
        hub's shared EventIdAllocator, so nothing depends on this migration
        succeeding; a database that fails it keeps working with the old key.
        """
        async with self._db.execute("PRAGMA table_info(events)") as cur:
            columns = list(await cur.fetchall())
        if not columns:
            return
        key_columns = {row[1] for row in columns if row[5]}  # row[5] = pk position
        if key_columns == {"robot_id", "id"}:
            return

        async with self._db.execute("SELECT COUNT(*) FROM events") as cur:
            before = int((await cur.fetchone())[0])
        log.info("migrating %d event row(s) to a (robot_id, id) primary key", before)
        try:
            await self._db.execute("BEGIN IMMEDIATE")
            await self._db.execute("""
                CREATE TABLE events_migrated (
                    id INTEGER NOT NULL,
                    robot_id TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    PRIMARY KEY (robot_id, id)
                )""")
            await self._db.execute(
                "INSERT INTO events_migrated (id, robot_id, ts, severity, title, message) "
                "SELECT id, robot_id, ts, severity, title, message FROM events")
            async with self._db.execute("SELECT COUNT(*) FROM events_migrated") as cur:
                copied = int((await cur.fetchone())[0])
            if copied != before:
                raise RuntimeError(
                    f"events migration copied {copied} of {before} rows; rolling back")
            # Only now is the original expendable. Dropping it also drops
            # idx_events_ts, which is recreated below.
            await self._db.execute("DROP TABLE events")
            await self._db.execute("ALTER TABLE events_migrated RENAME TO events")
            await self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_ts ON events(robot_id, ts)")
            await self._db.commit()
            log.info("events migration complete (%d rows)", copied)
        except Exception:
            await self._db.rollback()
            with contextlib.suppress(Exception):
                await self._db.execute("DROP TABLE IF EXISTS events_migrated")
                await self._db.commit()
            log.exception("events primary-key migration failed; the existing table "
                          "is unchanged and the server will continue with it")

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
        # Retention by rowid, not by id: with more than one robot, "id not in
        # the newest 5000 ids" would delete a second robot's rows whenever
        # their ids happened to fall outside the window.
        await self.db.execute(
            "DELETE FROM events WHERE rowid NOT IN "
            "(SELECT rowid FROM events ORDER BY id DESC LIMIT 5000)"
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
                                goal: dict[str, Any] | None, user_id: int | None = None,
                                username: str | None = None, source_ip: str | None = None,
                                session_generation: int | None = None) -> None:
        await self.db.execute(
            "INSERT OR IGNORE INTO command_audit "
            "(robot_id, command_id, command, goal, user_id, username, source_ip, session_generation) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (robot_id, command_id, command, json.dumps(goal) if goal else None,
             user_id, username, source_ip, session_generation),
        )
        await self.db.commit()

    async def add_command_rejection(self, robot_id: str, command_id: str, command: str,
                                    reason: str, user_id: int | None = None,
                                    username: str | None = None, source_ip: str | None = None,
                                    session_generation: int | None = None) -> None:
        """Record a rejected command attempt. INSERT OR IGNORE so a replayed
        command_id (already audited) does not create a second row."""
        await self.db.execute(
            "INSERT OR IGNORE INTO command_audit "
            "(robot_id, command_id, command, outcome, detail, rejection_reason, completed_at, "
            " user_id, username, source_ip, session_generation) "
            "VALUES (?,?,?, 'rejected', ?, ?, datetime('now'), ?,?,?,?)",
            (robot_id, command_id, command, reason, reason,
             user_id, username, source_ip, session_generation),
        )
        await self.db.commit()

    async def command_recorded(self, command_id: str) -> bool:
        """True if this command_id already exists — restart-persistent duplicate
        protection that survives the in-memory cache being cleared."""
        async with self.db.execute(
            "SELECT 1 FROM command_audit WHERE command_id = ? LIMIT 1", (command_id,)
        ) as cur:
            return await cur.fetchone() is not None

    async def complete_command_audit(self, command_id: str, outcome: str, detail: str) -> None:
        await self.db.execute(
            "UPDATE command_audit SET outcome = ?, detail = ?, completed_at = datetime('now') "
            "WHERE command_id = ?",
            (outcome, detail, command_id),
        )
        await self.db.commit()

    async def get_command_audit(self, limit: int = 50) -> list[dict[str, Any]]:
        async with self.db.execute(
            "SELECT robot_id, command_id, command, goal, requested_at, outcome, detail, "
            "completed_at, user_id, username, source_ip, session_generation, rejection_reason "
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

    # -- robot state (last-known pose) -------------------------------------------

    async def save_last_pose(self, robot_id: str, pose: dict[str, Any]) -> None:
        await self.db.execute(
            "INSERT INTO robot_state (robot_id, last_pose, updated_at) "
            "VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(robot_id) DO UPDATE SET last_pose = excluded.last_pose, "
            "updated_at = datetime('now')",
            (robot_id, json.dumps(pose)),
        )
        await self.db.commit()

    async def get_last_pose(self, robot_id: str) -> dict[str, Any] | None:
        async with self.db.execute(
            "SELECT last_pose FROM robot_state WHERE robot_id = ?", (robot_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None or row["last_pose"] is None:
            return None
        return json.loads(row["last_pose"])
