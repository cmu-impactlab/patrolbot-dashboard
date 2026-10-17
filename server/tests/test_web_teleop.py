"""Software-only receipt tests. Fake hardware evidence exists ONLY in this lab.

No ROS, SSH, production node or physical motion is involved or claimed.
"""
import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.commands.broker import ActiveCommand
from app.database.repo import Database
from app.main import create_app
from app.protocol.envelope import decode, encode
from app.protocol.messages import BaseStateData, TeleopVelocityData
from app.settings import Settings
from app.telemetry.hub import TelemetryHub

ROBOT = "isolated-software-robot"


class Socket:
    def __init__(self):
        self.frames = []
        self.delay = False
        self.fail = False

    async def send_text(self, raw):
        if self.fail:
            raise ConnectionError("isolated send failure")
        if self.delay:
            await asyncio.Event().wait()
        self.frames.append(json.loads(raw))

    async def close(self, **kwargs):
        pass


def fake_base(**overrides):
    """Never imported by application/production code; no map/localization data."""
    data = dict(session_generation=7, link_connected=True, telemetry_age=.01,
                hardware_state_valid=True, charge_state="idle", motors_enabled=True,
                estop_pressed=False, fault_flags=0, stall_value=0,
                bumpers_front=False, bumpers_rear=False, bumpers_valid=True,
                dock_state="CLEAR_CONFIRMED", dock_state_valid=True,
                odom_epoch_valid=True, raw_odom_age_s=.01,
                lidar_state="READY", lidar_scan_age=.01)
    return BaseStateData(**dict(data, **overrides))


def operator(uid=1, can_command=True):
    return SimpleNamespace(id=uid, username=f"operator-{uid}", can_command=can_command)


async def request(lab, command="teleop_start", client=None, command_id=None, **extra):
    command_id = command_id or str(uuid.uuid4())
    env, payload = decode(encode("command.request", ROBOT, 1,
        dict(command_id=command_id, command=command, **extra)))
    await lab.hub.commands.handle_browser_request(client or lab.client, env, payload)
    return command_id


async def reply(lab, command_id, kind="command.ack", **data):
    if not data:
        data = {"accepted": True} if kind == "command.ack" else {"outcome": "canceled"}
    env, payload = decode(encode(kind, ROBOT, 1, dict(command_id=command_id, **data)))
    await lab.hub.handle_robot_command_reply(lab.session, env, payload)


async def velocity(lab, start_id, seq=0, client=None, stamp=None, **changes):
    env, payload = decode(encode("teleop.velocity", ROBOT, seq,
        dict(command_id=start_id, seq=seq, **dict(dict(linear=.1, angular=0., deadman=True), **changes)),
        timestamp=stamp))
    return await lab.hub.commands.teleop.velocity(client or lab.client, env, payload)


def samples(lab):
    return [f for f in lab.robot.frames if f["type"] == "teleop.velocity"]


def browser_frames(client):
    return [json.loads(raw) for raw, _ in client._queue]


@pytest.fixture
async def lab():
    # Explicit, isolated fake state: never bypass production gate code.
    hub = TelemetryHub(Settings(_env_file=None, default_robot_id=ROBOT))
    robot = Socket()
    session = await hub.robot_connected(ROBOT, robot)
    session.state.set_capabilities(["web_teleop_v1", "motor_disable", "software_reset"])
    session.state.record_base_state(fake_base())
    client = await hub.browser_connected(Socket(), user=operator())
    value = SimpleNamespace(hub=hub, robot=robot, session=session, client=client)
    try:
        yield value
    finally:
        robot.delay = False
        robot.fail = False
        await hub.stop()


async def started(lab):
    start_id = await request(lab)
    assert lab.robot.frames[-1]["data"]["command_id"] == start_id
    assert lab.robot.frames[-1]["data"]["operator_authorized"] is True
    await reply(lab, start_id)
    assert lab.hub.commands.teleop.owners[ROBOT].phase == "active"
    return start_id


