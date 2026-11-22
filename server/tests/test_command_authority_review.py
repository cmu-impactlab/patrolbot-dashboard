"""R3/R6 regressions: isolated in-process sockets only, never robot hardware."""
import asyncio
import json
import time
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.authentication.local import User
from app.authentication.sessions import COOKIE_NAME, issue
from app.commands.broker import ActiveCommand
from app.database.repo import Database
from app.main import create_app
from app.protocol.envelope import decode, encode
from app.protocol.messages import MotionData, PoseData
from app.settings import Settings
from test_web_teleop import (
    ROBOT, Socket, browser_frames, fake_base, lab, reply, request, samples,
    started, velocity,
)


def oidc(lab):
    settings = lab.hub.settings
    settings.auth_mode = "oidc"
    settings.session_secret = "isolated-authority-test"
    settings.operator_usernames = "driver"
    lab.client.user = User(1, "driver", "Driver", "operator")
    token = issue(settings.session_secret, vars(lab.client.user), 300)
    lab.client.websocket.cookies = {COOKIE_NAME: token}
    return token


def invalidate(lab, mode, monkeypatch):
    if mode == "expiry":
        monkeypatch.setattr("app.authentication.sessions.time",
                            SimpleNamespace(time=lambda: 10**12))
    elif mode == "demotion":
        lab.hub.settings.operator_usernames = ""
    elif mode == "allowlist":
        lab.hub.settings.allowed_usernames = "someone-else"
    elif mode == "signature":
        lab.hub.settings.session_secret = "rotated"
    else:
        lab.client.websocket.cookies = {}


@pytest.mark.parametrize("mode", ["expiry", "demotion", "allowlist", "signature", "missing"])
@pytest.mark.parametrize("command", [
    "motor_enable", "navigate_to_pose", "undock", "teleop_start",
    "set_initial_pose", "charge_release", "software_reset", "dock_reset", "set_lidar_power",
])
async def test_stale_socket_cannot_admit_any_new_physical_authority(lab, monkeypatch, mode, command):
    oidc(lab)
    invalidate(lab, mode, monkeypatch)
    command_id = await request(lab, command, **({"enabled": True} if command == "set_lidar_power" else {}))
    assert not lab.robot.frames
    assert command_id not in lab.hub.commands.active
    ack = browser_frames(lab.client)[-1]["data"]
    assert ack["command_id"] == command_id and not ack["accepted"]


@pytest.mark.parametrize("mode", ["expiry", "demotion", "allowlist", "signature", "missing"])
@pytest.mark.parametrize("command", ["stop", "motor_disable", "teleop_stop"])
async def test_former_operator_can_only_end_input_after_authority_loss(lab, monkeypatch, mode, command):
    oidc(lab)
    start_id = await started(lab)
    invalidate(lab, mode, monkeypatch)
    await request(lab, command)
    assert samples(lab)[-1]["data"]["deadman"] is False
    assert any(frame["type"] == "command.request" and frame["data"]["command"] == command
               for frame in lab.robot.frames)
    assert not await velocity(lab, start_id)
    assert start_id in lab.hub.commands.active  # stopping is not proof of stopped
    assert not lab.hub.commands.can_command(lab.client)


@pytest.mark.parametrize("mode", ["expiry", "demotion", "allowlist", "signature", "missing"])
@pytest.mark.parametrize("path", ["velocity", "watchdog", "late_ack", "release"])
async def test_manual_authority_is_live_not_connection_cached(lab, monkeypatch, mode, path):
    oidc(lab)
    start_id = await (request(lab) if path == "late_ack" else started(lab))
    owner = lab.hub.commands.teleop.owners[ROBOT]
    invalidate(lab, mode, monkeypatch)
    if path == "velocity":
        assert not await velocity(lab, start_id)
    elif path == "late_ack":
        await reply(lab, start_id)
        assert not browser_frames(lab.client)[-1]["data"].get("accepted", False)
    elif path == "release":
        assert await velocity(lab, start_id, deadman=False, linear=0.)
    else:
        # Well before the 300ms producer watchdog: a silent socket loses its
        # authority without needing another browser frame or telemetry tick.
        owner.last_frame = time.monotonic() + 10
        await asyncio.sleep(.09)
    assert owner.phase in {"closing", "unknown"}
    assert samples(lab)[-1]["data"]["deadman"] is False
    assert not any(frame["data"]["deadman"] for frame in samples(lab))
    assert not await velocity(lab, start_id, seq=10)
    assert start_id in lab.hub.commands.active
    assert lab.hub.commands.lease.holder(ROBOT) is None
    assert not any(frame["type"] == "command.request" and
                   frame["data"]["command"] in {"motor_enable", "software_reset"}
                   for frame in lab.robot.frames)


