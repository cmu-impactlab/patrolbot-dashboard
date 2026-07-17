import json

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


def hello_frame(map_version: int = 0) -> str:
    return encode("robot.hello", "patrolbot-01", 0, {
        "protocol_version": 1, "capabilities": ["pose"], "map_version": map_version,
        "software_version": "test",
    })


def recv_until(ws, wanted_type: str, limit: int = 50) -> dict:
    for _ in range(limit):
        frame = json.loads(ws.receive_text())
        if frame["type"] == wanted_type:
            return frame
    raise AssertionError(f"never received {wanted_type}")


def test_robot_rejected_with_bad_token(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/robot?token=wrong") as ws:
            ws.send_text(hello_frame())
            ws.receive_text()


def test_robot_hello_ack_and_ui_snapshot(client):
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame())
        ack = json.loads(robot.receive_text())
        assert ack["type"] == "server.hello_ack"
        assert ack["data"]["want_map"] is True

        with client.websocket_connect("/ws/ui") as ui:
            snapshot = json.loads(ui.receive_text())
            assert snapshot["type"] == "server.snapshot"
            assert snapshot["data"]["connection"]["state"] == "online"

            robot.send_text(encode("telemetry.pose", "patrolbot-01", 1, {
                "x": 1.0, "y": 2.0, "yaw": 0.5, "linear_velocity": 0.1, "angular_velocity": 0.0,
            }))
            pose = recv_until(ui, "telemetry.pose")
            assert pose["data"]["x"] == 1.0


def test_battery_rebroadcast_includes_estimate(client):
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame())
        robot.receive_text()
        with client.websocket_connect("/ws/ui") as ui:
            ui.receive_text()  # snapshot
            robot.send_text(encode("telemetry.battery", "patrolbot-01", 2, {
                "voltage": 24.5, "percentage": 80.0, "charging": False,
            }))
            battery = recv_until(ui, "telemetry.battery")
            assert battery["data"]["estimate"]["state"] in (
                "calculating", "estimated", "stable", "unavailable"
            )


def test_estop_produces_event_and_status(client):
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame())
        robot.receive_text()
        with client.websocket_connect("/ws/ui") as ui:
            ui.receive_text()
            robot.send_text(encode("telemetry.base_state", "patrolbot-01", 3, {
                "session_generation": 1, "link_connected": True, "telemetry_age": 0.1,
                "hardware_state_valid": True, "charge_state": "not_charging",
                "motors_enabled": True, "estop_pressed": True, "fault_flags": 0,
                "stall_value": 0, "bumpers_front": False, "bumpers_rear": False,
            }))
            event = recv_until(ui, "event.append")
            assert event["data"]["severity"] == "critical"
            status = recv_until(ui, "state.robot_status")
            assert status["data"]["status"] == "needs_attention"


def test_map_flow_and_rest(client):
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame())
        robot.receive_text()
        robot.send_text(encode("telemetry.map", "patrolbot-01", 4, {
            "map_version": 1, "name": "Test Map", "resolution": 0.05, "width": 2, "height": 2,
            "origin": {"x": 0.0, "y": 0.0, "yaw": 0.0}, "rle": [[0, 4]],
        }))
        # Map should now be available over REST.
        for _ in range(100):
            response = client.get("/api/map")
            if response.status_code == 200:
                break
        assert response.status_code == 200
        assert response.json()["name"] == "Test Map"

    response = client.get("/api/health")
    assert response.status_code == 200


def test_layout_endpoints(client):
    response = client.get("/api/layouts")
    assert response.status_code == 200
    names = [item["name"] for item in response.json()]
    assert {"Operator", "Research", "Diagnostics"} <= set(names)

    body = {"widgets": ["liveMap"], "layouts": {"lg": [{"i": "liveMap", "x": 0, "y": 0, "w": 6, "h": 8}]}}
    assert client.put("/api/layouts/current", json=body).status_code == 200
    fetched = client.get("/api/layouts/current")
    assert fetched.status_code == 200
    assert fetched.json()["layout"]["widgets"] == ["liveMap"]

    # Presets cannot be deleted.
    assert client.delete("/api/layouts/Operator").status_code == 404
    assert client.delete("/api/layouts/current").status_code == 200


def test_ui_command_frames_ignored(client):
    with client.websocket_connect("/ws/ui") as ui:
        ui.receive_text()
        ui.send_text(json.dumps({"version": 1, "type": "command.navigate_to_pose", "robot_id": "x",
                                 "sequence": 1, "timestamp": "now", "data": {}}))
        # Connection stays open and functional.
        response = client.get("/api/health")
        assert response.status_code == 200