@pytest.mark.parametrize("changes", [
    {"linear": .301}, {"linear": -.301}, {"angular": .701}, {"angular": -.701},
    {"linear": float("nan")}, {"angular": float("inf")}, {"extra": 1},
    {"seq": -1}, {"seq": 1.0}, {"seq": True}, {"deadman": 1},
    {"linear": "0.1"}, {"command_id": "not-a-uuid"},
    {"deadman": False, "linear": .1},
])
def test_velocity_schema_rejects_invalid_intent(changes):
    from pydantic import ValidationError
    data = dict(command_id=str(uuid.uuid4()), seq=0, linear=.1, angular=0., deadman=True)
    with pytest.raises(ValidationError):
        TeleopVelocityData.model_validate(dict(data, **changes))


@pytest.mark.parametrize("changes", [
    {"link_connected": False}, {"hardware_state_valid": False},
    {"telemetry_age": .51}, {"raw_odom_age_s": .51}, {"raw_odom_age_s": None},
    {"lidar_scan_age": .51}, {"lidar_scan_age": -1}, {"lidar_state": "OFF"},
    {"odom_epoch_valid": False}, {"motors_enabled": False},
    {"estop_pressed": True}, {"fault_flags": 4}, {"undock_active": True},
    {"dock_state_valid": False}, {"dock_state_valid": None}, {"dock_state": None},
    {"dock_state": "DOCKED_CONFIRMED"}, {"bumpers_front": True},
    {"bumpers_rear": True}, {"bumpers_valid": None},
])
async def test_start_fails_closed_on_hardware(lab, changes):
    lab.session.state.record_base_state(fake_base(**changes))
    start_id = await request(lab)
    assert not lab.robot.frames
    assert ROBOT not in lab.hub.commands.teleop.owners
    ack = browser_frames(lab.client)[-1]
    assert ack["data"]["command_id"] == start_id and not ack["data"]["accepted"]


async def test_start_needs_no_map_profile_or_localization(lab):
    assert lab.session.state.pose.data is None
    assert lab.session.state.base_state.data.localization_recovery_required
    await started(lab)


@pytest.mark.parametrize("mode", ["offline", "stale", "capability", "receipt", "prior_socket"])
async def test_start_rejects_missing_connection_evidence(lab, mode):
    if mode in {"offline", "stale"}:
        lab.session.state.connection = mode
    elif mode == "capability":
        lab.session.state.set_capabilities([])
    elif mode == "receipt":
        lab.session.state.base_state.received_mono -= .6
    else:
        lab.session.connected_mono = time.monotonic() + 1
    await request(lab)
    assert not lab.robot.frames


@pytest.mark.parametrize("command", ["teleop_start", "teleop_stop", "stop", "motor_disable", "software_reset"])
async def test_observer_never_commands_or_revokes(lab, command):
    start_id = await started(lab)
    observer = await lab.hub.browser_connected(Socket(), user=operator(2, False))
    before = len(lab.robot.frames)
    await request(lab, command, client=observer)
    assert not await velocity(lab, start_id, client=observer, deadman=False, linear=0.)
    assert len(lab.robot.frames) == before
    assert lab.hub.commands.teleop.owners[ROBOT].phase == "active"


async def test_frames_wait_for_robot_acceptance(lab):
    start_id = await request(lab)
    assert not await velocity(lab, start_id)
    owner = lab.hub.commands.teleop.owners[ROBOT]
    assert owner.seq == -1
    await reply(lab, start_id)
    assert await velocity(lab, start_id)
    assert len(samples(lab)) == 1


async def test_exact_browser_identity_even_same_user_knowing_uuid(lab):
    start_id = await started(lab)
    tab = await lab.hub.browser_connected(Socket(), user=lab.client.user)
    assert not await velocity(lab, start_id, client=tab)
    assert not await velocity(lab, start_id, client=tab, deadman=False, linear=0.)
    await request(lab, client=tab)
    assert lab.hub.commands.teleop.owners[ROBOT].client is lab.client
    assert lab.hub.commands.lease.holder(ROBOT).client is lab.client
    assert await velocity(lab, start_id)