@pytest.mark.parametrize("command", ["stop", "motor_disable", "teleop_stop"])
async def test_observer_socket_never_inherits_cleanup_permission(lab, monkeypatch, command):
    token = oidc(lab)
    lab.hub.settings.operator_usernames = ""
    lab.client = await lab.hub.browser_connected(
        Socket(), user=User(1, "driver", "Driver", "observer"))
    lab.client.websocket.cookies = {COOKIE_NAME: token}
    invalidate(lab, "expiry", monkeypatch)
    await request(lab, command)
    assert not lab.robot.frames


async def test_freshly_promoted_observer_can_still_release_after_expiry(lab, monkeypatch):
    token = oidc(lab)
    lab.client = await lab.hub.browser_connected(
        Socket(), user=User(1, "driver", "Driver", "observer"))
    lab.client.websocket.cookies = {COOKIE_NAME: token}
    start_id = await started(lab)  # current policy, not the cached observer role
    invalidate(lab, "expiry", monkeypatch)
    assert await velocity(lab, start_id, deadman=False, linear=0.)
    assert samples(lab)[-1]["data"]["deadman"] is False
    assert any(frame["type"] == "command.request" and frame["data"]["command"] == "teleop_stop"
               for frame in lab.robot.frames)
    assert start_id in lab.hub.commands.active
    assert lab.hub.commands.lease.holder(ROBOT) is None


@pytest.mark.parametrize("field", ["id", "username"])
async def test_revalidated_credentials_must_match_the_connected_principal(lab, field):
    oidc(lab)
    principal = dict(vars(lab.client.user), **{field: 2 if field == "id" else "another-driver"})
    lab.hub.settings.operator_usernames += ",another-driver"
    lab.client.websocket.cookies = {COOKIE_NAME: issue(
        lab.hub.settings.session_secret, principal, 300)}
    await request(lab, "motor_enable")
    assert not lab.robot.frames


@pytest.mark.parametrize("command", ["motor_enable", "teleop_start", "software_reset"])
async def test_authority_rechecked_after_audit_await(lab, tmp_path, monkeypatch, command):
    oidc(lab)
    lab.session.state.set_capabilities(["web_teleop_v1", "motor_enable", "software_reset"])
    db = Database(str(tmp_path / "admission.db"))
    await db.init()
    lab.hub.db = db
    original = db.add_command_audit

    async def expire(*args, **kwargs):
        await original(*args, **kwargs)
        invalidate(lab, "expiry", monkeypatch)

    monkeypatch.setattr(db, "add_command_audit", expire)
    try:
        command_id = await request(lab, command)
        assert not lab.robot.frames
        assert command_id not in lab.hub.commands.active
        assert ROBOT not in lab.hub.commands.teleop.owners
        assert await db.command_recorded(command_id)
    finally:
        lab.hub.db = None
        await db.close()


