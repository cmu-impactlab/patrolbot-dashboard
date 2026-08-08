import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.authentication.sessions import COOKIE_NAME, issue
from app.main import create_app
from app.protocol.envelope import encode
from app.settings import Settings


@pytest.fixture()
def client(tmp_path):
    settings = Settings(robot_token="test-token", database_path=str(tmp_path / "test.db"))
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def client_factory(tmp_path):
    """Build a TestClient (not yet entered) with settings overrides."""
    def make(**overrides) -> TestClient:
        settings = Settings(robot_token="test-token",
                            database_path=str(tmp_path / "test.db"), **overrides)
        return TestClient(create_app(settings))
    return make


def hello_frame() -> str:
    return encode("robot.hello", "patrolbot-01", 0, {
        "protocol_version": 1, "capabilities": ["pose"], "map_version": 0,
        "software_version": "test",
    })


def make_drivable(robot) -> None:
    """Send the telemetry navigate_to_pose is gated on (seq 1 and 2).

    A robot that has only said hello is not a robot known to be safe to drive.
    The server refuses navigation until a fresh, healthy base_state and a fresh
    localized pose have actually arrived — see app/commands/gates.py.
    """
    robot.send_text(encode("telemetry.base_state", "patrolbot-01", 1, {
        "session_generation": 1, "link_connected": True, "telemetry_age": 0.1,
        "hardware_state_valid": True, "charge_state": "idle",
        "motors_enabled": True, "estop_pressed": False, "fault_flags": 0,
        "stall_value": 0, "bumpers_front": False, "bumpers_rear": False,
    }))
    robot.send_text(encode("telemetry.pose", "patrolbot-01", 2, {
        "x": 1.0, "y": 1.0, "yaw": 0.0, "linear_velocity": 0.0,
        "angular_velocity": 0.0, "localized": True,
    }))


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
        make_drivable(robot)
        with client.websocket_connect("/ws/ui") as ui:
            ui.receive_text()  # snapshot
            command_id, frame = request_frame()
            ui.send_text(frame)

            forwarded = json.loads(robot.receive_text())
            assert forwarded["type"] == "command.request"
            assert forwarded["data"]["command_id"] == command_id
            assert forwarded["data"]["goal"]["x"] == 1.0

            robot.send_text(encode("command.ack", "patrolbot-01", 3,
                                   {"command_id": command_id, "accepted": True}))
            ack = recv_until(ui, "command.ack")
            assert ack["data"]["accepted"] is True

            robot.send_text(encode("command.progress", "patrolbot-01", 4,
                                   {"command_id": command_id, "stage": "navigating",
                                    "distance_remaining": 2.5}))
            progress = recv_until(ui, "command.progress")
            assert progress["data"]["distance_remaining"] == 2.5

            robot.send_text(encode("command.result", "patrolbot-01", 5,
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
        make_drivable(robot)
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
        make_drivable(robot)
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
        make_drivable(robot)
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


# -- Roles & single-operator lease -----------------------------------------

def _oidc_client(tmp_path) -> TestClient:
    """OIDC-mode app so /ws/ui resolves a real signed session (role-bearing)."""
    settings = Settings(robot_token="test-token", database_path=str(tmp_path / "t.db"),
                        auth_mode="oidc", session_secret="s3cret")
    return TestClient(create_app(settings))


def _cookie(role: str, uid: int, username: str, secret: str = "s3cret") -> dict:
    token = issue(secret, {"id": uid, "username": username,
                           "display_name": username, "role": role}, 3600)
    return {"cookie": f"{COOKIE_NAME}={token}"}


def _cmd_frame(command: str = "navigate_to_pose", goal=({"x": 1.0, "y": 2.0, "yaw": None}),
               command_id: str | None = None, takeover: bool = False):
    command_id = command_id or str(uuid.uuid4())
    data = {"command_id": command_id, "command": command, "takeover": takeover}
    if goal is not None:
        data["goal"] = goal
    return command_id, encode("command.request", "patrolbot-01", 1, data)


def _connect_robot(client):
    robot = client.websocket_connect("/ws/robot?token=test-token")
    ws = robot.__enter__()
    ws.send_text(hello_frame())
    ws.receive_text()  # hello_ack
    make_drivable(ws)
    return robot, ws


def test_observer_cannot_command(tmp_path):
    with _oidc_client(tmp_path) as client:
        robot_cm, _robot = _connect_robot(client)
        try:
            with client.websocket_connect("/ws/ui", headers=_cookie("observer", 2, "viewer")) as ui:
                ui.receive_text()  # snapshot
                _, frame = _cmd_frame()
                ui.send_text(frame)
                ack = recv_until(ui, "command.ack")
                assert ack["data"]["accepted"] is False
                assert "read-only" in ack["data"]["reason"].lower()
        finally:
            robot_cm.__exit__(None, None, None)


def test_operator_can_command(tmp_path):
    with _oidc_client(tmp_path) as client:
        robot_cm, robot = _connect_robot(client)
        try:
            with client.websocket_connect("/ws/ui", headers=_cookie("operator", 2, "driver")) as ui:
                ui.receive_text()
                command_id, frame = _cmd_frame()
                ui.send_text(frame)
                forwarded = json.loads(robot.receive_text())
                assert forwarded["type"] == "command.request"
                assert forwarded["data"]["command_id"] == command_id
        finally:
            robot_cm.__exit__(None, None, None)


def test_second_operator_blocked_without_takeover(tmp_path):
    with _oidc_client(tmp_path) as client:
        robot_cm, robot = _connect_robot(client)
        try:
            with client.websocket_connect("/ws/ui", headers=_cookie("operator", 2, "alice")) as ui1:
                ui1.receive_text()
                _, f1 = _cmd_frame()
                ui1.send_text(f1)
                robot.receive_text()  # alice's command forwarded; alice holds the lease

                with client.websocket_connect("/ws/ui", headers=_cookie("operator", 3, "bob")) as ui2:
                    ui2.receive_text()
                    _, f2 = _cmd_frame()
                    ui2.send_text(f2)
                    ack = recv_until(ui2, "command.ack")
                    assert ack["data"]["accepted"] is False
                    assert "alice" in ack["data"]["reason"]
        finally:
            robot_cm.__exit__(None, None, None)


def test_takeover_transfers_lease(tmp_path):
    with _oidc_client(tmp_path) as client:
        robot_cm, robot = _connect_robot(client)
        try:
            with client.websocket_connect("/ws/ui", headers=_cookie("operator", 2, "alice")) as ui1:
                ui1.receive_text()
                _, f1 = _cmd_frame()
                ui1.send_text(f1)
                robot.receive_text()  # alice holds lease

                with client.websocket_connect("/ws/ui", headers=_cookie("operator", 3, "bob")) as ui2:
                    ui2.receive_text()
                    # Explicit takeover: bob's command goes through and he now holds the lease.
                    _, f2 = _cmd_frame(takeover=True)
                    ui2.send_text(f2)
                    forwarded = json.loads(robot.receive_text())
                    assert forwarded["type"] == "command.request"

                    # Alice, now dispossessed, is blocked without her own takeover.
                    _, f3 = _cmd_frame()
                    ui1.send_text(f3)
                    ack = recv_until(ui1, "command.ack")
                    assert ack["data"]["accepted"] is False
                    assert "bob" in ack["data"]["reason"]
        finally:
            robot_cm.__exit__(None, None, None)


def test_lease_released_on_disconnect(tmp_path):
    with _oidc_client(tmp_path) as client:
        robot_cm, robot = _connect_robot(client)
        try:
            with client.websocket_connect("/ws/ui", headers=_cookie("operator", 2, "alice")) as ui1:
                ui1.receive_text()
                _, f1 = _cmd_frame()
                ui1.send_text(f1)
                robot.receive_text()  # alice holds lease
            # alice disconnected -> lease freed

            with client.websocket_connect("/ws/ui", headers=_cookie("operator", 3, "bob")) as ui2:
                ui2.receive_text()
                _, f2 = _cmd_frame()
                ui2.send_text(f2)
                forwarded = json.loads(robot.receive_text())  # bob commands with no takeover
                assert forwarded["type"] == "command.request"
        finally:
            robot_cm.__exit__(None, None, None)


def test_same_user_second_tab_is_not_blocked(tmp_path):
    with _oidc_client(tmp_path) as client:
        robot_cm, robot = _connect_robot(client)
        try:
            with client.websocket_connect("/ws/ui", headers=_cookie("operator", 2, "alice")) as ui1:
                ui1.receive_text()
                _, f1 = _cmd_frame()
                ui1.send_text(f1)
                robot.receive_text()

                # Same operator, another tab (same user id) — same person, no takeover needed.
                with client.websocket_connect("/ws/ui", headers=_cookie("operator", 2, "alice")) as ui2:
                    ui2.receive_text()
                    _, f2 = _cmd_frame()
                    ui2.send_text(f2)
                    forwarded = json.loads(robot.receive_text())
                    assert forwarded["type"] == "command.request"
        finally:
            robot_cm.__exit__(None, None, None)


# -- Origin allowlist & rate limits ----------------------------------------

def _origin_client(tmp_path, **overrides) -> TestClient:
    settings = Settings(robot_token="test-token", database_path=str(tmp_path / "t.db"),
                        allowed_origins="https://dash.example.edu", **overrides)
    return TestClient(create_app(settings))


def test_rejects_disallowed_origin(tmp_path):
    from starlette.websockets import WebSocketDisconnect

    with _origin_client(tmp_path) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                    "/ws/ui", headers={"origin": "https://evil.example.com"}) as ui:
                ui.receive_text()


def test_allows_listed_origin(tmp_path):
    with _origin_client(tmp_path) as client:
        with client.websocket_connect(
                "/ws/ui", headers={"origin": "https://dash.example.edu"}) as ui:
            snapshot = json.loads(ui.receive_text())
            assert snapshot["type"] == "server.snapshot"


def test_empty_allowlist_permits_any_origin(tmp_path):
    # Local dev default: no allowlist configured -> no Origin enforcement.
    settings = Settings(robot_token="test-token", database_path=str(tmp_path / "t.db"))
    with TestClient(create_app(settings)) as client:
        with client.websocket_connect("/ws/ui", headers={"origin": "http://localhost:5173"}) as ui:
            assert json.loads(ui.receive_text())["type"] == "server.snapshot"


def test_command_rate_limited(tmp_path):
    with TestClient(create_app(Settings(
            robot_token="test-token", database_path=str(tmp_path / "t.db"),
            command_rate_per_min=2))) as client:
        with client.websocket_connect("/ws/robot?token=test-token") as robot:
            robot.send_text(hello_frame())
            robot.receive_text()
            make_drivable(robot)
            with client.websocket_connect("/ws/ui") as ui:
                ui.receive_text()
                # Two commands fit the budget and are forwarded.
                for _ in range(2):
                    _, frame = _cmd_frame()
                    ui.send_text(frame)
                    assert json.loads(robot.receive_text())["type"] == "command.request"
                # The third exceeds the per-operator budget and is rejected.
                _, frame = _cmd_frame()
                ui.send_text(frame)
                ack = recv_until(ui, "command.ack")
                assert ack["data"]["accepted"] is False
                assert "too many" in ack["data"]["reason"].lower()


# -- Robot token transport --------------------------------------------------

def test_robot_connects_with_header_token(client):
    with client.websocket_connect(
            "/ws/robot", headers={"authorization": "Bearer test-token"}) as robot:
        robot.send_text(hello_frame())
        assert json.loads(robot.receive_text())["type"] == "server.hello_ack"


def test_robot_connects_with_legacy_query_token(client):
    # Backward-compatible transition path (deprecated) — must keep working so a
    # not-yet-updated bridge stays online during rollout.
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame())
        assert json.loads(robot.receive_text())["type"] == "server.hello_ack"


