"""Navigation, charging, motor-power and undock command gates.

Two layers are covered: the pure precondition rules, and the broker actually
enforcing them on a live socket — a UI that greys out the wrong button must
still not be able to move the robot.
"""
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.commands import gates
from app.main import create_app
from app.protocol.envelope import encode
from app.settings import Settings

ALL_CAPS = ("undock", "charge_release", "motor_enable")


def facts(**overrides) -> gates.StateFacts:
    """A robot sitting on its dock, charging, everything healthy."""
    base = dict(online=True, link_connected=True, telemetry_age=0.1,
                base_state_age=0.1, pose_age=0.1,
                hardware_state_valid=True, charge_state="charging",
                motors_enabled=False, estop_pressed=False, fault_flags=0,
                bumpers_front=False, bumpers_rear=False, localized=True,
                stationary=True, capabilities=ALL_CAPS)
    return gates.StateFacts(**{**base, **overrides})


# -- pure gates -------------------------------------------------------------

def test_charge_release_allowed_while_charging_and_parked():
    assert gates.charge_release_reason(facts()) is None


@pytest.mark.parametrize("overrides, fragment", [
    ({"online": False}, "not connected"),
    ({"telemetry_age": 9.0}, "stale"),
    ({"link_connected": False}, "stale"),
    ({"hardware_state_valid": False}, "valid data"),
    ({"fault_flags": 4}, "fault"),
    ({"charge_state": "not_charging"}, "not on charge"),
    ({"stationary": False}, "still moving"),
])
def test_charge_release_refusals(overrides, fragment):
    reason = gates.charge_release_reason(facts(**overrides))
    assert reason is not None and fragment in reason.lower()


def test_charging_with_motors_on_is_not_a_deadlock():
    """A hand-docked robot charges with its motors left enabled. Charge release
    used to demand motors off while motor enable demanded the charger released,
    leaving an operator with two refusals pointing at each other."""
    stuck = facts(charge_state="charging", motors_enabled=True)
    assert gates.charge_release_reason(stuck) is None
    assert "release charging" in (gates.motor_enable_reason(stuck) or "").lower()
    # Whatever else changes, at least one of the two must always be actionable.
    assert None in (gates.charge_release_reason(stuck), gates.motor_enable_reason(stuck))


def test_motor_enable_needs_charge_released_first():
    reason = gates.motor_enable_reason(facts(charge_state="charging"))
    assert reason is not None and "release charging" in reason.lower()
    assert gates.motor_enable_reason(facts(charge_state="docked")) is None


@pytest.mark.parametrize("overrides, fragment", [
    ({"estop_pressed": True}, "emergency stop"),
    ({"motors_enabled": True}, "already on"),
    ({"stationary": False}, "still moving"),
    ({"fault_flags": 1}, "fault"),
])
def test_motor_enable_refusals(overrides, fragment):
    reason = gates.motor_enable_reason(facts(charge_state="docked", **overrides))
    assert reason is not None and fragment in reason.lower()


def test_undock_allowed_straight_off_the_charger():
    """One operator action: the robot releases its own charger and powers its
    own motors, so neither is required of the dashboard beforehand."""
    assert gates.undock_reason(facts(charge_state="charging", motors_enabled=False)) is None
    assert gates.undock_reason(facts(charge_state="docked", motors_enabled=True)) is None


def test_dock_observer_overrides_stale_charge_state_after_departure():
    clear = facts(
        charge_state="float",
        dock_state="CLEAR_CONFIRMED",
        dock_state_valid=True,
        undock_profile_commissioned=True,
    )
    assert clear.on_dock is False
    assert "not on its dock" in (gates.undock_reason(clear) or "").lower()


def test_undock_refuses_invalid_or_uncommissioned_dock_observer():
    invalid = facts(dock_state="UNKNOWN", dock_state_valid=False)
    assert "dock observer" in (gates.undock_reason(invalid) or "").lower()

    uncommissioned = facts(
        dock_state="DOCKED_CONFIRMED",
        dock_state_valid=True,
        undock_profile_commissioned=False,
    )
    assert "profile" in (gates.undock_reason(uncommissioned) or "").lower()


