"""PostgreSQL persistence via asyncpg (Phase 5).

Mirrors the public interface of `repo.Database` exactly; `create_database`
in this package picks the backend from the connection string. SQLite stays
the default for single-host deployments.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg

from ..protocol.messages import EventData
from .presets import PRESET_LAYOUTS

log = logging.getLogger("database.pg")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS layouts (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    name TEXT NOT NULL,
    is_preset INTEGER NOT NULL DEFAULT 0,
    layout_json TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, name)
);
CREATE TABLE IF NOT EXISTS events (
    id BIGINT PRIMARY KEY,
    robot_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS battery_samples (
    id BIGSERIAL PRIMARY KEY,
    robot_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    voltage DOUBLE PRECISION NOT NULL,
    percentage DOUBLE PRECISION,
    current DOUBLE PRECISION,
    charging INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS recordings (
    id SERIAL PRIMARY KEY,
    robot_id TEXT NOT NULL,
    name TEXT NOT NULL,
    channels TEXT NOT NULL DEFAULT '["pose","battery","event"]',
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'recording',
    sample_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS recording_samples (
    id BIGSERIAL PRIMARY KEY,
    recording_id INTEGER NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS command_audit (
    id BIGSERIAL PRIMARY KEY,
    robot_id TEXT NOT NULL,
    command_id TEXT NOT NULL UNIQUE,
    command TEXT NOT NULL,
    goal TEXT,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    outcome TEXT,
    detail TEXT,
    completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_battery_ts ON battery_samples(robot_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(robot_id, ts);
CREATE INDEX IF NOT EXISTS idx_samples_rec ON recording_samples(recording_id, id);
"""


def _iso(value: Any) -> Any:
    """Timestamps come back as datetime from asyncpg; the API serves strings."""
    return value.isoformat(sep=" ", timespec="seconds") if hasattr(value, "isoformat") else value