def test_robot_bad_header_token_rejected(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
                "/ws/robot", headers={"authorization": "Bearer wrong"}) as robot:
            robot.receive_text()


def test_robot_missing_token_rejected(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/robot") as robot:
            robot.receive_text()


# -- Goal & envelope validation --------------------------------------------

def test_nan_goal_rejected(client):
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame())
        robot.receive_text()
        with client.websocket_connect("/ws/ui") as ui:
            ui.receive_text()
            _, frame = request_frame(goal={"x": float("nan"), "y": 1.0, "yaw": None})
            ui.send_text(frame)
            ack = recv_until(ui, "command.ack")
            assert ack["data"]["accepted"] is False
            assert "invalid" in ack["data"]["reason"].lower()


def test_out_of_bounds_goal_rejected(client):
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame())
        robot.receive_text()
        with client.websocket_connect("/ws/ui") as ui:
            ui.receive_text()
            # No occupancy map streamed -> coarse sanity limit (1000 m) applies.
            _, frame = request_frame(goal={"x": 50000.0, "y": 1.0, "yaw": None})
            ui.send_text(frame)
            ack = recv_until(ui, "command.ack")
            assert ack["data"]["accepted"] is False
            assert "too far" in ack["data"]["reason"].lower()


def _pose_frame(robot_id: str, seq: int, x: float) -> str:
    return encode("telemetry.pose", robot_id, seq, {
        "x": x, "y": 0.0, "yaw": 0.0, "linear_velocity": 0.0, "angular_velocity": 0.0,
        "localized": True,
    })