def test_undock_refuses_duplicate_reported_by_robot_state():
    active = facts(
        dock_state="DEPARTING",
        dock_state_valid=True,
        undock_profile_commissioned=True,
        undock_active=True,
    )
    assert "already undocking" in (gates.undock_reason(active) or "").lower()


@pytest.mark.parametrize("overrides, fragment", [
    ({"capabilities": ("charge_release",)}, "not commissioned"),
    ({"charge_state": "not_charging"}, "not on its dock"),
    ({"bumpers_rear": True}, "rear bumper"),
    ({"estop_pressed": True}, "emergency stop"),
    ({"stationary": False}, "still moving"),
    # An accepted nav goal that hasn't started moving yet still reads as
    # stationary, so `stationary` alone let an undock through that the robot
    # then refused with "another navigation is active" (2026-07-28 12:27).
    ({"navigating": True}, "driving to a destination"),
    ({"hardware_state_valid": False}, "valid data"),
    ({"telemetry_age": 9.0}, "stale"),
    ({"fault_flags": 2}, "fault"),
])
def test_undock_refusals(overrides, fragment):
    reason = gates.undock_reason(facts(**overrides))
    assert reason is not None and fragment in reason.lower()


def test_there_is_no_dock_command():
    """The robot has no automatic dock-in path, so the dashboard does not offer
    one — not even a gated, greyed-out one that could never succeed."""
    assert "dock" not in gates.GATES
    assert gates.rejection_reason("dock", facts()) is None  # not a command


def test_undock_hidden_behind_capabilities():
    """The robot must claim the capability; an uncommissioned base is refused
    even when every other precondition is satisfied."""
    on_dock = facts(charge_state="docked", motors_enabled=True, capabilities=())
    assert "not commissioned" in (gates.undock_reason(on_dock) or "")


def test_missing_telemetry_fails_closed():
    """No base_state at all: every gate refuses rather than assuming healthy."""
    blank = gates.facts_from_state("online", None, None, list(ALL_CAPS))
    for command in gates.GATES:
        assert gates.rejection_reason(command, blank) is not None


def test_ungated_commands_pass_through():
    """stop and set_initial_pose do not drive the robot, so nothing gates them —
    stop especially must stay available in exactly the states that refuse
    everything else."""
    assert gates.rejection_reason("stop", facts(online=False)) is None
    assert gates.rejection_reason("set_initial_pose", facts(fault_flags=4)) is None


# -- navigate ---------------------------------------------------------------

def driving_facts(**overrides) -> gates.StateFacts:
    """Off the dock, motors live, localized — ready to be sent somewhere."""
    return facts(**{"charge_state": "not_charging", "motors_enabled": True,
                    **overrides})


def test_navigate_allowed_when_robot_is_ready_to_drive():
    assert gates.navigate_reason(driving_facts()) is None


@pytest.mark.parametrize("overrides, fragment", [
    ({"online": False}, "not connected"),
    # Two independent staleness questions: what the base said, and when we
    # heard it. Either one alone is enough to refuse.
    ({"telemetry_age": 9.0}, "stale"),
    ({"base_state_age": 9.0}, "stale"),
    ({"link_connected": False}, "stale"),
    ({"hardware_state_valid": False}, "valid data"),
    ({"fault_flags": 4}, "fault"),
    ({"estop_pressed": True}, "emergency stop"),
    ({"motors_enabled": False}, "motors are off"),
    ({"pose_age": 9.0}, "recent position update"),
    ({"pose_age": None}, "recent position update"),
    ({"localized": False}, "does not know where it is"),
])
def test_navigate_refusals(overrides, fragment):
    reason = gates.navigate_reason(driving_facts(**overrides))
    assert reason is not None and fragment in reason.lower()


