"""PostgreSQL backend tests — run only when a test database is provided:

    PATROLBOT_TEST_PG_URL=postgresql://user:pass@127.0.0.1/db pytest tests/test_postgres.py

CI and local runs without Postgres skip this module.
"""
import json
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.protocol.envelope import encode
from app.protocol.messages import EventData
from app.settings import Settings

PG_URL = os.environ.get("PATROLBOT_TEST_PG_URL", "")

# `external`: excluded from the default hermetic `make test-server` run, which
# uses -m "not external". Also skipped entirely when no test DB is provided.
pytestmark = [
    pytest.mark.external,
    pytest.mark.skipif(not PG_URL, reason="PATROLBOT_TEST_PG_URL not set"),
]


async def _wipe(db) -> None:
    async with db.pool.acquire() as conn:
        for table in ("recording_samples", "recordings", "command_audit",
                      "battery_samples", "events", "layouts", "users", "robot_state"):
            await conn.execute(f"DELETE FROM {table}")


async def test_repo_round_trips():
    from app.database.pg import PostgresDatabase

    db = PostgresDatabase(PG_URL)
    await db.init()
    await _wipe(db)
    await db.close()

    db = PostgresDatabase(PG_URL)
    await db.init()  # re-seeds presets/users after the wipe
    try:
        layouts = await db.get_layouts(1)
        assert {"Operator", "Research", "Diagnostics"} <= {l["name"] for l in layouts}

        await db.save_layout(1, "current", {"widgets": ["liveMap"], "layouts": {}})
        fetched = await db.get_layout(1, "current")
        assert fetched["layout"]["widgets"] == ["liveMap"]
        assert await db.delete_layout(1, "current") is True
        assert await db.delete_layout(1, "Operator") is False  # presets protected

        await db.add_event("r1", EventData(id=1, ts="2026-07-17T00:00:00Z", severity="info",
                                           title="t", message="m"))
        events = await db.get_events()
        assert events[0]["title"] == "t"
        assert await db.next_event_id() == 2

        rec = await db.start_recording("r1", "pg test", ["pose", "event"])
        assert rec["status"] == "recording" and rec["channels"] == ["pose", "event"]
        assert await db.start_recording("r1", "second", ["pose"]) is None
        await db.add_recording_sample(rec["id"], "2026-07-17T00:00:01Z", "pose",
                                      json.dumps({"x": 1.0, "y": 2.0}))
        assert await db.stop_recording(rec["id"]) is True
        detail = await db.get_recording(rec["id"])
        assert detail["status"] == "done" and detail["sample_count"] == 1
        samples = await db.get_recording_samples(rec["id"])
        assert json.loads(samples[0]["data"])["x"] == 1.0
        assert await db.delete_recording(rec["id"]) is True

        command_id = str(uuid.uuid4())
        await db.add_command_audit("r1", command_id, "stop", None)
        await db.complete_command_audit(command_id, "succeeded", "done")
        audit = await db.get_command_audit()
        assert audit[0]["outcome"] == "succeeded"

        await db.add_battery_sample("r1", "2026-07-17T00:00:00Z", 24.5, 80.0, -1.5, False)

        assert await db.get_last_pose("r1") is None
        await db.save_last_pose("r1", {"x": 1.5, "y": -2.0, "yaw": 0.5})
        assert await db.get_last_pose("r1") == {"x": 1.5, "y": -2.0, "yaw": 0.5}
        await db.save_last_pose("r1", {"x": 3.0, "y": 4.0, "yaw": None})  # upsert
        assert await db.get_last_pose("r1") == {"x": 3.0, "y": 4.0, "yaw": None}
    finally:
        await db.close()


def test_full_app_against_postgres(tmp_path):
    settings = Settings(robot_token="test-token", database_url=PG_URL,
                        database_path=str(tmp_path / "unused.db"))
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/health").status_code == 200
        names = [l["name"] for l in client.get("/api/layouts").json()]
        assert "Operator" in names

        with client.websocket_connect("/ws/robot?token=test-token") as robot:
            robot.send_text(encode("robot.hello", "patrolbot-01", 0, {
                "protocol_version": 1, "capabilities": ["pose"], "map_version": 0,
                "software_version": "test"}))
            robot.receive_text()
            assert client.post("/api/recordings/start",
                               json={"name": "pg e2e", "channels": ["pose"]}).status_code == 200
            assert client.post("/api/recordings/stop").status_code == 200
        rows = client.get("/api/recordings").json()
        assert rows[0]["name"] == "pg e2e"
