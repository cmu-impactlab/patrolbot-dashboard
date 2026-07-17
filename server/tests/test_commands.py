import json
import uuid

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


def request_frame(command: str = "navigate_to_pose", command_id: str | None = None,
                  goal: dict | None = {"x": 1.0, "y": 2.0, "yaw": None}) -> tuple[str, str]:
    command_id = command_id or str(uuid.uuid4())
    data = {"command_id": command_id, "command": command}
    if goal is not None:
        data["goal"] = goal
    return command_id, encode("command.request", "patrolbot-01", 1, data)


def recv_until(ws, wanted_type: str, limit: int = 50) -> dict:
    for _ in range(limit):
        frame = json.loads(ws.receive_text())
        if frame["type"] == wanted_type:
            return frame
    raise AssertionError(f"never received {wanted_type}")


def test_command_full_lifecycle(client):
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame())
        robot.receive_text()  # hello_ack
        with client.websocket_connect("/ws/ui") as ui:
            ui.receive_text()  # snapshot
            command_id, frame = request_frame()
            ui.send_text(frame)

            forwarded = json.loads(robot.receive_text())
            assert forwarded["type"] == "command.request"
            assert forwarded["data"]["command_id"] == command_id
            assert forwarded["data"]["goal"]["x"] == 1.0

            robot.send_text(encode("command.ack", "patrolbot-01", 2,
                                   {"command_id": command_id, "accepted": True}))
            ack = recv_until(ui, "command.ack")
            assert ack["data"]["accepted"] is True

            robot.send_text(encode("command.progress", "patrolbot-01", 3,
                                   {"command_id": command_id, "stage": "navigating",
                                    "distance_remaining": 2.5}))
            progress = recv_until(ui, "command.progress")
            assert progress["data"]["distance_remaining"] == 2.5

            robot.send_text(encode("command.result", "patrolbot-01", 4,
                                   {"command_id": command_id, "outcome": "succeeded",
                                    "detail": "Arrived"}))
            result = recv_until(ui, "command.result")
            assert result["data"]["outcome"] == "succeeded"

    audit = client.get("/api/commands").json()
    assert audit[0]["command_id"] == command_id
    assert audit[0]["outcome"] == "succeeded"


def test_command_rejected_when_robot_offline(client):
    with client.websocket_connect("/ws/ui") as ui:
        ui.receive_text()
        _, frame = request_frame()
        ui.send_text(frame)
        ack = recv_until(ui, "command.ack")
        assert ack["data"]["accepted"] is False
        assert "not connected" in ack["data"]["reason"]


def test_duplicate_command_id_rejected(client):
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame())
        robot.receive_text()
        with client.websocket_connect("/ws/ui") as ui:
            ui.receive_text()
            command_id, frame = request_frame()
            ui.send_text(frame)
            robot.receive_text()  # forwarded once
            ui.send_text(frame)   # replay
            ack = recv_until(ui, "command.ack")
            assert ack["data"]["accepted"] is False
            assert "Duplicate" in ack["data"]["reason"]


def test_missing_goal_rejected(client):
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame())
        robot.receive_text()
        with client.websocket_connect("/ws/ui") as ui:
            ui.receive_text()
            _, frame = request_frame(goal=None)
            ui.send_text(frame)
            ack = recv_until(ui, "command.ack")
            assert ack["data"]["accepted"] is False


def test_stop_needs_no_goal(client):
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame())
        robot.receive_text()
        with client.websocket_connect("/ws/ui") as ui:
            ui.receive_text()
            _, frame = request_frame(command="stop", goal=None)
            ui.send_text(frame)
            forwarded = json.loads(robot.receive_text())
            assert forwarded["data"]["command"] == "stop"


def test_ack_timeout_synthesizes_result(client):
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame())
        robot.receive_text()
        with client.websocket_connect("/ws/ui") as ui:
            ui.receive_text()
            client.app.state.hub.commands.ack_timeout_s = 0.2
            command_id, frame = request_frame()
            ui.send_text(frame)
            result = recv_until(ui, "command.result")
            assert result["data"]["command_id"] == command_id
            assert result["data"]["outcome"] == "timeout"

    audit = client.get("/api/commands").json()
    assert audit[0]["outcome"] == "timeout"


def test_late_reply_after_close_is_dropped(client):
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame())
        robot.receive_text()
        with client.websocket_connect("/ws/ui") as ui:
            ui.receive_text()
            client.app.state.hub.commands.ack_timeout_s = 0.2
            command_id, frame = request_frame()
            ui.send_text(frame)
            recv_until(ui, "command.result")  # timeout closed it
            # Late robot result must not reach browsers or reopen the command.
            robot.send_text(encode("command.result", "patrolbot-01", 9,
                                   {"command_id": command_id, "outcome": "succeeded"}))
            robot.send_text(encode("telemetry.pose", "patrolbot-01", 10, {
                "x": 0.0, "y": 0.0, "yaw": 0.0, "linear_velocity": 0.0, "angular_velocity": 0.0,
            }))
            for _ in range(50):
                frame_in = json.loads(ui.receive_text())
                assert frame_in["type"] != "command.result"
                if frame_in["type"] == "telemetry.pose":
                    break
