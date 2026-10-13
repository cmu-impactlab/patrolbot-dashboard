"""History and retention must remain bounded as the battery log grows."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.database.repo import Database


@pytest.mark.parametrize("query", [
    "DELETE FROM battery_samples WHERE ts < datetime('now', '-7 days')",
    "SELECT ts, voltage, percentage, current, charging FROM battery_samples "
    "WHERE ts >= datetime('now', '-120 minutes') ORDER BY ts DESC LIMIT 2000",
])
async def test_existing_history_uses_time_ranges_without_losing_rows(tmp_path, query):
    path = tmp_path / "legacy.db"
    now = datetime.now(timezone.utc)
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
                plan = [row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + query)]
            assert any("SEARCH battery_samples" in step and "ts" in step for step in plan), plan
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