class PostgresDatabase:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def init(self) -> None:
        self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5)
        async with self._pool.acquire() as conn:
            await conn.execute(SCHEMA)
            await conn.execute(
                "INSERT INTO users (id, username, display_name) VALUES (1, 'local', 'Local Operator') "
                "ON CONFLICT (id) DO NOTHING")
            await conn.execute(
                "INSERT INTO users (id, username, display_name) VALUES (0, '_presets', 'Preset Owner') "
                "ON CONFLICT (id) DO NOTHING")
            for name, layout in PRESET_LAYOUTS.items():
                await conn.execute(
                    "INSERT INTO layouts (user_id, name, is_preset, layout_json) VALUES (0, $1, 1, $2) "
                    "ON CONFLICT (user_id, name) DO UPDATE SET layout_json = excluded.layout_json",
                    name, json.dumps(layout))
        log.info("connected to PostgreSQL")

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @property
    def pool(self) -> asyncpg.Pool:
        assert self._pool is not None, "Database not initialized"
        return self._pool

    # -- users ----------------------------------------------------------------

    async def get_or_create_user(self, username: str, display_name: str) -> int:
        row = await self.pool.fetchrow(
            "INSERT INTO users (id, username, display_name) "
            "VALUES ((SELECT COALESCE(MAX(id), 1) + 1 FROM users), $1, $2) "
            "ON CONFLICT (username) DO UPDATE SET display_name = excluded.display_name "
            "RETURNING id",
            username, display_name)
        return int(row["id"])

    # -- events ---------------------------------------------------------------

    async def next_event_id(self) -> int:
        row = await self.pool.fetchrow("SELECT COALESCE(MAX(id), 0) + 1 AS next FROM events")
        return int(row["next"])

    async def add_event(self, robot_id: str, event: EventData) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO events (id, robot_id, ts, severity, title, message) "
                "VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (id) DO UPDATE SET "
                "robot_id=excluded.robot_id, ts=excluded.ts, severity=excluded.severity, "
                "title=excluded.title, message=excluded.message",
                event.id, robot_id, event.ts, event.severity, event.title, event.message)
            await conn.execute(
                "DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 5000)")

    async def get_events(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            "SELECT id, robot_id, ts, severity, title, message FROM events ORDER BY id DESC LIMIT $1",
            limit)
        return [dict(row) for row in rows]

    # -- battery history --------------------------------------------------------

    async def add_battery_sample(self, robot_id: str, ts: str, voltage: float,
                                 percentage: float | None, current: float | None, charging: bool) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO battery_samples (robot_id, ts, voltage, percentage, current, charging) "
                "VALUES ($1,$2,$3,$4,$5,$6)",
                robot_id, ts, voltage, percentage, current, int(charging))
            await conn.execute(
                "DELETE FROM battery_samples WHERE ts < to_char(now() - interval '7 days', 'YYYY-MM-DD\"T\"HH24:MI:SS')")

    async def get_battery_history(self, minutes: int = 120, limit: int = 2000) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            "SELECT ts, voltage, percentage, current, charging FROM battery_samples "
            "WHERE ts >= to_char(now() - make_interval(mins => $1), 'YYYY-MM-DD\"T\"HH24:MI:SS') "
            "ORDER BY ts DESC LIMIT $2",
            minutes, limit)
        return [dict(row) for row in reversed(rows)]

    # -- recordings --------------------------------------------------------------

    async def start_recording(self, robot_id: str, name: str,
                              channels: list[str]) -> dict[str, Any] | None:
        async with self.pool.acquire() as conn:
            active = await conn.fetchrow("SELECT id FROM recordings WHERE status = 'recording'")
            if active is not None:
                return None
            row = await conn.fetchrow(
                "INSERT INTO recordings (robot_id, name, channels) VALUES ($1,$2,$3) RETURNING id",
                robot_id, name, json.dumps(channels))
        return await self.get_recording(row["id"])

    async def stop_recording(self, recording_id: int) -> bool:
        result = await self.pool.execute(
            "UPDATE recordings SET status = 'done', ended_at = now() "
            "WHERE id = $1 AND status = 'recording'", recording_id)
        return result.endswith("1")

    async def add_recording_sample(self, recording_id: int, ts: str, kind: str, data: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO recording_samples (recording_id, ts, kind, data) VALUES ($1,$2,$3,$4)",
                recording_id, ts, kind, data)
            await conn.execute(
                "UPDATE recordings SET sample_count = sample_count + 1 WHERE id = $1", recording_id)

    async def list_recordings(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            "SELECT id, robot_id, name, channels, started_at, ended_at, status, sample_count "
            "FROM recordings ORDER BY id DESC LIMIT $1", limit)
        return [self._recording_row(row) for row in rows]

    async def get_recording(self, recording_id: int) -> dict[str, Any] | None:
        row = await self.pool.fetchrow(
            "SELECT id, robot_id, name, channels, started_at, ended_at, status, sample_count "
            "FROM recordings WHERE id = $1", recording_id)
        return self._recording_row(row) if row else None

    @staticmethod
    def _recording_row(row: Any) -> dict[str, Any]:
        out = dict(row)
        out["channels"] = json.loads(out["channels"])
        out["started_at"] = _iso(out["started_at"])
        out["ended_at"] = _iso(out["ended_at"])
        return out

    async def get_recording_samples(self, recording_id: int) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            "SELECT ts, kind, data FROM recording_samples WHERE recording_id = $1 ORDER BY id",
            recording_id)
        return [dict(row) for row in rows]

    async def delete_recording(self, recording_id: int) -> bool:
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM recording_samples WHERE recording_id = $1", recording_id)
            result = await conn.execute("DELETE FROM recordings WHERE id = $1", recording_id)
        return result.endswith("1")

    # -- command audit -----------------------------------------------------------

    async def add_command_audit(self, robot_id: str, command_id: str, command: str,
                                goal: dict[str, Any] | None) -> None:
        await self.pool.execute(
            "INSERT INTO command_audit (robot_id, command_id, command, goal) VALUES ($1,$2,$3,$4) "
            "ON CONFLICT (command_id) DO NOTHING",
            robot_id, command_id, command, json.dumps(goal) if goal else None)

    async def complete_command_audit(self, command_id: str, outcome: str, detail: str) -> None:
        await self.pool.execute(
            "UPDATE command_audit SET outcome = $1, detail = $2, completed_at = now() "
            "WHERE command_id = $3", outcome, detail, command_id)

    async def get_command_audit(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            "SELECT robot_id, command_id, command, goal, requested_at, outcome, detail, completed_at "
            "FROM command_audit ORDER BY id DESC LIMIT $1", limit)
        return [{**dict(row),
                 "requested_at": _iso(row["requested_at"]),
                 "completed_at": _iso(row["completed_at"])} for row in rows]

    # -- layouts -----------------------------------------------------------------

    async def get_layouts(self, user_id: int) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            "SELECT name, is_preset, layout_json, updated_at FROM layouts "
            "WHERE user_id = $1 OR user_id = 0 ORDER BY is_preset DESC, name", user_id)
        return [
            {"name": row["name"], "is_preset": bool(row["is_preset"]),
             "layout": json.loads(row["layout_json"]), "updated_at": _iso(row["updated_at"])}
            for row in rows
        ]

    async def get_layout(self, user_id: int, name: str) -> dict[str, Any] | None:
        row = await self.pool.fetchrow(
            "SELECT name, is_preset, layout_json, updated_at FROM layouts "
            "WHERE name = $1 AND (user_id = $2 OR user_id = 0) ORDER BY (user_id = 0) LIMIT 1",
            name, user_id)
        if row is None:
            return None
        return {"name": row["name"], "is_preset": bool(row["is_preset"]),
                "layout": json.loads(row["layout_json"]), "updated_at": _iso(row["updated_at"])}

    async def save_layout(self, user_id: int, name: str, layout: dict[str, Any]) -> None:
        await self.pool.execute(
            "INSERT INTO layouts (user_id, name, is_preset, layout_json, updated_at) "
            "VALUES ($1, $2, 0, $3, now()) "
            "ON CONFLICT (user_id, name) DO UPDATE SET layout_json = excluded.layout_json, updated_at = now()",
            user_id, name, json.dumps(layout))

    async def delete_layout(self, user_id: int, name: str) -> bool:
        result = await self.pool.execute(
            "DELETE FROM layouts WHERE user_id = $1 AND name = $2 AND is_preset = 0", user_id, name)
        return result.endswith("1")