async def test_seq_replay_wrong_uuid_and_invalid_frames_not_consumed(lab):
    start_id = await started(lab)
    assert not await velocity(lab, str(uuid.uuid4()), seq=99)
    assert await velocity(lab, start_id, seq=1)
    assert not await velocity(lab, start_id, seq=1)
    assert not await velocity(lab, start_id, seq=0)
    assert await velocity(lab, start_id, seq=2)
    assert len(samples(lab)) == 2


@pytest.mark.parametrize("stamp", [
    "2020-01-01T00:00:00Z", "2026-01-01T00:00:00", "not-time",
    "9999-01-01T00:00:00Z",
])
async def test_stale_future_naive_or_bad_timestamp_rejected(lab, stamp):
    start_id = await started(lab)
    assert not await velocity(lab, start_id, stamp=stamp)
    assert await velocity(lab, start_id)  # bad time did not burn sequence


async def test_clock_future_tolerance(lab):
    start_id = await started(lab)
    stamp = (datetime.now(timezone.utc) + timedelta(seconds=.2)).isoformat()
    assert not await velocity(lab, start_id, stamp=stamp)
    stamp = (datetime.now(timezone.utc) + timedelta(seconds=.01)).isoformat()
    assert await velocity(lab, start_id, stamp=stamp)


async def test_stream_rate_cap_separate_and_release_bypasses_it(lab):
    lab.hub.settings.teleop_frames_per_s = 2
    start_id = await started(lab)
    assert await velocity(lab, start_id, seq=0)
    assert await velocity(lab, start_id, seq=1)
    assert not await velocity(lab, start_id, seq=2)
    assert await velocity(lab, start_id, seq=2, deadman=False, linear=0.)
    assert samples(lab)[-1]["data"]["deadman"] is False
    assert lab.hub.commands.teleop.owners[ROBOT].phase == "closing"
    assert not await velocity(lab, start_id, seq=3)


async def test_release_while_start_pending_blocks_late_acceptance(lab):
    start_id = await request(lab)
    assert await velocity(lab, start_id, deadman=False, linear=0.)
    await reply(lab, start_id)
    acks = [f for f in browser_frames(lab.client) if f["type"] == "command.ack"]
    assert not acks[-1]["data"]["accepted"]
    assert not await velocity(lab, start_id, seq=1)
    assert start_id in lab.hub.commands.active


@pytest.mark.parametrize("command", ["teleop_stop", "stop", "motor_disable"])
async def test_authorized_other_tab_stop_bypasses_rate_and_lease(lab, command):
    start_id = await started(lab)
    other = await lab.hub.browser_connected(Socket(), user=operator(2))
    lab.hub.commands.rate.allow = lambda _: False
    await request(lab, command, client=other)
    assert any(f["type"] == "command.request" and f["data"]["command"] == command
               for f in lab.robot.frames)
    assert samples(lab)[-1]["data"]["deadman"] is False
    assert not await velocity(lab, start_id)


async def test_stop_before_start_ack_and_unknown_hold_until_both_terminals(lab):
    start_id = await request(lab)
    stop_id = await request(lab, "teleop_stop")
    await reply(lab, stop_id, "command.result", outcome="succeeded")
    await reply(lab, start_id)  # accepted after Stop must not arm anyone
    assert not await velocity(lab, start_id)
    count = len(lab.robot.frames)
    await request(lab)
    assert len(lab.robot.frames) == count
    await reply(lab, start_id, "command.result", outcome="canceled")
    assert ROBOT not in lab.hub.commands.teleop.owners
    await started(lab)


async def test_start_terminal_still_waits_for_pending_stop_terminal(lab):
    start_id = await started(lab)
    stop_id = await request(lab, "teleop_stop")
    await reply(lab, start_id, "command.result", outcome="canceled")
    assert ROBOT in lab.hub.commands.teleop.owners
    await reply(lab, stop_id, "command.result", outcome="succeeded")
    assert ROBOT not in lab.hub.commands.teleop.owners


async def test_robot_decline_releases_known_not_accepted_start(lab):
    start_id = await request(lab)
    await reply(lab, start_id, accepted=False, reason="base down")
    assert ROBOT not in lab.hub.commands.teleop.owners
    assert start_id not in lab.hub.commands.active