@pytest.mark.parametrize("change,reason", [
    ("expired", "fresh, valid session-bound measured-motion"),
    ("moving", "still moving"),
    ("new_session", "fresh, valid session-bound measured-motion"),
])
async def test_undock_rechecks_measured_stop_after_audit_await(lab, tmp_path, monkeypatch, change, reason):
    state = lab.session.state
    state.set_capabilities(["undock"])
    state.record_base_state(fake_base(dock_state="DOCKED_CONFIRMED",
                                     undock_profile_commissioned=True,
                                     odom_epoch="isolated-epoch", odom_epoch_generation=1))
    stopped = MotionData(source="raw_odom", linear_velocity=0., angular_velocity=0.,
                         sample_age_s=.01, receipt_age_s=.01, session_generation=7,
                         odom_epoch="isolated-epoch", odom_epoch_generation=1)
    state.record_motion(stopped)
    db = Database(str(tmp_path / "undock-admission.db"))
    await db.init()
    lab.hub.db = db
    original = db.add_command_audit

    async def change_motion(*args, **kwargs):
        await original(*args, **kwargs)
        if change == "expired":
            state.motion.received_mono = time.monotonic() - 1.6
        elif change == "moving":
            state.record_motion(stopped.model_copy(update={"linear_velocity": .1}))
        else:
            state.record_base_state(state.base_state.data.model_copy(update={"session_generation": 8}))

    monkeypatch.setattr(db, "add_command_audit", change_motion)
    try:
        assert lab.hub.commands._validate_robot_state(lab.session, "undock") is None
        command_id = await request(lab, "undock")
        assert not lab.robot.frames
        assert command_id not in lab.hub.commands.active
        assert await db.command_recorded(command_id)
        ack = next(f["data"] for f in browser_frames(lab.client) if f["type"] == "command.ack")
        assert ack["command_id"] == command_id and not ack["accepted"]
        assert reason in ack["reason"]
    finally:
        lab.hub.db = None
        await db.close()


@pytest.mark.parametrize("same_origin", [True, False])
async def test_undock_discards_stop_on_origin_transition(lab, same_origin):
    state = lab.session.state
    state.set_capabilities(["undock"])
    base = fake_base(dock_state="DOCKED_CONFIRMED", undock_profile_commissioned=True,
                     odom_epoch="old-epoch", odom_epoch_generation=2)
    stopped = MotionData(source="raw_odom", linear_velocity=0., angular_velocity=0.,
                         sample_age_s=.01, receipt_age_s=.01, session_generation=7,
                         odom_epoch="old-epoch", odom_epoch_generation=2)
    broker = lab.hub.commands

    async def telemetry(kind, payload):
        frame = decode(encode(f"telemetry.{kind}", ROBOT, 1, payload))
        await lab.hub.handle_robot_message(lab.session, *frame)

    await telemetry("base_state", base)
    await telemetry("motion", stopped)
    assert broker._validate_robot_state(lab.session, "undock") is None
    if same_origin:
        await telemetry("base_state", base.model_copy(update={"odom_epoch_valid": False}))
        await telemetry("motion", stopped)
        assert state.motion.data is None
        new_base = base
    else:
        # A bridge process restart can reuse the numeric session generation.
        new_base = base.model_copy(update={"odom_epoch": "new-epoch", "odom_epoch_generation": 1})
    await telemetry("base_state", new_base)
    assert state.motion.data is None
    assert "measured-motion" in broker._validate_robot_state(lab.session, "undock")
    if not same_origin:
        await telemetry("motion", stopped)
        assert state.motion.data is None
    await telemetry("motion", stopped.model_copy(update={
        "odom_epoch": new_base.odom_epoch, "odom_epoch_generation": new_base.odom_epoch_generation,
    }))
    assert broker._validate_robot_state(lab.session, "undock") is None


async def motion(lab, command):
    """Supply explicit fake healthy evidence, without bypassing any gate."""
    map_id, revision = "isolated-map", "sha256:" + "1" * 64
    lab.hub.maps = {map_id: SimpleNamespace(
        map_id=map_id, map_revision=revision, width=100, height=100,
        resolution=1., origin=SimpleNamespace(x=0., y=0., yaw=0.))}
    lab.session.state.set_capabilities([
        "map_context_v1", "undock", "motor_enable", "motor_disable", "software_reset",
        "web_teleop_v1",
    ])
    lab.session.state.record_base_state(fake_base(
        map_id=map_id, map_revision=revision, localization_recovery_required=False,
        localization_seed_stamp_ns=1, odom_epoch="isolated-epoch", odom_epoch_generation=1,
        dock_state="DOCKED_CONFIRMED" if command == "undock" else "CLEAR_CONFIRMED"))
    lab.session.state.record_pose(PoseData(
        map_id=map_id, map_revision=revision, x=1., y=1., yaw=0.,
        linear_velocity=0., angular_velocity=0., localized=True))
    lab.session.state.record_motion(MotionData(
        source="raw_odom", linear_velocity=0., angular_velocity=0.,
        sample_age_s=.01, receipt_age_s=.01, session_generation=7,
        odom_epoch="isolated-epoch", odom_epoch_generation=1))
    extra = {"goal": dict(map_id=map_id, map_revision=revision, x=2., y=2.)} if command == "navigate_to_pose" else {}
    command_id = await request(lab, command, **extra)
    assert command_id in lab.hub.commands.active
    return lab.hub.commands.active[command_id]