def test_robot_frame_with_mismatched_id_dropped(client):
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame())
        robot.receive_text()
        with client.websocket_connect("/ws/ui") as ui:
            ui.receive_text()
            robot.send_text(_pose_frame("evil-robot", 5, 1.0))   # spoofed id -> dropped
            robot.send_text(_pose_frame("patrolbot-01", 6, 2.0))  # legit -> forwarded
            pose = recv_until(ui, "telemetry.pose")
            assert pose["robot_id"] == "patrolbot-01"
            assert pose["data"]["x"] == 2.0


def test_robot_reconnect_does_not_replay_command(client):
    # First connection: send and acknowledge a command.
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame())
        robot.receive_text()
        make_drivable(robot)
        with client.websocket_connect("/ws/ui") as ui:
            ui.receive_text()
            old_id, frame = request_frame(goal={"x": 5.0, "y": 6.0, "yaw": None})
            ui.send_text(frame)
            assert json.loads(robot.receive_text())["data"]["command_id"] == old_id
            robot.send_text(encode("command.ack", "patrolbot-01", 3,
                                   {"command_id": old_id, "accepted": True}))
            recv_until(ui, "command.ack")
    # Robot socket dropped. Reconnect: the server must NOT resend the old goal.
    with client.websocket_connect("/ws/robot?token=test-token") as robot2:
        robot2.send_text(hello_frame())
        robot2.receive_text()  # hello_ack only
        make_drivable(robot2)
        with client.websocket_connect("/ws/ui") as ui2:
            ui2.receive_text()
            new_id, frame2 = request_frame(goal={"x": 1.0, "y": 1.0, "yaw": None})
            ui2.send_text(frame2)
            forwarded = json.loads(robot2.receive_text())
            # The first frame the reconnected robot receives is the NEW command,
            # proving the prior goal was not auto-replayed on reconnect.
            assert forwarded["data"]["command_id"] == new_id