@pytest.mark.parametrize("command", ["navigate_to_pose", "set_initial_pose", "undock", "charge_release", "motor_enable", "set_lidar_power"])
async def test_other_physical_operation_revokes_and_cannot_run_during_manual(lab, command):
    start_id = await started(lab)
    await request(lab, command, **({"enabled": False} if command == "set_lidar_power" else {}))
    assert not any(f["type"] == "command.request" and f["data"]["command"] == command
                   for f in lab.robot.frames)
    assert not await velocity(lab, start_id)
    assert samples(lab)[-1]["data"]["deadman"] is False


@pytest.mark.parametrize("command", ["navigate_to_pose", "set_initial_pose", "undock", "charge_release"])
async def test_start_rejects_existing_operation_no_nav_resume(lab, command):
    old = ActiveCommand(str(uuid.uuid4()), command, ROBOT)
    lab.hub.commands.active[old.command_id] = old
    await request(lab)
    assert not lab.robot.frames  # no cancellation, mutation or manual preemption
    assert old.command_id in lab.hub.commands.active
    if command in {"navigate_to_pose", "set_initial_pose"}:
        assert "Stop navigation" in browser_frames(lab.client)[-1]["data"]["reason"]


async def test_lease_takeover_ends_old_session_without_rearming(lab):
    start_id = await started(lab)
    other = await lab.hub.browser_connected(Socket(), user=operator(2))
    await request(lab, client=other, takeover=True)
    assert lab.hub.commands.teleop.owners[ROBOT].phase == "closing"
    assert not await velocity(lab, start_id)


async def test_external_lease_change_revokes(lab):
    start_id = await started(lab)
    other = await lab.hub.browser_connected(Socket(), user=lab.client.user)
    lab.hub.commands.lease.acquire(ROBOT, 1, "same user", other)
    await lab.hub.commands.teleop.changed(lab.session)
    assert not await velocity(lab, start_id)
    assert samples(lab)[-1]["data"]["deadman"] is False


async def test_producer_disconnect_revokes_and_new_tab_cannot_resume(lab):
    start_id = await started(lab)
    lab.hub.browser_disconnected(lab.client)
    await asyncio.sleep(.01)
    other = await lab.hub.browser_connected(Socket(), user=lab.client.user)
    assert not await velocity(lab, start_id, client=other)
    assert samples(lab)[-1]["data"]["deadman"] is False
    count = len(lab.robot.frames)
    await request(lab, client=other)
    assert len(lab.robot.frames) == count


async def test_silence_uses_300ms_not_20s_and_no_physical_stop_claim(lab):
    start_id = await started(lab)
    assert await velocity(lab, start_id)
    await asyncio.sleep(.37)
    assert lab.hub.commands.teleop.owners[ROBOT].phase != "active"
    assert samples(lab)[-1]["data"]["deadman"] is False
    assert start_id in lab.hub.commands.active  # no fake terminal success


@pytest.mark.parametrize("change", ["capability", "receipt", "generation", "estop", "motors", "fault", "lidar"])
async def test_ongoing_hardware_or_capability_loss_revokes(lab, change):
    start_id = await started(lab)
    if change == "capability":
        lab.session.state.set_capabilities([])
    elif change == "receipt":
        lab.session.state.base_state.received_mono -= 1
    else:
        field, value = {"generation": ("session_generation", 8), "estop": ("estop_pressed", True),
                        "motors": ("motors_enabled", False), "fault": ("fault_flags", 1),
                        "lidar": ("lidar_state", "FAULT")}[change]
        lab.session.state.record_base_state(fake_base(**{field: value}))
    await lab.hub.commands.teleop.changed(lab.session)
    assert not await velocity(lab, start_id)
    assert lab.hub.commands.teleop.owners[ROBOT].phase in {"closing", "unknown"}


