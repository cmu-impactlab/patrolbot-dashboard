"""Software delivery/expiry contracts; no ROS graph or real robot connection."""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace as NS
import threading
from uuid import uuid4

import pytest

from patrolbot_web_bridge.teleop import TeleopSession, read_contract
from patrolbot_web_bridge.ws_client import WsClient


class Rig:
    def __init__(self):
        self.now = 1.0
        self.sent = []
        self.published = []
        self.problem = None
        self.session = TeleopSession(
            publish=lambda *values: self.published.append(values),
            send=lambda kind, data: self.sent.append((kind, data)),
            safety=lambda: self.problem, clock=lambda: self.now)
        self.command_id = str(uuid4())
        self.context = (1, "origin-1")

    def start(self, **kwargs):
        return self.session.start(dict(command_id=self.command_id,
                                       operator_authorized=True),
                                  self.context, received_at=self.now, **kwargs)

    def frame(self, seq=0, *, linear=0.1, angular=0.0, deadman=True, context=None,
              command_id=None, received_at=None, **extra):
        return self.session.frame(dict(command_id=command_id or self.command_id,
            seq=seq, linear=linear, angular=angular, deadman=deadman, **extra),
            self.context if context is None else context,
            received_at=self.now if received_at is None else received_at)


def test_contract_matches_robot_limits_topics_and_packaged_bytes():
    root = Path(__file__).resolve().parents[4]
    packaged = Path(__file__).resolve().parents[1] / "config/web_teleop.json"
    assert packaged.read_bytes() == (root / "contracts/web-teleop.json").read_bytes()
    contract = read_contract()
    assert contract["topics"]["velocity"]["name"] == "/input/teleop"
    assert contract["velocity_limits"]["linear_m_s"] == {"min": -0.3, "max": 0.3}
    assert contract["velocity_limits"]["angular_rad_s"] == {"min": -0.7, "max": 0.7}
    priorities = contract["mux"]
    assert priorities["safety_controller"]["priority"] > priorities["undock_turn"]["priority"]
    assert priorities["undock_turn"]["priority"] > priorities["joy"]["priority"]
    assert priorities["joy"]["priority"] > priorities["teleop"]["priority"] > priorities["navigation"]["priority"]


def test_received_velocity_uses_existing_mux_source_not_final_cmd_vel():
    rig = Rig()
    assert rig.start()
    assert rig.published == [(0.0, 0.0, False)]
    assert rig.frame(linear=0.25, angular=-0.4)
    assert rig.published[-1] == (0.25, -0.4, True)


def test_preparing_session_never_accepts_input_until_own_drain_is_confirmed():
    rig = Rig()
    assert rig.start(defer_ack=True)
    assert not any(kind == "command.ack" for kind, _ in rig.sent)
    assert not rig.frame()
    rig.now += 0.533
    assert rig.session.confirm_start(rig.context)
    assert rig.frame()
    assert rig.published[-1] == (0.1, 0.0, True)


def test_release_during_pending_acceptance_cannot_become_a_late_start():
    rig = Rig()
    rig.start(defer_ack=True)
    rig.session.end("The operator released manual input.")
    assert not rig.session.confirm_start(rig.context)
    assert not rig.frame()
    assert rig.sent[-1][1]["accepted"] is False
    assert all(not active for _, _, active in rig.published)


def test_duplicate_closed_start_id_cannot_reopen_after_release():
    rig = Rig()
    rig.start()
    assert rig.frame(linear=0, deadman=False)
    assert not rig.session.active
    assert rig.published[-1] == (0.0, 0.0, False)
    assert not rig.start()


def test_sequence_replays_do_not_refresh_watchdog_or_replace_newer_input():
    rig = Rig()
    rig.start()
    assert rig.frame(seq=5)
    original_time = rig.session.last_received
    rig.now += 0.2
    assert not rig.frame(seq=4, linear=-0.2)
    assert rig.session.last_received == original_time
    rig.now += 0.101
    rig.session.tick(rig.context)
    assert not rig.session.active
    assert rig.published[-1] == (0.0, 0.0, False)
    assert rig.sent[-1][1]["outcome"] == "canceled"