def cancellations(lab):
    return [frame for frame in lab.robot.frames if frame["type"] == "command.cancel"]


@pytest.mark.parametrize("command", ["navigate_to_pose", "undock"])
@pytest.mark.parametrize("path", ["ack_timeout", "result_timeout", "robot_timeout", "robot_disconnect", "reconnect"])
async def test_motion_uncertainty_cancels_and_retains_ownership(lab, command, path):
    entry = await motion(lab, command)
    broker = lab.hub.commands
    if path == "ack_timeout":
        broker.ack_timeout_s = 0
        await broker._ack_timeout(entry)
    elif path == "result_timeout":
        await reply(lab, entry.command_id)
        broker.result_timeout_s = 0
        await broker._result_timeout(entry)
    elif path == "robot_timeout":
        await reply(lab, entry.command_id, "command.result", outcome="timeout")
    elif path == "robot_disconnect":
        await lab.hub.robot_disconnected(lab.session, lab.robot)
    else:
        await lab.hub.robot_connected(ROBOT, Socket())
    assert broker.active[entry.command_id] is entry and entry.uncertain
    assert len(cancellations(lab)) == 1
    assert cancellations(lab)[0]["data"] == {"command_id": entry.command_id}
    assert all(timer.cancelled() or timer.cancelling() for timer in entry.timers)
    before = len(lab.robot.frames)
    for next_command in ["navigate_to_pose", "undock", "teleop_start", "motor_enable", "set_initial_pose"]:
        await request(lab, next_command, takeover=True)
    assert len(lab.robot.frames) == before
    assert "unknown outcome" in browser_frames(lab.client)[-1]["data"]["reason"]
    # Repeated uncertainty and a late acceptance do not resend, retire or resume.
    await broker._motion_uncertain(entry, "still unknown")
    await reply(lab, entry.command_id)
    assert broker.active[entry.command_id] is entry
    assert len(cancellations(lab)) == 1


@pytest.mark.parametrize("command", ["navigate_to_pose", "undock"])
@pytest.mark.parametrize("failure", ["raises", "hangs", "replacement"])
async def test_motion_send_uncertainty_is_bounded_and_never_retried(lab, monkeypatch, command, failure):
    original = lab.robot.send_text
    attempted = []

    async def send(raw):
        frame = json.loads(raw)
        attempted.append(frame)
        if frame["type"] == "command.request":
            if failure == "raises":
                raise ConnectionError("may have sent")
            if failure == "hangs":
                await asyncio.Event().wait()
            await original(raw)
            lab.session.websocket = Socket()
        else:
            await original(raw)

    monkeypatch.setattr(lab.robot, "send_text", send)
    lab.hub.commands.ack_timeout_s = .02
    entry = await asyncio.wait_for(motion(lab, command), timeout=.5)
    assert entry.uncertain
    assert sum(frame["type"] == "command.request" for frame in attempted) == 1
    assert entry.cancel_requested
    if failure != "replacement":
        assert len(cancellations(lab)) == 1
    else:
        assert not lab.session.websocket.frames  # never cancel on a replacement
    assert lab.hub.commands.active[entry.command_id] is entry