def _base_state_frame(seq: int, generation: int) -> str:
    return encode("telemetry.base_state", "patrolbot-01", seq, {
        "session_generation": generation, "link_connected": True, "telemetry_age": 0.1,
        "hardware_state_valid": True, "charge_state": "idle", "motors_enabled": True,
        "estop_pressed": False, "fault_flags": 0, "stall_value": 0,
        "bumpers_front": False, "bumpers_rear": False,
    })


def test_audit_records_identity(client):
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame())
        robot.receive_text()
        robot.send_text(_base_state_frame(1, 7))  # establishes session_generation
        robot.send_text(_pose_frame("patrolbot-01", 2, 1.0))  # navigate needs a pose
        with client.websocket_connect("/ws/ui") as ui:
            ui.receive_text()
            command_id, frame = request_frame()
            ui.send_text(frame)
            robot.receive_text()  # forwarded
            robot.send_text(encode("command.ack", "patrolbot-01", 3,
                                   {"command_id": command_id, "accepted": True}))
            robot.send_text(encode("command.result", "patrolbot-01", 4,
                                   {"command_id": command_id, "outcome": "succeeded"}))
            recv_until(ui, "command.result")

    row = client.get("/api/commands").json()[0]
    assert row["command_id"] == command_id
    assert row["username"] == "local" and row["user_id"] == 1
    assert row["source_ip"]  # recorded
    assert row["session_generation"] == 7


