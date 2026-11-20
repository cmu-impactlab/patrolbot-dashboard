"""History and retention must remain bounded as the battery log grows."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.database.repo import (
    BATTERY_HISTORY_SQL, BATTERY_RETENTION_SQL, Database, _DAY_US, _timestamp_us,
)


@pytest.mark.parametrize("query,params", [
    (BATTERY_RETENTION_SQL, (_timestamp_us("2026-11-19T23:00:00Z") - 7 * _DAY_US,)),
    (BATTERY_HISTORY_SQL, (_timestamp_us("2026-11-19T21:00:00Z"), 2000)),
])
async def test_existing_history_uses_time_ranges_without_losing_rows(tmp_path, monkeypatch, query, params):
    path = tmp_path / "legacy.db"
    now = datetime(2026, 11, 19, 23, tzinfo=timezone.utc)
    monkeypatch.setattr("app.database.repo._now_us", lambda: _timestamp_us(now.isoformat()))
    recent = [("r1", (now - timedelta(minutes=10)).isoformat(), 24.0),
              ("r2", (now - timedelta(minutes=20)).isoformat(), 25.0)]
    rows = [("r1", (now - timedelta(days=8)).isoformat(), 23.0), *recent]
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE battery_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT, robot_id TEXT NOT NULL,
                ts TEXT NOT NULL, voltage REAL NOT NULL, percentage REAL,
                current REAL, charging INTEGER NOT NULL);
            CREATE INDEX idx_battery_ts ON battery_samples(robot_id, ts);
        """)
        connection.executemany(
            "INSERT INTO battery_samples (robot_id, ts, voltage, charging) VALUES (?,?,?,0)", rows)

    for _ in range(2):
        database = Database(str(path))
        await database.init()
        try:
            with sqlite3.connect(path) as connection:
                assert connection.execute(
                    "SELECT robot_id, ts, voltage FROM battery_samples ORDER BY id").fetchall() == rows
                normalized = connection.execute(
                    "SELECT ts_utc FROM battery_samples ORDER BY id").fetchall()
                assert normalized == [(_timestamp_us(row[1]),) for row in rows]
                plan = [row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + query, params)]
            assert any("SEARCH battery_samples" in step and "idx_battery_time_utc" in step for step in plan), plan
            assert not any("TEMP B-TREE" in step for step in plan), plan
            history = await database.get_battery_history()
            assert {row["voltage"] for row in history} == {24.0, 25.0}
        finally:
            await database.close()

    database = Database(str(path))
    await database.init()
    try:
        await database.add_battery_sample("r1", now.isoformat(), 26.0, None, None, False)
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM battery_samples").fetchone()[0] == 3
        assert {row["voltage"] for row in await database.get_battery_history()} == {24.0, 25.0, 26.0}
    finally:
        await database.close()