@pytest.mark.parametrize("command", ["navigate_to_pose", "undock"])
async def test_canceling_publication_task_cancels_but_retains_motion(lab, monkeypatch, command):
    sending = asyncio.Event()
    original = lab.robot.send_text

    async def send(raw):
        await original(raw)
        if json.loads(raw)["type"] == "command.request":
            sending.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(lab.robot, "send_text", send)
    pending = asyncio.create_task(motion(lab, command))
    await sending.wait()
    entry = next(iter(lab.hub.commands.active.values()))
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert lab.hub.commands.active[entry.command_id] is entry and entry.uncertain
    assert len(cancellations(lab)) == 1
    await reply(lab, entry.command_id, "command.result", outcome="canceled")
    assert entry.command_id not in lab.hub.commands.active


@pytest.mark.parametrize("command", ["navigate_to_pose", "undock", "teleop_start"])
async def test_authority_rechecked_inside_publication_task(lab, monkeypatch, command):
    oidc(lab)
    original = asyncio.wait_for
    checked = False

    async def expire_before_scheduled_send(awaitable, timeout):
        nonlocal checked
        if not checked:
            checked = True
            invalidate(lab, "expiry", monkeypatch)
        return await original(awaitable, timeout)

    monkeypatch.setattr(asyncio, "wait_for", expire_before_scheduled_send)
    if command == "teleop_start":
        await request(lab)
    else:
        with pytest.raises(AssertionError):  # helper requires a dispatched motion
            await motion(lab, command)
    assert checked
    assert not lab.robot.frames
    assert not lab.hub.commands.active
    assert ROBOT not in lab.hub.commands.teleop.owners
    assert lab.hub.commands.lease.holder(ROBOT) is None


@pytest.mark.parametrize("command", ["navigate_to_pose", "undock"])
@pytest.mark.parametrize("outcome", ["succeeded", "canceled", "failed", "rejected"])
async def test_actual_terminal_result_resolves_retained_motion(lab, command, outcome):
    entry = await motion(lab, command)
    await lab.hub.commands._motion_uncertain(entry, "late")
    await reply(lab, entry.command_id, "command.result", outcome=outcome)
    assert entry.command_id not in lab.hub.commands.active
    assert browser_frames(lab.client)[-1]["data"].get("outcome") == outcome or any(
        frame["type"] == "command.result" and frame["data"]["outcome"] == outcome
        for frame in browser_frames(lab.client))


@pytest.mark.parametrize("command", ["navigate_to_pose", "undock"])
async def test_cancel_failure_never_releases_motion_and_reset_is_explicit_recovery(lab, command):
    entry = await motion(lab, command)
    lab.robot.fail = True
    await lab.hub.commands._motion_uncertain(entry, "lost")
    assert entry.command_id in lab.hub.commands.active
    lab.robot.fail = False
    reset = await request(lab, "software_reset")
    assert entry.command_id not in lab.hub.commands.active
    assert lab.hub.commands.reset_pending[ROBOT] == reset
    count = len(lab.robot.frames)
    await request(lab, "motor_enable")
    assert len(lab.robot.frames) == count
    assert sum(frame["type"] == "command.request" and frame["data"]["command"] == command
               for frame in lab.robot.frames) == 1


@pytest.mark.parametrize("command", ["navigate_to_pose", "undock"])
async def test_motion_replies_are_bound_to_exact_robot_socket(lab, command):
    entry = await motion(lab, command)
    old_session = lab.session
    replacement = await lab.hub.robot_connected(ROBOT, Socket())
    for kind, extra in [("command.ack", {"accepted": False}),
                        ("command.progress", {"stage": "stale"}),
                        ("command.result", {"outcome": "succeeded"})]:
        env, payload = decode(encode(kind, ROBOT, 3, dict(command_id=entry.command_id, **extra)))
        count = len(browser_frames(lab.client))
        await lab.hub.commands.handle_robot_reply(old_session, env, payload)
        await lab.hub.commands.handle_robot_reply(replacement, env, payload)
        assert lab.hub.commands.active[entry.command_id] is entry
        assert len(browser_frames(lab.client)) == count


