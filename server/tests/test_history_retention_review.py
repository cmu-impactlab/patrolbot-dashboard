"""R11: UTC windows, lossless migrations and configured persistence retention."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.database import create_database
from app.database import repo
from app.database.repo import Database, _timestamp_us
from app.protocol.messages import EventData
from app.settings import Settings


NOW = "2026-11-19T23:00:00.123456Z"


@pytest.fixture
async def database(monkeypatch):
    monkeypatch.setattr(repo, "_now_us", lambda: _timestamp_us(NOW))
    db = Database(":memory:")
    await db.init()
    try:
        yield db
    finally:
        await db.close()


@pytest.mark.parametrize("now,minutes,stamps,expected", [
    # Original same-day bug: midnight must not be in a two-hour evening window.
    ("2026-11-19T23:00:00Z", 120, [
        "2026-11-19T00:00:00Z", "2026-11-19T20:59:59.999999Z",
        "2026-11-19T21:00:00Z", "2026-11-19 22:00:00", "2026-11-19T23:00:00+00:00",
    ], [2, 3, 4]),
    # Cross midnight and normalize offsets crossing the UTC day boundary.
    ("2026-11-20T00:30:00Z", 60, [
        "2026-11-19T23:29:59.999999Z", "2026-11-20T01:30:00+02:00",
        "2026-11-19 23:45:00+00:00", "2026-11-19T19:00:00-05:00",
        "2026-11-20T00:30:00Z",
    ], [1, 2, 3, 4]),
    # Microseconds at both sides of an exact inclusive cutoff.
    (NOW, 1, [
        "2026-11-19T22:59:00.123455Z", "2026-11-19T22:59:00.123456+00:00",
        "2026-11-19 22:59:00.123457", NOW,
    ], [1, 2, 3]),
    # A zero-minute query includes an exactly current sample, not an older one.
    (NOW, 0, ["2026-11-19T23:00:00.123455Z", NOW], [1]),
])
async def test_history_utc_windows_preserve_api_text(database, monkeypatch, now, minutes, stamps, expected):
    monkeypatch.setattr(repo, "_now_us", lambda: _timestamp_us(now))
    # Reverse insertion order: ordering must be chronological, not insertion/text order.
    for i in reversed(range(len(stamps))):
        await database.add_battery_sample(f"r{i % 2}", stamps[i], float(i), None, -1.0, False)
    history = await database.get_battery_history(minutes=minutes)
    assert [row["voltage"] for row in history] == expected
    assert [row["ts"] for row in history] == [stamps[i] for i in expected]
    assert all(set(row) == {"ts", "voltage", "percentage", "current", "charging"} for row in history)
    limited = await database.get_battery_history(minutes=minutes, limit=2)
    assert limited == history[-2:]


async def test_equal_instants_have_deterministic_limit_order(database):
    for i, ts in enumerate([NOW, "2026-11-20T01:00:00.123456+02:00", "2026-11-19 23:00:00.123456"]):
        await database.add_battery_sample("r", ts, float(i), None, None, False)
    assert [r["voltage"] for r in await database.get_battery_history(limit=2)] == [1, 2]


@pytest.mark.parametrize("days", [1, 3, 7])
@pytest.mark.parametrize("now_text", [NOW, "2026-11-20T00:00:00Z"])
async def test_battery_retention_uses_configured_days_and_exact_boundary(monkeypatch, days, now_text):
    now = datetime.fromisoformat(now_text)
    monkeypatch.setattr(repo, "_now_us", lambda: _timestamp_us(now_text))
    cutoff = now - timedelta(days=days)
    db = create_database(Settings(database_path=":memory:", battery_history_days=days))
    await db.init()
    try:
        # All robots share the history bound. Offset spelling cannot save an old row.
        for i, instant in enumerate([cutoff - timedelta(microseconds=1), cutoff,
                                     cutoff + timedelta(microseconds=1), now]):
            ts = instant.astimezone(timezone(timedelta(hours=2))).isoformat()
            await db.add_battery_sample(f"r{i}", ts, float(i), None, None, False)
        async with db.db.execute("SELECT voltage FROM battery_samples ORDER BY id") as cur:
            assert [row[0] for row in await cur.fetchall()] == [1, 2, 3]
        assert [row["voltage"] for row in await db.get_battery_history(minutes=days * 1440)] == [1, 2, 3]
    finally:
        await db.close()


async def test_event_limit_is_global_and_uses_full_robot_key():
    db = create_database(Settings(database_path=":memory:", event_limit=3))
    await db.init()
    try:
        for robot, event_id in [("r1", 1), ("r2", 1), ("r1", 2), ("r2", 2), ("r1", 3)]:
            await db.add_event(robot, EventData(id=event_id, ts=NOW, severity="info", title="t", message="m"))
        rows = await db.get_events()
        assert {(r["robot_id"], r["id"]) for r in rows} == {("r1", 2), ("r2", 2), ("r1", 3)}
        assert await db.next_event_id() == 4
    finally:
        await db.close()


@pytest.mark.parametrize("field", ["event_limit", "battery_history_days"])
@pytest.mark.parametrize("value", [0, -1])
def test_retention_rejects_nonpositive_settings(field, value):
    with pytest.raises(ValidationError):
        Settings(**{field: value})
    with pytest.raises(ValueError):
        Database(":memory:", **{field: value})


def test_direct_database_defaults_remain_compatible():
    db = Database(":memory:")
    assert (db.event_limit, db.battery_history_days) == (5000, 7)


def test_retention_environment_settings_reach_factory(monkeypatch):
    monkeypatch.setenv("PATROLBOT_EVENT_LIMIT", "12")
    monkeypatch.setenv("PATROLBOT_BATTERY_HISTORY_DAYS", "4")
    db = create_database(Settings(database_path=":memory:", database_url=""))
    assert (db.event_limit, db.battery_history_days) == (12, 4)


@pytest.mark.parametrize("text,expected", [
    ("1970-01-01T00:00:00Z", 0),
    ("1970-01-01 00:00:00+00:00", 0),
    ("1970-01-01T02:00:00+02:00", 0),
    ("1969-12-31T19:00:00-05:00", 0),
    ("1969-12-31 23:59:59.999999", -1),
    ("1970-01-01T00:00:00.000001Z", 1),
])
def test_utc_key_is_exact_including_pre_epoch_dates(text, expected):
    assert _timestamp_us(text) == expected


LEGACY_SCHEMA = """
CREATE TABLE battery_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT, robot_id TEXT NOT NULL, ts TEXT NOT NULL,
    voltage REAL NOT NULL, percentage REAL, current REAL, charging INTEGER NOT NULL);
