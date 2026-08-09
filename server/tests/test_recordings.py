import json
import time

import pytest
from fastapi.testclient import TestClient

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


def base_state_frame(seq: int) -> str:
    """A healthy drive base. The status pill refuses to characterise a robot it
    has no current drive-base data for, so a robot that only says hello reads
    as needing attention rather than as ready/recording."""
    return encode("telemetry.base_state", "patrolbot-01", seq, {
        "session_generation": 1, "link_connected": True, "telemetry_age": 0.1,
        "hardware_state_valid": True, "charge_state": "idle", "motors_enabled": True,
        "estop_pressed": False, "fault_flags": 0, "stall_value": 0,
        "bumpers_front": False, "bumpers_rear": False, "bumpers_valid": True,
    })


def pose_frame(seq: int, x: float) -> str:
    return encode("telemetry.pose", "patrolbot-01", seq, {
        "x": x, "y": 0.0, "yaw": 0.0, "linear_velocity": 0.3, "angular_velocity": 0.0,
    })


def test_recording_lifecycle(client):
    # No robot -> cannot start.
    assert client.post("/api/recordings/start", json={"name": "x"}).status_code == 409

    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame())
        robot.receive_text()
        robot.send_text(base_state_frame(1))

        started = client.post("/api/recordings/start", json={"name": "Patrol A"})
        assert started.status_code == 200
        rec_id = started.json()["id"]
        assert started.json()["status"] == "recording"

        # Second start is refused while one is active.
        assert client.post("/api/recordings/start", json={"name": "y"}).status_code == 409

        # Robot status now reports "recording".
        snap = client.get("/api/snapshot").json()
        assert snap["robot_status"]["status"] == "recording"

        # Stream some poses; decimation keeps at most ~2/s but the first lands.
        for i in range(5):
            robot.send_text(pose_frame(i + 1, float(i)))
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if any(s["kind"] == "pose"
                   for s in client.get(f"/api/recordings/{rec_id}").json()["samples"]):
                break
            time.sleep(0.05)

        stopped = client.post("/api/recordings/stop")
        assert stopped.status_code == 200
        assert stopped.json()["status"] == "done"
        assert client.post("/api/recordings/stop").status_code == 409

    detail = client.get(f"/api/recordings/{rec_id}").json()
    kinds = {s["kind"] for s in detail["samples"]}
    assert "pose" in kinds
    assert "event" in kinds  # the "Recording started" event itself is captured
    pose_samples = [s for s in detail["samples"] if s["kind"] == "pose"]
    assert 1 <= len(pose_samples) <= 2  # 5 poses in a burst -> decimated

    csv = client.get(f"/api/recordings/{rec_id}/export.csv")
    assert csv.status_code == 200
    assert csv.text.splitlines()[0].startswith("ts,kind,x,y")
    assert any(",pose," in line for line in csv.text.splitlines())

    assert client.delete(f"/api/recordings/{rec_id}").status_code == 200
    assert client.get(f"/api/recordings/{rec_id}").status_code == 404


def test_open_recording_recovered_on_restart(tmp_path):
    settings = Settings(robot_token="test-token", database_path=str(tmp_path / "test.db"))
    with TestClient(create_app(settings)) as client:
        with client.websocket_connect("/ws/robot?token=test-token") as robot:
            robot.send_text(hello_frame())
            robot.receive_text()
            assert client.post("/api/recordings/start", json={"name": "left open"}).status_code == 200
        # Exit without stopping: the row stays status='recording' in the DB.

    with TestClient(create_app(settings)) as client:
        rows = client.get("/api/recordings").json()
        assert rows[0]["status"] == "done"  # recovered at startup
        with client.websocket_connect("/ws/robot?token=test-token") as robot:
            robot.send_text(hello_frame())
            robot.receive_text()
            assert client.post("/api/recordings/start", json={"name": "new"}).status_code == 200
            assert client.post("/api/recordings/stop").status_code == 200