@pytest.mark.parametrize("command", ["navigate_to_pose", "undock"])
async def test_active_ids_cannot_be_replayed_after_cache_eviction(lab, command):
    entry = await motion(lab, command)
    for _ in range(600):
        lab.hub.commands._remember(str(uuid.uuid4()))
    assert entry.command_id not in lab.hub.commands._seen
    count = len(lab.robot.frames)
    await request(lab, "stop", command_id=entry.command_id)
    assert lab.hub.commands.active[entry.command_id] is entry
    assert len(lab.robot.frames) == count
    assert "Duplicate" in browser_frames(lab.client)[-1]["data"]["reason"]


async def test_stale_timer_cannot_close_replacement_entry(lab):
    old = ActiveCommand("same-id", "navigate_to_pose", ROBOT)
    replacement = ActiveCommand("same-id", "navigate_to_pose", ROBOT)
    lab.hub.commands.active["same-id"] = replacement
    lab.hub.commands.ack_timeout_s = lab.hub.commands.result_timeout_s = 0
    await lab.hub.commands._ack_timeout(old)
    await lab.hub.commands._result_timeout(old)
    await lab.hub.commands._finish(old, "failed", "stale")
    assert lab.hub.commands.active["same-id"] is replacement
    assert not replacement.uncertain


@pytest.mark.parametrize("command", ["navigate_to_pose", "undock"])
async def test_expired_operator_cannot_use_reset_to_discard_unknown_motion(lab, monkeypatch, command):
    oidc(lab)
    entry = await motion(lab, command)
    await lab.hub.commands._motion_uncertain(entry, "unknown")
    invalidate(lab, "expiry", monkeypatch)
    count = len(lab.robot.frames)
    await request(lab, "software_reset")
    assert len(lab.robot.frames) == count
    assert lab.hub.commands.active[entry.command_id] is entry
    assert ROBOT not in lab.hub.commands.reset_pending
    # Emergency actions remain usable, but their success is NOT a terminal
    # result for the original motion request.
    disable = await request(lab, "motor_disable")
    await reply(lab, disable, "command.result", outcome="succeeded")
    assert lab.hub.commands.active[entry.command_id] is entry


@pytest.mark.parametrize("command", ["navigate_to_pose", "undock"])
async def test_other_client_disconnect_never_cancels_motion(lab, command):
    entry = await motion(lab, command)
    await lab.hub.commands.cancel_for_client(object())
    assert not entry.uncertain and not cancellations(lab)


@pytest.mark.parametrize("command", ["navigate_to_pose", "undock"])
async def test_browser_disconnect_keeps_autonomous_motion_owned_and_supervised(lab, command):
    entry = await motion(lab, command)
    await lab.hub.commands.cancel_for_client(lab.client)
    assert lab.hub.commands.active[entry.command_id] is entry
    assert not entry.uncertain and not cancellations(lab)
    assert all(not timer.done() and not timer.cancelling() for timer in entry.timers)
    # Losing the browser is not a terminal robot result, nor a reason to remove
    # the timeout cancellation safeguard.
    lab.hub.commands.result_timeout_s = 0
    await lab.hub.commands._result_timeout(entry)
    assert entry.uncertain and len(cancellations(lab)) == 1


@pytest.mark.parametrize("command", ["navigate_to_pose", "undock"])
async def test_cancel_backpressure_is_bounded_and_retains_ownership(lab, command):
    entry = await motion(lab, command)
    lab.hub.settings.teleop_send_timeout_s = .02
    lab.robot.delay = True
    await asyncio.wait_for(lab.hub.commands._motion_uncertain(entry, "unknown"), timeout=.2)
    assert lab.hub.commands.active[entry.command_id] is entry and entry.uncertain
    assert entry.cancel_requested
    lab.robot.delay = False


async def test_stale_id_and_old_replies_never_resolve_explicit_recovery(lab):
    old = await motion(lab, "navigate_to_pose")
    await lab.hub.commands._motion_uncertain(old, "unknown")
    reset = await request(lab, "software_reset")
    count = len(browser_frames(lab.client))
    await reply(lab, old.command_id, "command.result", outcome="succeeded")
    assert reset in lab.hub.commands.active
    assert len(browser_frames(lab.client)) == count
    await request(lab, "software_reset", command_id=old.command_id)
    assert "Duplicate" in browser_frames(lab.client)[-1]["data"]["reason"]
    assert lab.hub.commands.reset_pending[ROBOT] == reset


