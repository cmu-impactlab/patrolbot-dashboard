"""In-process browser/robot sockets only; no shipped bridge claims this capability."""
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.authentication.sessions import COOKIE_NAME, issue
from app.commands import autonomous_speed
from app.main import create_app
from app.protocol.envelope import ProtocolError, decode, encode
from app.protocol.messages import (
    AUTONOMOUS_SPEED_CAPABILITY as CAP,
    AUTONOMOUS_SPEED_RECEIPT as RECEIPT,
    AUTONOMOUS_SPEED_REQUEST as REQUEST,
)
from app.settings import Settings

ROBOT = "patrolbot-01"
FAST = {"profile": "Fast", "linear_m_s": 0.22, "angular_rad_s": 0.70}
CUSTOM = {"profile": "Custom", "linear_m_s": 0.26, "angular_rad_s": 0.70}
TOKEN_FIELD = "autonomous_speed_caps_session"


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(robot_token="test-token", database_path=str(tmp_path / "test.db"),
                              auth_mode="oidc", session_secret="test-secret", operator_usernames="driver"))
    with TestClient(app) as client:
        yield client


def auth(username):
    token = issue("test-secret", {"id": 1 if username == "driver" else 2, "username": username,
                                  "display_name": username, "role": "operator"}, 3600)
    return {"cookie": f"{COOKIE_NAME}={token}"}


def hello(robot, caps, sequence=0):
    robot.send_text(encode("robot.hello", ROBOT, sequence, {
        "protocol_version": 1, "capabilities": caps, "map_version": 0, "software_version": "test",
    }))
    if sequence == 0:
        assert receive(robot, "server.hello_ack") is not None


def receive(socket, kind):
    for _ in range(30):
        frame = json.loads(socket.receive_text())
        if frame["type"] == kind:
            return frame["data"]
    raise AssertionError(f"Expected {kind}")


def request(session=None, selection=FAST):
    return {"command_id": str(uuid4()), "session_id": session or str(uuid4()), "selection": selection}


def send(socket, kind, data, sequence=1):
    socket.send_text(encode(kind, ROBOT, sequence, data))


def assert_unknown(ui, data):
    assert receive(ui, RECEIPT) == {"command_id": data["command_id"], "outcome": "unknown", "readback": None}


@pytest.mark.timeout(12)
def test_initial_capability_only_is_bound_to_exact_robot_session(client):
    with client.websocket_connect("/ws/ui", headers=auth("driver")) as ui:
        receive(ui, "server.snapshot")
        with client.websocket_connect("/ws/robot?token=test-token") as old_robot:
            hello(old_robot, ["robot_tuning_v1", "web_teleop_v1"])
            assert receive(ui, "state.capabilities")[TOKEN_FIELD] is None
            hello(old_robot, [CAP], sequence=1)
            announcement = receive(ui, "state.capabilities")
            assert announcement[TOKEN_FIELD] is None and CAP not in announcement["capabilities"]
            unsupported = request()
            send(ui, REQUEST, unsupported)
            assert_unknown(ui, unsupported)
        with client.websocket_connect("/ws/robot?token=test-token") as current:
            hello(current, [CAP])
            first = receive(ui, "state.capabilities")[TOKEN_FIELD]
            assert first
            hello(current, ["pose"], sequence=1)
            assert receive(ui, "state.capabilities") == {"capabilities": ["pose", CAP], TOKEN_FIELD: first}
            with client.websocket_connect("/ws/robot?token=test-token") as replacement:
                hello(replacement, [CAP])
                second = receive(ui, "state.capabilities")[TOKEN_FIELD]
                assert second != first
                stale = request(first)
                send(ui, REQUEST, stale)
                assert_unknown(ui, stale)
                valid = request(second, CUSTOM)
                send(ui, REQUEST, valid)
                assert receive(replacement, REQUEST) == {**valid, "operator_authorized": True}
                send(replacement, RECEIPT, {"command_id": valid["command_id"], "outcome": "applied", "readback": CUSTOM})
                assert receive(ui, RECEIPT) == {"command_id": valid["command_id"], "outcome": "applied", "readback": CUSTOM}


@pytest.mark.timeout(12)
def test_observer_cannot_forge_navigation_authority_or_robot_receipt(client):
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        hello(robot, [CAP])
        with client.websocket_connect("/ws/ui", headers=auth("viewer")) as viewer:
            session = receive(viewer, "server.snapshot")[TOKEN_FIELD]
            refused = request(session)
            send(viewer, REQUEST, {**refused, "operator_authorized": True})
            assert_unknown(viewer, refused)
            with client.websocket_connect("/ws/ui", headers=auth("driver")) as operator:
                current = request(session)
                send(operator, REQUEST, current)
                assert receive(robot, REQUEST) == {**current, "operator_authorized": True}
                send(viewer, RECEIPT, {"command_id": current["command_id"], "outcome": "applied", "readback": FAST})
                wrong = {"command_id": current["command_id"], "outcome": "applied", "readback": CUSTOM}
                send(robot, RECEIPT, wrong)
                assert_unknown(operator, current)


@pytest.mark.timeout(12)
@pytest.mark.parametrize("terminal", ["missing", "timeout", "disconnect"])
def test_uncertain_application_never_replays(client, monkeypatch, terminal):
    if terminal == "timeout":
        monkeypatch.setattr(autonomous_speed, "RECEIPT_TIMEOUT_S", 0.15)
    with client.websocket_connect("/ws/ui", headers=auth("driver")) as ui:
        receive(ui, "server.snapshot")
        with client.websocket_connect("/ws/robot?token=test-token") as robot:
            hello(robot, [CAP])
            data = request(receive(ui, "state.capabilities")[TOKEN_FIELD])
            send(ui, REQUEST, data)
            assert receive(robot, REQUEST)["command_id"] == data["command_id"]
            if terminal == "missing":
                send(robot, RECEIPT, {"command_id": data["command_id"], "outcome": "applied"})
            if terminal != "disconnect":
                assert_unknown(ui, data)
                send(robot, RECEIPT, {"command_id": data["command_id"], "outcome": "applied", "readback": FAST}, sequence=2)
                next_attempt = request(data["session_id"])
                send(ui, REQUEST, next_attempt)
                assert_unknown(ui, next_attempt)
        if terminal == "disconnect":
            assert_unknown(ui, data)


@pytest.mark.parametrize("selection", [
    {**FAST, "profile": "Really Fast", "linear_m_s": 2.0},
    {**FAST, "linear_m_s": 0.26},
    {**CUSTOM, "linear_m_s": 0.2600001},
    {**CUSTOM, "angular_rad_s": 0.700001},
    {**CUSTOM, "angular_rad_s": float("nan")},
    {**CUSTOM, "angular_rad_s": "0.70"},
    {**CUSTOM, "linear_m_s": True},
    {**CUSTOM, "extra": "foreign field"},
])
def test_wire_rejects_unbounded_or_unrecognized_selections(selection):
    with pytest.raises(ProtocolError):
        decode(encode(REQUEST, ROBOT, 1, request(selection=selection)))