async def test_robot_reconnect_is_new_identity_and_old_ack_cannot_arm(lab):
    start_id = await request(lab)
    previous = lab.session
    new_socket = Socket()
    replacement = await lab.hub.robot_connected(ROBOT, new_socket)
    replacement.state.set_capabilities(["web_teleop_v1"])
    replacement.state.record_base_state(fake_base())
    assert replacement is not previous and replacement.generation > previous.generation
    await reply(lab, start_id)  # old session reply dropped by hub
    env, payload = decode(encode("command.ack", ROBOT, 4, {"command_id": start_id, "accepted": True}))
    await lab.hub.handle_robot_command_reply(replacement, env, payload)  # new cannot ack old start
    assert lab.hub.commands.teleop.owners[ROBOT].phase != "active"
    assert not await velocity(lab, start_id)
    await request(lab)
    assert not new_socket.frames  # old uncertain ownership blocks replacement
    old_data = previous.state.base_state.data
    env, payload = decode(encode("telemetry.base_state", ROBOT, 5, fake_base(fault_flags=99)))
    await lab.hub.handle_robot_message(previous, env, payload)
    assert replacement.state.base_state.data is old_data


async def test_robot_disconnect_keeps_unknown_ownership(lab):
    start_id = await started(lab)
    await lab.hub.robot_disconnected(lab.session, lab.robot)
    assert lab.session.websocket is None
    assert ROBOT in lab.hub.commands.teleop.owners
    assert not await velocity(lab, start_id)


async def test_f710_terminal_permanently_ends_web_session(lab):
    start_id = await started(lab)
    await reply(lab, start_id, "command.result", outcome="canceled", detail="F710 active; manual web input ended.")
    assert ROBOT not in lab.hub.commands.teleop.owners
    assert not await velocity(lab, start_id)
    # Only a new deliberate human start, never F710 release, can arm again.
    assert await started(lab) != start_id


async def test_ack_timeout_holds_unknown_instead_of_allowing_new_start(lab):
    lab.hub.commands.ack_timeout_s = .02
    start_id = await request(lab)
    await asyncio.sleep(.05)
    assert lab.hub.commands.active[start_id].uncertain
    assert ROBOT in lab.hub.commands.teleop.owners
    count = len(lab.robot.frames)
    await request(lab)
    assert len(lab.robot.frames) == count
    await reply(lab, start_id)
    assert not await velocity(lab, start_id)


async def test_send_failure_unknown_not_robot_stopped(lab):
    lab.robot.fail = True
    start_id = await request(lab)
    assert lab.hub.commands.active[start_id].uncertain
    assert lab.hub.commands.teleop.owners[ROBOT].phase == "unknown"
    results = [f for f in browser_frames(lab.client) if f["type"] == "command.result"]
    assert results[-1]["data"]["outcome"] == "timeout"
    assert "no physical stop" in results[-1]["data"]["detail"]


async def test_bounded_sends_and_no_nonzero_queue(lab):
    lab.hub.settings.teleop_send_timeout_s = .02
    start_id = await started(lab)
    lab.robot.delay = True
    pending = asyncio.create_task(velocity(lab, start_id))
    await asyncio.sleep(.005)
    assert not await velocity(lab, start_id, seq=1)  # no backlog behind first send
    assert not await asyncio.wait_for(pending, timeout=.2)
    assert not samples(lab)  # no delayed nonzero intent
    assert lab.hub.commands.teleop.owners[ROBOT].phase != "active"


async def test_reset_explicitly_retires_unknown_without_resuming(lab):
    start_id = await started(lab)
    await request(lab, "software_reset")
    assert ROBOT not in lab.hub.commands.teleop.owners
    assert start_id not in lab.hub.commands.active
    assert not await velocity(lab, start_id)
    before = len(lab.robot.frames)
    await request(lab)
    assert len(lab.robot.frames) == before  # reset itself remains a barrier