CREATE INDEX idx_battery_ts ON battery_samples(robot_id, ts);
"""


async def test_paged_migration_is_lossless_idempotent_and_keeps_unknown_dates(tmp_path, monkeypatch, caplog):
    path = tmp_path / "mixed.db"
    monkeypatch.setattr(repo, "SAMPLE_PAGE", 2)
    monkeypatch.setattr(repo, "_now_us", lambda: _timestamp_us(NOW))
    stamps = [
        "2026-11-19T23:00:00.123456Z", "not a timestamp",
        "2026-11-20T01:00:00.123456+02:00", "2026-11-19 23:00:00.123456",
        "2026-11-19T23:00:00.123456+00:00", "2026-11-01T00:00:00Z",
    ]
    # Include explicitly allocated zero/negative ids as well as normal legacy ids.
    rows = [(i - 2, f"r{i}", ts, 20.0 + i, 50.0, -1.0, i % 2) for i, ts in enumerate(stamps)]
    with sqlite3.connect(path) as conn:
        conn.executescript(LEGACY_SCHEMA)
        conn.executemany("INSERT INTO battery_samples VALUES (?,?,?,?,?,?,?)", rows)
    for _ in range(2):
        db = Database(str(path))
        await db.init()
        try:
            async with db.db.execute(
                "SELECT id, robot_id, ts, voltage, percentage, current, charging, ts_utc "
                "FROM battery_samples ORDER BY id"
            ) as cur:
                migrated = [tuple(row) for row in await cur.fetchall()]
            assert [row[:-1] for row in migrated] == rows
            assert [row[-1] for row in migrated] == [
                None if ts == "not a timestamp" else _timestamp_us(ts) for ts in stamps]
            assert len(await db.get_battery_history()) == 4
        finally:
            await db.close()
    assert "unparseable timestamps" in caplog.text
    db = Database(str(path))
    await db.init()
    try:
        await db.add_battery_sample("new", NOW, 30.0, None, None, False)
        async with db.db.execute("SELECT ts FROM battery_samples ORDER BY id") as cur:
            retained = [row[0] for row in await cur.fetchall()]
        assert retained == stamps[:-1] + [NOW]  # unknown row kept, known expired row pruned
    finally:
        await db.close()


async def test_failed_migration_rolls_back_schema_and_partial_updates(tmp_path):
    path = tmp_path / "rollback.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(LEGACY_SCHEMA)
        conn.executemany(
            "INSERT INTO battery_samples (robot_id, ts, voltage, charging) VALUES ('r',?,24,0)",
            [(NOW,), ("2026-11-19 22:00:00",)])
        conn.executescript("""
            CREATE TRIGGER fail_backfill BEFORE UPDATE ON battery_samples
            WHEN NEW.id = 2 BEGIN SELECT RAISE(ABORT, 'injected failure'); END;
        """)
    db = Database(str(path))
    try:
        with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
            await db.init()
    finally:
        await db.close()
    with sqlite3.connect(path) as conn:
        assert "ts_utc" not in {row[1] for row in conn.execute("PRAGMA table_info(battery_samples)")}
        assert conn.execute("SELECT ts FROM battery_samples ORDER BY id").fetchall() == [
            (NOW,), ("2026-11-19 22:00:00",)]
        conn.execute("DROP TRIGGER fail_backfill")
    db = Database(str(path))
    await db.init()
    try:
        async with db.db.execute("SELECT ts_utc FROM battery_samples ORDER BY id") as cur:
            assert [row[0] for row in await cur.fetchall()] == [
                _timestamp_us(NOW), _timestamp_us("2026-11-19 22:00:00")]
    finally:
        await db.close()


async def test_restart_backfills_rows_written_by_older_binary(tmp_path, monkeypatch):
    path = str(tmp_path / "rollback-compatible.db")
    monkeypatch.setattr(repo, "_now_us", lambda: _timestamp_us(NOW))
    db = Database(path)
    await db.init()
    await db.close()
    # A rolled-back older binary knows only the original ts column.
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO battery_samples (robot_id, ts, voltage, charging) VALUES ('r',?,24,0)",
            (NOW,))
    db = Database(path)
    await db.init()
    try:
        assert (await db.get_battery_history())[0]["ts"] == NOW
        async with db.db.execute("SELECT ts_utc FROM battery_samples") as cur:
            assert (await cur.fetchone())[0] == _timestamp_us(NOW)
    finally:
        await db.close()


async def test_invalid_new_timestamp_is_rejected_without_mutation(database):
    with pytest.raises(ValueError):
        await database.add_battery_sample("r", "invalid", 24, None, None, False)
    async with database.db.execute("SELECT COUNT(*) FROM battery_samples") as cur:
        assert (await cur.fetchone())[0] == 0
