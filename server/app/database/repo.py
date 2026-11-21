"""SQLite persistence via aiosqlite."""
from __future__ import annotations

import contextlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import aiosqlite

from ..protocol.messages import EventData
from .presets import PRESET_LAYOUTS

log = logging.getLogger("database")

# Rows fetched per round trip when walking a recording. Big enough that paging
# overhead is negligible, small enough that one page is never a memory problem
# even when every row is a lidar scan (~3 KB).
SAMPLE_PAGE = 2000
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MINUTE_US = 60 * 1_000_000
_DAY_US = 24 * 60 * _MINUTE_US


def _timestamp_us(value: str) -> int:
    """Exact UTC microseconds; legacy timezone-less SQLite dates mean UTC.

    Keep the original text for the API, but never compare it as a time. Integer
    arithmetic avoids float rounding at window/retention boundaries.
    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    delta = parsed.astimezone(timezone.utc) - _EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def _now_us() -> int:
    return _timestamp_us(datetime.now(timezone.utc).isoformat())


def _validate_retention(event_limit: int, battery_history_days: int) -> None:
    if event_limit < 1 or battery_history_days < 1:
        raise ValueError("event_limit and battery_history_days must be positive")


BATTERY_HISTORY_SQL = (
    "SELECT ts, voltage, percentage, current, charging FROM battery_samples "
    "WHERE ts_utc >= ? ORDER BY ts_utc DESC, id DESC LIMIT ?")
BATTERY_RETENTION_SQL = "DELETE FROM battery_samples WHERE ts_utc < ?"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    help_guide_version_seen INTEGER NOT NULL DEFAULT 0,
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
    ts_utc INTEGER,
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
CREATE INDEX IF NOT EXISTS idx_battery_time ON battery_samples(ts);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(robot_id, ts);
CREATE INDEX IF NOT EXISTS idx_samples_rec ON recording_samples(recording_id, id);
"""