@pytest.mark.parametrize("overrides", [
    # Motors off because the charger cut them — the ordinary docked state.
    {"charge_state": "charging", "motors_enabled": False},
    # Hand-docked: charging with the motors still reported enabled. This one
    # otherwise reads as "healthy and ready to drive" on every other fact.
    {"charge_state": "charging", "motors_enabled": True},
    # Dock observer present and confirming, whatever charge_state claims.
    {"charge_state": "idle", "dock_state": "DOCKED_CONFIRMED",
     "dock_state_valid": True},
    # An unusable dock observer says nothing about the dock, so it must not
    # overrule a charge_state reporting current flowing in.
    {"charge_state": "charging", "dock_state": "UNKNOWN",
     "dock_state_valid": False, "motors_enabled": True},
])
def test_navigate_refused_on_the_dock(overrides):
    """Coming off the dock is undock's job. Navigation must not pull the robot
    off its charger under Nav2, and the refusal has to name the dock rather
    than send the operator after a motor-enable that cannot succeed there."""
    reason = gates.navigate_reason(driving_facts(**overrides))
    assert reason is not None and "on its charger" in reason.lower()


def test_navigate_allowed_while_already_moving():
    """Redirecting a driving robot is supported — Nav2 preempts its own goal."""
    assert gates.navigate_reason(driving_facts(stationary=False)) is None


def test_facts_treat_encoder_noise_as_stationary():
    class Pose:
        linear_velocity = 0.005
        angular_velocity = -0.01
        localized = True

    assert gates.facts_from_state("online", None, Pose()).stationary is True

    class Rolling(Pose):
        linear_velocity = 0.3

    assert gates.facts_from_state("online", None, Rolling()).stationary is False


# -- broker enforcement over a live socket ----------------------------------

@pytest.fixture()
def client(tmp_path):
    settings = Settings(robot_token="test-token", database_path=str(tmp_path / "test.db"))
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def hello_frame(capabilities=ALL_CAPS) -> str:
    return encode("robot.hello", "patrolbot-01", 0, {
        "protocol_version": 1, "capabilities": list(capabilities),
        "map_version": 0, "software_version": "test",
    })


def base_state_frame(sequence: int, **overrides) -> str:
    data = {"session_generation": 1, "link_connected": True, "telemetry_age": 0.1,
            "hardware_state_valid": True, "charge_state": "charging",
            "motors_enabled": False, "estop_pressed": False, "fault_flags": 0,
            "stall_value": 0, "bumpers_front": False, "bumpers_rear": False}
    data.update(overrides)
    return encode("telemetry.base_state", "patrolbot-01", sequence, data)


def pose_frame(sequence: int, moving: bool = False, localized: bool = True) -> str:
    return encode("telemetry.pose", "patrolbot-01", sequence, {
        "x": 1.0, "y": 1.0, "yaw": 0.0,
        "linear_velocity": 0.4 if moving else 0.0,
        "angular_velocity": 0.0, "localized": localized,
    })


def command_frame(command: str, goal: dict | None = None) -> tuple[str, str]:
    command_id = str(uuid.uuid4())
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


def test_charge_release_forwarded_when_state_allows(client):
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame())
        robot.receive_text()
        robot.send_text(base_state_frame(1))
        robot.send_text(pose_frame(2))
        with client.websocket_connect("/ws/ui") as ui:
            ui.receive_text()  # snapshot
            command_id, frame = command_frame("charge_release")
            ui.send_text(frame)
            forwarded = json.loads(robot.receive_text())
            assert forwarded["data"]["command"] == "charge_release"
            assert forwarded["data"]["command_id"] == command_id


def test_navigate_rejected_when_the_robot_is_not_localized(client):
    """The browser's "location set this session" flag is a fact about a tab.
    A frame that never passed through that UI still has to meet the same
    requirement, judged from the robot's own pose."""
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame())
        robot.receive_text()
        robot.send_text(base_state_frame(1, charge_state="idle", motors_enabled=True))
        robot.send_text(pose_frame(2, localized=False))
        with client.websocket_connect("/ws/ui") as ui:
            ui.receive_text()
            _, frame = command_frame("navigate_to_pose", goal={"x": 1.0, "y": 2.0})
            ui.send_text(frame)
            ack = recv_until(ui, "command.ack")
            assert ack["data"]["accepted"] is False
            assert "does not know where it is" in ack["data"]["reason"]