@pytest.mark.parametrize("bad", [
    {"linear": float("nan")}, {"angular": float("inf")}, {"linear": 0.301},
    {"angular": -0.701}, {"seq": True}, {"seq": -1}, {"deadman": "true"},
    {"linear": 0.1, "deadman": False}, {"linear": True}, {"extra": "ignored?"},
])
def test_poison_or_unbounded_frames_end_the_session_and_publish_zero(bad):
    rig = Rig()
    rig.start()
    assert not rig.frame(**bad)
    assert not rig.session.active
    assert rig.published[-1] == (0.0, 0.0, False)


def test_transport_change_ends_permission_and_old_id_cannot_retry():
    rig = Rig()
    rig.start()
    assert not rig.frame(context=(2, "origin-1"))
    assert not rig.session.active
    assert not rig.start()


def test_hardware_fault_interrupts_even_fresh_input_without_claiming_physical_stop():
    rig = Rig()
    rig.start()
    rig.frame()
    rig.problem = "Emergency stop or stale hardware."
    rig.session.tick(rig.context)
    assert not rig.session.active
    assert rig.published[-1] == (0.0, 0.0, False)
    assert "stopped" not in rig.sent[-1][1]["detail"].lower()


@pytest.mark.parametrize("age", [-0.1, 0.301])
def test_delayed_or_future_local_receipt_does_not_forward(age):
    rig = Rig()
    rig.start()
    assert not rig.frame(received_at=rig.now - age)
    assert rig.published[-1] == (0.0, 0.0, False)


def test_watchdog_closes_known_input_lease_not_unknown_rpc_status():
    rig = Rig()
    rig.start()
    rig.frame()
    rig.now += 0.3
    rig.session.tick(rig.context)
    assert not rig.session.active
    assert rig.sent[-1][1]["outcome"] == "canceled"
    assert "timed out" in rig.sent[-1][1]["detail"]


def test_stale_start_or_absent_authorization_does_not_reserve_permission():
    rig = Rig()
    assert not rig.session.start(dict(command_id=rig.command_id,
        operator_authorized=False), rig.context, received_at=rig.now)
    assert not rig.session.start(dict(command_id=rig.command_id,
        operator_authorized=True), rig.context, received_at=rig.now - .31)
    assert not rig.session.active
    assert not rig.published


def test_ws_velocity_ingress_is_separate_from_discrete_command_fifo():
    velocities, commands = [], []
    ws = WsClient("ws://unused", "test-only", "sim-robot", lambda: None,
                  on_command=commands.append, on_teleop=velocities.append)

    class Incoming:
        def __aiter__(self):
            async def frames():
                yield json.dumps({"version": 1, "type": "teleop.velocity", "robot_id": "sim-robot",
                                  "data": {"seq": 1}})
                yield json.dumps({"type": "command.request", "data": {"command": "teleop_stop"}})
                yield json.dumps({"type": "teleop.velocity", "robot_id": "wrong-robot", "data": {}})
                yield "[]"
            return frames()

    asyncio.run(ws._drain_incoming(Incoming()))
    assert len(velocities) == 1 and velocities[0]["data"] == {"seq": 1}
    assert commands == [{"command": "teleop_stop"}]


def test_publication_failure_ends_permission_without_automatic_retry():
    rig = Rig()
    rig.start()
    def broken(*values):
        raise RuntimeError("synthetic publisher failure")
    rig.session.publish = broken
    assert not rig.frame()
    assert not rig.session.active
    assert rig.sent[-1][1]["outcome"] == "failed"
    assert "not confirmed" in rig.sent[-1][1]["detail"]
    assert not rig.frame(seq=1)
