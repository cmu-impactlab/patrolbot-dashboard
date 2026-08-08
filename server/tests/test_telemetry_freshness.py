"""Motion gates must consider *when the server heard* each telemetry slice.

The failure this covers: the bridge's socket thread keeps heartbeating (and
keeps publishing cheap slices like resources) after the ROS subscription behind
base_state or pose has died. The connection stays "online", and the last
base_state payload keeps reporting the `telemetry_age` it had when it was sent
— 0.05 s, forever. Every motion gate then authorized charging, motor power,
undock off a frozen snapshot of a robot that had stopped talking.

Two layers, as in test_dock_commands.py: the pure rules, and the broker
enforcing them over a live socket.
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
STALE = gates.MAX_RECEIPT_AGE_S + 1.0


def facts(**overrides) -> gates.StateFacts:
    """A healthy robot on its dock, both slices freshly received."""
    base = dict(online=True, link_connected=True, telemetry_age=0.1,
                base_state_age=0.1, pose_age=0.1,
                hardware_state_valid=True, charge_state="charging",
                motors_enabled=False, estop_pressed=False, fault_flags=0,
                bumpers_front=False, bumpers_rear=False, localized=True,
                stationary=True, capabilities=ALL_CAPS)
    return gates.StateFacts(**{**base, **overrides})


# -- pure gates -------------------------------------------------------------

# Each gate needs a different world to be *otherwise* allowable — charge
# release wants the charger on, motor enable and dock want it off — and a gate
# that refuses for an earlier reason would prove nothing about freshness.
ALLOWABLE = {
    "charge_release": {"charge_state": "charging"},
    "motor_enable": {"charge_state": "not_charging"},
    "undock": {"charge_state": "charging"},
}


@pytest.mark.parametrize("gate", sorted(ALLOWABLE))
def test_fresh_baseline_is_allowed(gate):
    """Guards the tests below: each baseline must pass, or a refusal proves
    nothing about the age that was changed."""
    assert gates.GATES[gate](facts(**ALLOWABLE[gate])) is None


@pytest.mark.parametrize("gate", sorted(ALLOWABLE))
@pytest.mark.parametrize("age", [None, STALE], ids=["never-received", "stale"])
def test_every_gate_refuses_an_unheard_base_state(gate, age):
    reason = gates.GATES[gate](facts(base_state_age=age, **ALLOWABLE[gate]))
    assert reason is not None and "stale" in reason.lower()


@pytest.mark.parametrize("gate", sorted(ALLOWABLE))
@pytest.mark.parametrize("age", [None, STALE], ids=["never-received", "stale"])
def test_every_gate_refuses_an_unheard_pose(gate, age):
    reason = gates.GATES[gate](facts(pose_age=age, **ALLOWABLE[gate]))
    assert reason is not None and "position update" in reason.lower()


def test_a_stale_pose_does_not_read_as_stopped():
    """The dangerous direction. `stationary` is computed from the last pose, so
    a robot that was parked when its final pose arrived and has been driving
    ever since satisfied "the robot has stopped"."""
    parked_long_ago = facts(stationary=True, pose_age=STALE)
    reason = gates.charge_release_reason(parked_long_ago)
    assert reason is not None
    assert "still moving" not in reason.lower()  # the honest reason, not a guess


def test_a_live_telemetry_age_does_not_excuse_a_frozen_slice():
    """telemetry_age is a number inside the payload; base_state_age is when we
    received that payload. A frozen slice keeps the first one looking perfect."""
    frozen = facts(telemetry_age=0.05, base_state_age=STALE)
    assert frozen.telemetry_fresh is True
    assert frozen.base_state_fresh is False
    assert gates.undock_reason(frozen) is not None


def test_receipt_age_is_stricter_than_the_offline_threshold():
    """The gate has to notice before the connection state does, or it adds
    nothing: `online` is still true at MAX_RECEIPT_AGE_S."""
    settings = Settings()
    assert gates.MAX_RECEIPT_AGE_S < settings.offline_threshold_s


# -- broker enforcement over a live socket ----------------------------------

@pytest.fixture()
def client(tmp_path):
    settings = Settings(robot_token="test-token", database_path=str(tmp_path / "test.db"))
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def hello_frame() -> str:
    return encode("robot.hello", "patrolbot-01", 0, {
        "protocol_version": 1, "capabilities": list(ALL_CAPS),
        "map_version": 0, "software_version": "test",
    })


def base_state_frame(sequence: int, **overrides) -> str:
    data = {"session_generation": 1, "link_connected": True, "telemetry_age": 0.1,
            "hardware_state_valid": True, "charge_state": "charging",
            "motors_enabled": False, "estop_pressed": False, "fault_flags": 0,
            "stall_value": 0, "bumpers_front": False, "bumpers_rear": False}
    data.update(overrides)
    return encode("telemetry.base_state", "patrolbot-01", sequence, data)


def pose_frame(sequence: int) -> str:
    return encode("telemetry.pose", "patrolbot-01", sequence, {
        "x": 1.0, "y": 1.0, "yaw": 0.0, "linear_velocity": 0.0,
        "angular_velocity": 0.0, "localized": True,
    })


def resources_frame(sequence: int) -> str:
    """A slice that keeps arriving while the interesting ones have stopped —
    the bridge publishes it from its own timer, not from a robot subscription."""
    return encode("telemetry.resources", "patrolbot-01", sequence, {
        "cpu_percent": 12.0, "memory_percent": 40.0, "disk_percent": 55.0,
        "temperature_c": 44.0, "uptime_s": 900.0,
    })


def command_frame(command: str) -> tuple[str, str]:
    command_id = str(uuid.uuid4())
    return command_id, encode("command.request", "patrolbot-01", 1,
                              {"command_id": command_id, "command": command})


def recv_until(ws, wanted_type: str, limit: int = 50) -> dict:
    for _ in range(limit):
        frame = json.loads(ws.receive_text())
        if frame["type"] == wanted_type:
            return frame
    raise AssertionError(f"never received {wanted_type}")


def freeze(state, slice_name: str, seconds: float = STALE) -> None:
    """Rewind the server's receipt time for one slice, leaving every other
    slice — and the heartbeat — untouched."""
    getattr(state, slice_name).received_mono -= seconds


@pytest.mark.parametrize("frozen_slice", ["base_state", "pose"])
@pytest.mark.parametrize("command", ["charge_release", "undock"])
def test_frozen_slice_is_refused_while_the_robot_still_looks_online(
        client, frozen_slice, command):
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame())
        robot.receive_text()
        robot.send_text(base_state_frame(1))
        robot.send_text(pose_frame(2))
        with client.websocket_connect("/ws/ui") as ui:
            ui.receive_text()  # snapshot — also a sync point

            state = client.app.state.hub.primary().state
            freeze(state, frozen_slice)
            # ...and the robot keeps talking, exactly as the real bridge does.
            robot.send_text(resources_frame(3))
            with client.websocket_connect("/ws/ui") as sync:
                sync.receive_text()

            assert state.connection == "online"
            assert state.heartbeat_age() < 1.0

            _, frame = command_frame(command)
            ui.send_text(frame)
            ack = recv_until(ui, "command.ack")
            assert ack["data"]["accepted"] is False
            assert ack["data"]["reason"]


def test_refusal_is_recorded_in_the_audit(client):
    """A refusal on stale telemetry has to be visible afterwards — this is the
    case where an operator says "I pressed it and nothing happened"."""
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame())
        robot.receive_text()
        robot.send_text(base_state_frame(1))
        robot.send_text(pose_frame(2))
        with client.websocket_connect("/ws/ui") as ui:
            ui.receive_text()
            freeze(client.app.state.hub.primary().state, "base_state")
            _, frame = command_frame("undock")
            ui.send_text(frame)
            recv_until(ui, "command.ack")

    rejected = [row for row in client.get("/api/commands").json()
                if row["outcome"] == "rejected"]
    assert len(rejected) == 1
    assert "stale" in rejected[0]["rejection_reason"].lower()


def test_fresh_telemetry_still_forwards(client):
    """The other half: nothing above should have made the normal path stricter."""
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(hello_frame())
        robot.receive_text()
        robot.send_text(base_state_frame(1))
        robot.send_text(pose_frame(2))
        with client.websocket_connect("/ws/ui") as ui:
            ui.receive_text()
            command_id, frame = command_frame("undock")
            ui.send_text(frame)
            forwarded = json.loads(robot.receive_text())
            assert forwarded["data"]["command_id"] == command_id