def test_undock_forwarded_straight_from_charging(client):
    """The dashboard's single button: one command, sent while the robot is
    still on charge with its motors off."""
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame())
        robot.receive_text()
        robot.send_text(base_state_frame(1, charge_state="charging", motors_enabled=False))
        robot.send_text(pose_frame(2))
        with client.websocket_connect("/ws/ui") as ui:
            ui.receive_text()
            command_id, frame = command_frame("undock")
            ui.send_text(frame)
            forwarded = json.loads(robot.receive_text())
            assert forwarded["data"]["command"] == "undock"
            assert forwarded["data"]["command_id"] == command_id


def test_motor_enable_rejected_while_charging(client):
    """The separate motor_enable command (not sent by the dashboard UI) keeps
    its own interlock: motors must not go live on the charger."""
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame())
        robot.receive_text()
        robot.send_text(base_state_frame(1, charge_state="charging"))
        robot.send_text(pose_frame(2))
        with client.websocket_connect("/ws/ui") as ui:
            ui.receive_text()
            _, frame = command_frame("motor_enable")
            ui.send_text(frame)
            ack = recv_until(ui, "command.ack")
            assert ack["data"]["accepted"] is False
            assert "release charging" in ack["data"]["reason"].lower()


def test_undock_rejected_when_robot_does_not_advertise_it(client):
    """The real Pi has not commissioned undock; asking anyway is refused."""
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame(capabilities=("pose",)))
        robot.receive_text()
        robot.send_text(base_state_frame(1, charge_state="docked", motors_enabled=True))
        robot.send_text(pose_frame(2))
        with client.websocket_connect("/ws/ui") as ui:
            ui.receive_text()
            _, frame = command_frame("undock")
            ui.send_text(frame)
            ack = recv_until(ui, "command.ack")
            assert ack["data"]["accepted"] is False
            assert "not commissioned" in ack["data"]["reason"]


def test_undock_rejected_while_moving(client):
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame())
        robot.receive_text()
        robot.send_text(base_state_frame(1, charge_state="docked"))
        robot.send_text(pose_frame(2, moving=True))
        with client.websocket_connect("/ws/ui") as ui:
            ui.receive_text()
            _, frame = command_frame("undock")
            ui.send_text(frame)
            ack = recv_until(ui, "command.ack")
            assert ack["data"]["accepted"] is False
            assert "still moving" in ack["data"]["reason"]


def test_gated_rejection_is_audited(client):
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame())
        robot.receive_text()
        robot.send_text(base_state_frame(1, charge_state="charging"))
        robot.send_text(pose_frame(2))
        with client.websocket_connect("/ws/ui") as ui:
            ui.receive_text()
            command_id, frame = command_frame("motor_enable")
            ui.send_text(frame)
            recv_until(ui, "command.ack")

    audit = client.get("/api/commands").json()
    entry = next(row for row in audit if row["command_id"] == command_id)
    assert entry["command"] == "motor_enable"
    assert entry["outcome"] == "rejected"


def test_capabilities_reach_the_browser(client):
    with client.websocket_connect("/ws/ui") as ui:
        ui.receive_text()  # snapshot, sent before any robot connected
        with client.websocket_connect("/ws/robot?token=test-token") as robot:
            robot.send_text(hello_frame())
            robot.receive_text()
            frame = recv_until(ui, "state.capabilities")
            assert "undock" in frame["data"]["capabilities"]