async def test_persistent_start_dedupe_and_audit_not_per_velocity(lab, tmp_path):
    db = Database(str(tmp_path / "isolated.db"))
    await db.init()
    lab.hub.db = db
    try:
        start_id = await started(lab)
        assert await velocity(lab, start_id)
        # End by actual robot terminal; only the start is in the audit.
        await reply(lab, start_id, "command.result", outcome="canceled")
        lab.hub.commands._seen.clear()  # simulate in-memory dedupe cache lost
        before = len(lab.robot.frames)
        await request(lab, command_id=start_id)
        assert len(lab.robot.frames) == before
        assert "Duplicate" in browser_frames(lab.client)[-1]["data"]["reason"]
        assert await db.command_recorded(start_id)
    finally:
        lab.hub.db = None
        await db.close()


async def test_stop_during_start_audit_cancels_before_publication(lab, tmp_path, monkeypatch):
    db = Database(str(tmp_path / "race.db"))
    await db.init()
    lab.hub.db = db
    entered, release = asyncio.Event(), asyncio.Event()
    original = db.add_command_audit

    async def delayed_audit(robot_id, command_id, command, *args, **kwargs):
        if command == "teleop_start":
            entered.set()
            await release.wait()
        await original(robot_id, command_id, command, *args, **kwargs)

    monkeypatch.setattr(db, "add_command_audit", delayed_audit)
    pending = asyncio.create_task(request(lab))
    try:
        await entered.wait()
        stop_id = await request(lab, "teleop_stop")
        release.set()
        start_id = await pending
        assert not any(f["type"] == "command.request" and f["data"]["command"] == "teleop_start"
                       for f in lab.robot.frames)
        assert start_id not in lab.hub.commands.active
        assert ROBOT in lab.hub.commands.teleop.owners  # wait for actual Stop result
        await reply(lab, stop_id, "command.result", outcome="succeeded")
        assert ROBOT not in lab.hub.commands.teleop.owners
    finally:
        release.set()
        await pending
        lab.hub.db = None
        await db.close()


async def test_queued_start_is_canceled_by_stop_before_admission(lab):
    lock = asyncio.Lock()
    await lock.acquire()
    lab.hub.commands._admission_locks[ROBOT] = lock
    pending = asyncio.create_task(request(lab))
    await asyncio.sleep(0)
    await request(lab, "teleop_stop")
    lock.release()
    await pending
    assert not any(f["type"] == "command.request" and f["data"]["command"] == "teleop_start"
                   for f in lab.robot.frames)


async def test_hardware_rechecked_after_audit_await(lab, tmp_path, monkeypatch):
    db = Database(str(tmp_path / "freshness.db"))
    await db.init()
    lab.hub.db = db
    original = db.add_command_audit

    async def expire_during_audit(*args, **kwargs):
        await original(*args, **kwargs)
        lab.session.state.base_state.received_mono -= 1

    monkeypatch.setattr(db, "add_command_audit", expire_during_audit)
    try:
        await request(lab)
        assert not lab.robot.frames
        assert ROBOT not in lab.hub.commands.teleop.owners
    finally:
        lab.hub.db = None
        await db.close()


async def test_generation_checked_after_await_and_never_replays_to_new_socket(lab, monkeypatch):
    start_id = await started(lab)
    original = lab.robot.send_text

    async def change_generation_during_send(raw):
        await original(raw)
        if json.loads(raw)["type"] == "teleop.velocity":
            lab.session.generation += 1

    monkeypatch.setattr(lab.robot, "send_text", change_generation_during_send)
    assert not await velocity(lab, start_id)
    assert lab.hub.commands.teleop.owners[ROBOT].phase == "unknown"
    before = len(lab.robot.frames)
    assert not await velocity(lab, start_id, seq=1)
    assert len(lab.robot.frames) == before


async def test_duplicate_robot_acceptance_does_not_cancel_active_owner(lab):
    start_id = await started(lab)
    await reply(lab, start_id)
    assert await velocity(lab, start_id)


async def test_pending_zero_drain_can_exceed_300ms_before_robot_acceptance(lab):
    start_id = await request(lab)
    for _ in range(4):
        await asyncio.sleep(.15)
        lab.session.state.record_base_state(fake_base())
        assert lab.hub.commands.teleop.owners[ROBOT].phase == "pending"
        assert not await velocity(lab, start_id)
    assert not samples(lab)  # pending remains zero-only, never forwards intent
    await reply(lab, start_id)
    assert await velocity(lab, start_id)