async def test_duplicate_id_emergency_race_after_database_await(lab):
    entered, release = asyncio.Event(), asyncio.Event()
    count = 0

    async def recorded(command_id):
        nonlocal count
        count += 1
        if count == 2:
            entered.set()
        await release.wait()
        return False

    async def noop(*args, **kwargs):
        pass

    lab.hub.db = SimpleNamespace(command_recorded=recorded, add_command_audit=noop,
                                add_command_rejection=noop, complete_command_audit=noop,
                                add_event=noop)
    command_id = str(uuid.uuid4())
    one = asyncio.create_task(request(lab, "stop", command_id=command_id))
    two = asyncio.create_task(request(lab, "stop", command_id=command_id))
    try:
        await entered.wait()
        release.set()
        await asyncio.gather(one, two)
        assert sum(frame["type"] == "command.request" for frame in lab.robot.frames) == 1
        assert "Duplicate" in browser_frames(lab.client)[-1]["data"]["reason"] or any(
            frame["type"] == "command.ack" and "Duplicate" in frame["data"].get("reason", "")
            for frame in browser_frames(lab.client))
    finally:
        lab.hub.db = None


async def test_role_change_while_waiting_for_admission_lock(lab):
    oidc(lab)
    lock = asyncio.Lock()
    await lock.acquire()
    lab.hub.commands._admission_locks[ROBOT] = lock
    pending = asyncio.create_task(request(lab, "motor_enable"))
    await asyncio.sleep(0)
    lab.hub.settings.operator_usernames = ""
    lock.release()
    await pending
    assert not lab.robot.frames


async def test_motion_does_not_publish_to_replaced_socket_after_audit(lab, tmp_path, monkeypatch):
    db = Database(str(tmp_path / "reconnect.db"))
    await db.init()
    lab.hub.db = db
    original = db.add_command_audit

    async def replace(*args, **kwargs):
        await original(*args, **kwargs)
        await lab.hub.robot_connected(ROBOT, Socket())

    monkeypatch.setattr(db, "add_command_audit", replace)
    try:
        with pytest.raises(AssertionError):  # helper requires a dispatched motion
            await motion(lab, "navigate_to_pose")
        assert not lab.robot.frames
        assert not lab.hub.robots[ROBOT].websocket.frames
        assert not lab.hub.commands.active
    finally:
        lab.hub.db = None
        await db.close()


def test_existing_ui_socket_rejects_expired_motor_enable(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, auth_mode="oidc", session_secret="offline",
                        operator_usernames="driver", robot_token="offline-token",
                        database_path=str(tmp_path / "gateway.db"))
    token = issue("offline", dict(id=1, username="driver", display_name="Driver", role="operator"), 300)
    with TestClient(create_app(settings)) as client:
        client.cookies.set(COOKIE_NAME, token)
        with client.websocket_connect("/ws/robot?token=offline-token") as robot:
            robot.send_text(encode("robot.hello", ROBOT, 0, dict(
                protocol_version=1, capabilities=["motor_enable"], map_version=0, software_version="offline")))
            robot.receive_text()
            with client.websocket_connect("/ws/ui") as ui:
                ui.receive_text()
                monkeypatch.setattr("app.authentication.sessions.time",
                                    SimpleNamespace(time=lambda: 10**12))
                command_id = str(uuid.uuid4())
                ui.send_text(encode("command.request", ROBOT, 1,
                                   dict(command_id=command_id, command="motor_enable")))
                for _ in range(50):
                    frame = json.loads(ui.receive_text())
                    if frame["type"] == "command.ack":
                        assert frame["data"]["command_id"] == command_id
                        assert not frame["data"]["accepted"]
                        break
                else:
                    pytest.fail("expired request was not rejected")
                assert command_id not in client.app.state.hub.commands.active