def test_rejection_is_audited(client_factory):
    # Rate limit of 1/min: the second command is rejected and recorded.
    client = client_factory(command_rate_per_min=1)
    with client:
        with client.websocket_connect("/ws/robot?token=test-token") as robot:
            robot.send_text(hello_frame())
            robot.receive_text()
            make_drivable(robot)
            with client.websocket_connect("/ws/ui") as ui:
                ui.receive_text()
                _, f1 = request_frame()
                ui.send_text(f1)
                robot.receive_text()  # first accepted + forwarded
                _, f2 = request_frame()
                ui.send_text(f2)
                ack = recv_until(ui, "command.ack")
                assert ack["data"]["accepted"] is False

        rejected = [r for r in client.get("/api/commands").json() if r["outcome"] == "rejected"]
        assert rejected and "too many" in rejected[0]["rejection_reason"].lower()


def test_duplicate_after_restart_rejected(tmp_path):
    db = str(tmp_path / "shared.db")
    command_id, frame = request_frame()

    # First "boot": accept and complete a command, persisting it to the DB.
    with TestClient(create_app(Settings(robot_token="test-token", database_path=db))) as client:
        with client.websocket_connect("/ws/robot?token=test-token") as robot:
            robot.send_text(hello_frame())
            robot.receive_text()
            make_drivable(robot)
            with client.websocket_connect("/ws/ui") as ui:
                ui.receive_text()
                ui.send_text(frame)
                robot.receive_text()
                robot.send_text(encode("command.ack", "patrolbot-01", 3,
                                       {"command_id": command_id, "accepted": True}))
                robot.send_text(encode("command.result", "patrolbot-01", 4,
                                       {"command_id": command_id, "outcome": "succeeded"}))
                recv_until(ui, "command.result")

    # Second "boot": fresh process (empty in-memory cache), same DB. Replaying
    # the same command_id must be rejected from the persisted record.
    with TestClient(create_app(Settings(robot_token="test-token", database_path=db))) as client:
        with client.websocket_connect("/ws/robot?token=test-token") as robot:
            robot.send_text(hello_frame())
            robot.receive_text()
            with client.websocket_connect("/ws/ui") as ui:
                ui.receive_text()
                ui.send_text(frame)  # same command_id as before the restart
                ack = recv_until(ui, "command.ack")
                assert ack["data"]["accepted"] is False
                assert "duplicate" in ack["data"]["reason"].lower()


def test_stale_sequence_frame_dropped(client):
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame())
        robot.receive_text()
        with client.websocket_connect("/ws/ui") as ui:
            ui.receive_text()
            robot.send_text(_pose_frame("patrolbot-01", 6, 1.0))  # ok
            robot.send_text(_pose_frame("patrolbot-01", 4, 2.0))  # stale seq -> dropped
            robot.send_text(_pose_frame("patrolbot-01", 7, 3.0))  # ok
            seen_x = []
            for _ in range(60):
                frame = json.loads(ui.receive_text())
                if frame["type"] == "telemetry.pose":
                    seen_x.append(frame["data"]["x"])
                    if frame["data"]["x"] == 3.0:
                        break
            assert 2.0 not in seen_x  # the stale frame never propagated
            assert 1.0 in seen_x and 3.0 in seen_x