async def test_valid_owner_release_still_forwards_while_stop_pending(lab):
    start_id = await started(lab)
    await request(lab, "teleop_stop")
    before = len(samples(lab))
    assert await velocity(lab, start_id, seq=0, deadman=False, linear=0.)
    assert len(samples(lab)) == before + 1


async def test_real_robot_timeout_result_keeps_unknown_barrier(lab):
    start_id = await started(lab)
    await reply(lab, start_id, "command.result", outcome="timeout", detail="publication health unknown")
    assert lab.hub.commands.teleop.owners[ROBOT].phase == "unknown"
    assert start_id in lab.hub.commands.active
    assert not await velocity(lab, start_id)


async def test_command_start_unknown_fields_and_goals_rejected():
    from app.protocol.envelope import ProtocolError
    for extra in ({"extra": True}, {"goal": {"x": 0., "y": 0.}}, {"enabled": True}):
        with pytest.raises(ProtocolError):
            decode(encode("command.request", ROBOT, 1,
                dict(command="teleop_start", command_id=str(uuid.uuid4()), **extra)))


def recv_type(ws, kind):
    for _ in range(100):
        frame = json.loads(ws.receive_text())
        if frame["type"] == kind:
            return frame
    raise AssertionError(f"missing {kind}")


def test_gateway_receipt_exact_tab_and_stop_flood_bypass(tmp_path):
    settings = Settings(robot_token="isolated-token", database_path=str(tmp_path / "ws.db"),
                        default_robot_id=ROBOT, max_ui_frames_per_s=1)
    with TestClient(create_app(settings)) as client:
        with client.websocket_connect("/ws/robot?token=isolated-token") as robot:
            robot.send_text(encode("robot.hello", ROBOT, 0, dict(protocol_version=1,
                capabilities=["web_teleop_v1"], map_version=0, software_version="isolated")))
            recv_type(robot, "server.hello_ack")
            robot.send_text(encode("telemetry.base_state", ROBOT, 1, fake_base()))
            with client.websocket_connect("/ws/ui") as owner, client.websocket_connect("/ws/ui") as other:
                recv_type(owner, "server.snapshot")
                recv_type(other, "server.snapshot")
                start_id = str(uuid.uuid4())
                owner.send_text(encode("command.request", ROBOT, 1, dict(command="teleop_start", command_id=start_id)))
                assert recv_type(robot, "command.request")["data"]["command_id"] == start_id
                robot.send_text(encode("command.ack", ROBOT, 2, dict(command_id=start_id, accepted=True)))
                assert recv_type(owner, "command.ack")["data"]["accepted"]
                assert recv_type(other, "command.ack")["data"]["accepted"]
                # Same local user + leaked UUID is not the producing socket.
                other.send_text(encode("teleop.velocity", ROBOT, 1,
                    dict(command_id=start_id, seq=20, linear=.2, angular=0., deadman=True)))
                owner.send_text(encode("teleop.velocity", ROBOT, 3,
                    dict(command_id=start_id, seq=1, linear=.1, angular=0., deadman=True)))
                received = recv_type(robot, "teleop.velocity")
                assert received["data"]["seq"] == 1 and received["data"]["linear"] == .1
                other.send_text(encode("command.request", ROBOT, 2,
                    dict(command="teleop_stop", command_id=str(uuid.uuid4()))))
                assert recv_type(robot, "teleop.velocity")["data"]["deadman"] is False
                assert recv_type(robot, "command.request")["data"]["command"] == "teleop_stop"


def test_production_sbc_down_does_not_gain_fake_online_state(tmp_path):
    # Direct hub with production Settings: tests do not make a production
    # server insecure or add a runtime fake-hardware option.
    hub = TelemetryHub(Settings(environment="production", database_path=str(tmp_path / "p.db")))
    session = hub.get_or_create_state(ROBOT)
    assert session.websocket is None and session.state.connection == "offline"
    assert session.state.base_state.data is None
    assert not hub.commands.teleop.owners
