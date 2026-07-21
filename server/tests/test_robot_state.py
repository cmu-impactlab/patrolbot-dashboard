"""Last-known-pose persistence + restore-on-reconnect snapshot field."""
import json

import pytest
from fastapi.testclient import TestClient

from app.database.repo import Database
from app.main import create_app
from app.protocol.envelope import encode
from app.settings import Settings


@pytest.fixture()
def client(tmp_path):
    settings = Settings(robot_token="test-token", database_path=str(tmp_path / "test.db"))
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def hello_frame() -> str:
    return encode("robot.hello", "patrolbot-01", 0, {
        "protocol_version": 1, "capabilities": ["pose"], "map_version": 0,
        "software_version": "test",
    })


async def test_last_pose_sqlite_round_trip(tmp_path):
    db = Database(str(tmp_path / "state.db"))
    await db.init()
    try:
        assert await db.get_last_pose("patrolbot-01") is None
        await db.save_last_pose("patrolbot-01", {"x": 1.0, "y": 2.0, "yaw": 0.5})
        assert await db.get_last_pose("patrolbot-01") == {"x": 1.0, "y": 2.0, "yaw": 0.5}
        # Upsert overwrites in place, keyed by robot_id.
        await db.save_last_pose("patrolbot-01", {"x": 9.0, "y": 8.0, "yaw": None})
        assert await db.get_last_pose("patrolbot-01") == {"x": 9.0, "y": 8.0, "yaw": None}
    finally:
        await db.close()


def test_disconnect_persists_pose_and_snapshot_offers_it(client):
    # Connect the robot, report a pose, then disconnect.
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame())
        robot.receive_text()
        robot.send_text(encode("telemetry.pose", "patrolbot-01", 1, {
            "x": 3.25, "y": -1.5, "yaw": 0.75, "linear_velocity": 0.0, "angular_velocity": 0.0,
        }))
    # The robot has now disconnected; a fresh browser snapshot should carry the
    # last-known pose so the UI can offer to resume from it.
    with client.websocket_connect("/ws/ui") as ui:
        snapshot = json.loads(ui.receive_text())
        assert snapshot["type"] == "server.snapshot"
        last = snapshot["data"]["last_known_pose"]
        assert last == {"x": 3.25, "y": -1.5, "yaw": 0.75}


def test_snapshot_without_saved_pose_is_null(client):
    with client.websocket_connect("/ws/ui") as ui:
        snapshot = json.loads(ui.receive_text())
        assert snapshot["data"]["last_known_pose"] is None