class Database:
    def __init__(self, path: str, *, event_limit: int = 5000,
                 battery_history_days: int = 7) -> None:
        _validate_retention(event_limit, battery_history_days)
        self.path = path
        self.event_limit = event_limit
        self.battery_history_days = battery_history_days
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        # Existing accounts must see each newly versioned guide offer once.
        # Adding the column with a default preserves every row and marks the
        # current guide as unseen; CREATE TABLE above covers fresh databases.
        async with self._db.execute("PRAGMA table_info(users)") as cur:
            user_columns = {row[1] for row in await cur.fetchall()}
        if "help_guide_version_seen" not in user_columns:
            await self._db.execute(
                "ALTER TABLE users ADD COLUMN help_guide_version_seen "
                "INTEGER NOT NULL DEFAULT 0")
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
        await self._migrate_battery_timestamps()
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

    async def _migrate_battery_timestamps(self) -> None:
        """Add/backfill an indexed UTC key without rewriting or deleting data.

        A savepoint makes schema, backfill and index changes atomic even when
        another startup migration has already opened a transaction. Unknown
        legacy formats remain NULL: neither history nor retention may guess
        their age. Retrying startup is safe, including after a rollback.
        """
        await self.db.execute("SAVEPOINT battery_timestamp_migration")
        try:
            async with self.db.execute("PRAGMA table_info(battery_samples)") as cur:
                columns = {row[1] for row in await cur.fetchall()}
            if "ts_utc" not in columns:
                await self.db.execute("ALTER TABLE battery_samples ADD COLUMN ts_utc INTEGER")
            after_id = None
            invalid = 0
            while True:
                async with self.db.execute(
                    "SELECT id, ts FROM battery_samples WHERE ts_utc IS NULL "
                    + ("AND id > ? " if after_id is not None else "")
                    + "ORDER BY id LIMIT ?",
                    (after_id, SAMPLE_PAGE) if after_id is not None else (SAMPLE_PAGE,)
                ) as cur:
                    rows = await cur.fetchall()
                if not rows:
                    break
                updates = []
                for row in rows:
                    try:
                        updates.append((_timestamp_us(row["ts"]), row["id"]))
                    except (TypeError, ValueError, OverflowError):
                        invalid += 1
                await self.db.executemany(
                    "UPDATE battery_samples SET ts_utc = ? WHERE id = ?", updates)
                after_id = rows[-1]["id"]
            await self.db.execute(
                "CREATE INDEX IF NOT EXISTS idx_battery_time_utc ON battery_samples(ts_utc, id)")
            await self.db.execute("RELEASE SAVEPOINT battery_timestamp_migration")
        except BaseException:
            await self.db.execute("ROLLBACK TO SAVEPOINT battery_timestamp_migration")
            await self.db.execute("RELEASE SAVEPOINT battery_timestamp_migration")
            raise
        if invalid:
            log.warning("preserved %d battery sample(s) with unparseable timestamps; "
                        "excluded from time queries and retention", invalid)

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

    async def get_help_guide_version_seen(self, user_id: int) -> int:
        async with self.db.execute(
                "SELECT help_guide_version_seen FROM users WHERE id = ?",
                (user_id,)) as cur:
            row = await cur.fetchone()
        return int(row["help_guide_version_seen"]) if row is not None else 0

    async def mark_help_guide_seen(self, user_id: int, version: int) -> int:
        """Atomically advance a user's guide version, never move it backward."""
        await self.db.execute(
            "UPDATE users SET help_guide_version_seen = "
            "MAX(help_guide_version_seen, ?) WHERE id = ?",
            (version, user_id))
        await self.db.commit()
        return await self.get_help_guide_version_seen(user_id)

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
        # the newest ids" would delete a second robot's rows whenever
        # their ids happened to fall outside the window.
        await self.db.execute(
            "DELETE FROM events WHERE rowid NOT IN "
            "(SELECT rowid FROM events ORDER BY id DESC, robot_id DESC LIMIT ?)",
            (self.event_limit,),
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
        ts_utc = _timestamp_us(ts)
        await self.db.execute(
            "INSERT INTO battery_samples (robot_id, ts, ts_utc, voltage, percentage, current, charging) "
            "VALUES (?,?,?,?,?,?,?)",
            (robot_id, ts, ts_utc, voltage, percentage, current, int(charging)),
        )
        await self.db.execute(
            BATTERY_RETENTION_SQL,
            (_now_us() - self.battery_history_days * _DAY_US,),
        )
        await self.db.commit()

    async def get_battery_history(self, minutes: int = 120, limit: int = 2000) -> list[dict[str, Any]]:
        async with self.db.execute(
            BATTERY_HISTORY_SQL,
            (_now_us() - minutes * _MINUTE_US, limit),
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

    async def get_recording_samples(self, recording_id: int,
                                    kinds: list[str] | None = None,
                                    after_id: int = 0,
                                    limit: int = SAMPLE_PAGE) -> list[dict[str, Any]]:
        """One page of samples, oldest first.

        Keyset pagination on the sample id rather than OFFSET: offsets re-scan
        everything before them, which turns a walk over a large recording into
        quadratic work. `kinds` pushes the channel filter into SQL, so a replay
        that only draws pose and path never materializes the lidar scans that
        dominate a recording's size.
        """
        sql = ("SELECT id, ts, kind, data FROM recording_samples "
               "WHERE recording_id = ? AND id > ?")
        params: list[Any] = [recording_id, after_id]
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            params.extend(kinds)
        sql += " ORDER BY id LIMIT ?"
        params.append(limit)
        async with self.db.execute(sql, tuple(params)) as cur:
            return [dict(row) for row in await cur.fetchall()]

    async def iter_recording_samples(self, recording_id: int,
                                     kinds: list[str] | None = None,
                                     page: int = SAMPLE_PAGE) -> AsyncIterator[dict[str, Any]]:
        """Every sample, one page at a time. Yields to the event loop between
        pages, so exporting a large recording does not block telemetry."""
        after_id = 0
        while True:
            rows = await self.get_recording_samples(recording_id, kinds, after_id, page)
            if not rows:
                return
            for row in rows:
                yield row
            after_id = rows[-1]["id"]
            if len(rows) < page:
                return

    async def count_recording_samples(self, recording_id: int,
                                      kinds: list[str] | None = None) -> int:
        sql = "SELECT COUNT(*) FROM recording_samples WHERE recording_id = ?"
        params: list[Any] = [recording_id]
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            params.extend(kinds)
        async with self.db.execute(sql, tuple(params)) as cur:
            return int((await cur.fetchone())[0])

    async def recording_sample_kinds(self, recording_id: int) -> list[str]:
        """Which channels this recording actually holds samples for."""
        async with self.db.execute(
            "SELECT DISTINCT kind FROM recording_samples WHERE recording_id = ?",
            (recording_id,)
        ) as cur:
            return sorted(row[0] for row in await cur.fetchall())

    async def first_sample_ts(self, recording_id: int) -> str | None:
        """The recording's own time origin, without reading every sample."""
        async with self.db.execute(
            "SELECT MIN(ts) FROM recording_samples WHERE recording_id = ?",
            (recording_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None

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