def test_forwarded_command_is_stamped_operator_authorized(client):
    """The robot's guarded operations trust this flag, so the server sets it
    from the verified session — a browser cannot authorize itself."""
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame())
        robot.receive_text()
        robot.send_text(base_state_frame(1))
        robot.send_text(pose_frame(2))
        with client.websocket_connect("/ws/ui") as ui:
            ui.receive_text()
            command_id = str(uuid.uuid4())
            ui.send_text(encode("command.request", "patrolbot-01", 1, {
                "command_id": command_id, "command": "charge_release",
                "operator_authorized": False,  # browser says no...
            }))
            forwarded = json.loads(robot.receive_text())
            assert forwarded["data"]["operator_authorized"] is True  # ...server decides


def test_unauthorized_request_is_never_forwarded(tmp_path):
    """An observer's request is rejected outright, so the robot never sees a
    frame for it — authorized or otherwise."""
    from app.authentication.sessions import COOKIE_NAME, issue

    settings = Settings(robot_token="test-token", database_path=str(tmp_path / "o.db"),
                        auth_mode="oidc", session_secret="s3cret")
    with TestClient(create_app(settings)) as oidc_client:
        robot_cm = oidc_client.websocket_connect("/ws/robot?token=test-token")
        robot = robot_cm.__enter__()
        try:
            robot.send_text(hello_frame())
            robot.receive_text()
            robot.send_text(base_state_frame(1))
            robot.send_text(pose_frame(2))
            token = issue("s3cret", {"id": 7, "username": "viewer",
                                     "display_name": "viewer", "role": "observer"}, 3600)
            with oidc_client.websocket_connect(
                    "/ws/ui", headers={"cookie": f"{COOKIE_NAME}={token}"}) as ui:
                ui.receive_text()
                ui.send_text(encode("command.request", "patrolbot-01", 1, {
                    "command_id": str(uuid.uuid4()), "command": "undock",
                    "operator_authorized": True,  # forged
                }))
                ack = recv_until(ui, "command.ack")
                assert ack["data"]["accepted"] is False
                assert "read-only" in ack["data"]["reason"].lower()
        finally:
            robot_cm.__exit__(None, None, None)


def test_later_hello_updates_capabilities_without_a_reconnect(client):
    """The dock manager can start after the bridge does. A re-announced hello
    must un-grey the control on an already-open dashboard."""
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame(capabilities=("pose",)))
        robot.receive_text()
        with client.websocket_connect("/ws/ui") as ui:
            snapshot = json.loads(ui.receive_text())
            assert "undock" not in snapshot["data"]["capabilities"]

            robot.send_text(encode("robot.hello", "patrolbot-01", 5, {
                "protocol_version": 1, "capabilities": ["pose", "undock"],
                "map_version": 0, "software_version": "test",
            }))
            frame = recv_until(ui, "state.capabilities")
            assert "undock" in frame["data"]["capabilities"]

    # ...and it sticks for the next browser to connect.
    with client.websocket_connect("/ws/ui") as ui:
        assert "undock" in json.loads(ui.receive_text())["data"]["capabilities"]


def test_undock_allowed_once_the_robot_advertises_it_late(client):
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame(capabilities=("pose",)))
        robot.receive_text()
        robot.send_text(base_state_frame(1, charge_state="charging"))
        robot.send_text(pose_frame(2))
        with client.websocket_connect("/ws/ui") as ui:
            ui.receive_text()
            _, frame = command_frame("undock")
            ui.send_text(frame)
            ack = recv_until(ui, "command.ack")
            assert "not commissioned" in ack["data"]["reason"]

            robot.send_text(encode("robot.hello", "patrolbot-01", 5, {
                "protocol_version": 1, "capabilities": ["pose", "undock"],
                "map_version": 0, "software_version": "test",
            }))
            recv_until(ui, "state.capabilities")
            command_id, frame = command_frame("undock")
            ui.send_text(frame)
            forwarded = json.loads(robot.receive_text())
            assert forwarded["data"]["command_id"] == command_id


def test_snapshot_carries_capabilities(client):
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame())
        robot.receive_text()
        with client.websocket_connect("/ws/ui") as ui:
            snapshot = json.loads(ui.receive_text())
            assert snapshot["type"] == "server.snapshot"
            assert "undock" in snapshot["data"]["capabilities"]
