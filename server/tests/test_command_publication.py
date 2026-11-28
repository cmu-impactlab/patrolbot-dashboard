"""Bounded publication with in-process fake sockets; no network or hardware."""
import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest

from app.commands.broker import ActiveCommand
from app.database.repo import Database
from app.protocol.envelope import decode, encode
from app.protocol.messages import MotionData, PoseData
from test_web_teleop import ROBOT, Socket, browser_frames, fake_base, lab, reply, request


COMMANDS = [
    "stop", "motor_disable", "set_initial_pose", "software_reset",
    "set_lidar_power", "charge_release", "motor_enable", "dock_reset",
]


@pytest.fixture
def publication(lab):
    map_id, revision = "isolated-map", "sha256:" + "1" * 64
    lab.hub.maps = {map_id: SimpleNamespace(
        map_id=map_id, map_revision=revision, width=100, height=100,
        resolution=1., origin=SimpleNamespace(x=0., y=0., yaw=0.))}
    lab.session.state.set_capabilities(COMMANDS + ["map_context_v1"])
    lab.session.state.record_base_state(fake_base(
        map_id=map_id, map_revision=revision, charge_state="charging",
        dock_state="DOCKED_CONFIRMED",
        lidar_power_supported=True, odom_epoch="isolated-epoch", odom_epoch_generation=1))
    lab.session.state.record_pose(PoseData(
        map_id=map_id, map_revision=revision, x=1., y=1., yaw=0.,
        linear_velocity=0., angular_velocity=0., localized=True))
    lab.session.state.record_motion(MotionData(
        source="raw_odom", linear_velocity=0., angular_velocity=0.,
        sample_age_s=.01, receipt_age_s=.01, session_generation=7,
        odom_epoch="isolated-epoch", odom_epoch_generation=1))
    lab.hub.settings.teleop_send_timeout_s = .01

    async def send(command, **kwargs):
        extra = ({"enabled": False} if command == "set_lidar_power" else
                 {"goal": dict(map_id=map_id, map_revision=revision, x=2., y=2.)}
                 if command == "set_initial_pose" else {})
        return await request(lab, command, **extra, **kwargs)

    return send


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize("failure", ["hangs", "raises", "replacement"])
async def test_all_other_publications_are_bounded_and_uncertain(lab, publication, monkeypatch, command, failure):
    original = lab.robot.send_text
    attempted = []

    async def send(raw):
        frame = json.loads(raw)
        attempted.append(frame)
        if frame["type"] == "command.request":
            if failure == "hangs":
                await asyncio.Event().wait()
            if failure == "raises":
                raise ConnectionError("may have sent")
            lab.session.websocket = Socket()
        await original(raw)

    monkeypatch.setattr(lab.robot, "send_text", send)
    command_id = await asyncio.wait_for(publication(command), timeout=.5)
    entry = lab.hub.commands.active[command_id]
    assert entry.uncertain
    if command in {"software_reset", "set_lidar_power"}:
        assert "publication was not confirmed" in entry.uncertainty_detail
        assert "connection dropped" not in entry.uncertainty_detail
    assert sum(frame["type"] == "command.request" for frame in attempted) == 1
    assert not any(frame["type"] == "command.ack" for frame in browser_frames(lab.client))
    assert browser_frames(lab.client)[-1]["data"]["outcome"] == "timeout"
    if failure == "replacement":
        assert not lab.session.websocket.frames
    else:
        await reply(lab, command_id, "command.result", outcome="succeeded")
        assert command_id not in lab.hub.commands.active


@pytest.mark.parametrize("first", COMMANDS)
async def test_sequential_handler_reaches_independent_disable_after_stalled_send(lab, publication, monkeypatch, first):
    original = lab.robot.send_text
    attempted = []

    async def send(raw):
        frame = json.loads(raw)
        if frame["type"] == "command.request":
            attempted.append(frame["data"]["command"])
            if len(attempted) == 1:
                await asyncio.Event().wait()
        await original(raw)

    monkeypatch.setattr(lab.robot, "send_text", send)

    async def sequential_handler():
        await publication(first)
        return await publication("motor_disable" if first != "motor_disable" else "stop")

    command_id = await asyncio.wait_for(sequential_handler(), timeout=.5)
    assert attempted == [first, "motor_disable" if first != "motor_disable" else "stop"]
    assert lab.robot.frames[-1]["data"]["command_id"] == command_id
    assert not lab.hub.commands.active[command_id].uncertain


@pytest.mark.parametrize("command", COMMANDS)
async def test_explicit_publication_cancellation_propagates_and_retains_unknown_outcome(lab, publication, monkeypatch, command):
    sending = asyncio.Event()
    original = lab.robot.send_text

    async def send(raw):
        await original(raw)
        if json.loads(raw)["type"] == "command.request":
            sending.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(lab.robot, "send_text", send)
    pending = asyncio.create_task(publication(command))
    await asyncio.wait_for(sending.wait(), timeout=.5)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    entry, = lab.hub.commands.active.values()
    assert entry.uncertain
    assert sum(frame["type"] == "command.request" for frame in lab.robot.frames) == 1


@pytest.mark.parametrize("command", COMMANDS)
async def test_cancel_before_send_is_known_refusal_without_robot_cancel(lab, publication, monkeypatch, command):
    async def cancel_before_send(awaitable, timeout):
        awaitable.close()
        raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "wait_for", cancel_before_send)
    with pytest.raises(asyncio.CancelledError):
        await publication(command)
    assert not lab.robot.frames
    assert not lab.hub.commands.active
    assert ROBOT not in lab.hub.commands.reset_pending
    assert ROBOT not in lab.hub.commands.lidar_pending
    assert any(frame["type"] == "command.ack" and frame["data"]["accepted"] is False
               for frame in browser_frames(lab.client))


@pytest.mark.parametrize("command", [command for command in COMMANDS if command not in {"stop", "motor_disable"}])
async def test_authority_rechecked_in_scheduled_send(lab, publication, monkeypatch, command):
    original = asyncio.wait_for

    async def demote_before_send(awaitable, timeout):
        lab.client.user.can_command = False
        return await original(awaitable, timeout)

    monkeypatch.setattr(asyncio, "wait_for", demote_before_send)
    await publication(command)
    assert not lab.robot.frames
    assert not lab.hub.commands.active
    assert any(frame["type"] == "command.ack" and frame["data"]["accepted"] is False
               for frame in browser_frames(lab.client))


@pytest.mark.parametrize("command,change,kind,data", [
    (command, change, kind, data)
    for command in COMMANDS for change in ["socket", "session"]
    for kind, data in [("command.ack", {"accepted": True}),
                       ("command.progress", {"stage": "working"}),
                       ("command.result", {"outcome": "succeeded"})]
    if command != "software_reset" or kind != "command.result"
])
async def test_replacement_connection_cannot_ack_progress_or_complete(lab, publication, command, change, kind, data):
    command_id = await publication(command)
    old_session = lab.session
    if change == "socket":
        lab.session.websocket = Socket()
    else:
        lab.session = SimpleNamespace(robot_id=ROBOT, websocket=Socket())
        lab.hub.robots[ROBOT] = lab.session
    before = list(browser_frames(lab.client))
    env, payload = decode(encode(kind, ROBOT, 1, dict(command_id=command_id, **data)))
    for session in [old_session, lab.session]:
        await lab.hub.commands.handle_robot_reply(session, env, payload)
    assert browser_frames(lab.client) == before
    assert not lab.hub.commands.active[command_id].acked


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize("dispatched", [False, True])
async def test_admission_refusal_and_send_uncertainty_have_durable_distinct_outcomes(lab, publication, tmp_path, monkeypatch, command, dispatched):
    db = Database(str(tmp_path / "publication.db"))
    await db.init()
    lab.hub.db = db
    command_id = str(uuid.uuid4())
    if dispatched:
        lab.robot.fail = True
    else:
        original = asyncio.wait_for

        async def replace_before_scheduled_send(awaitable, timeout):
            lab.session.websocket = Socket()
            return await original(awaitable, timeout)

        monkeypatch.setattr(asyncio, "wait_for", replace_before_scheduled_send)
    try:
        await publication(command, command_id=command_id)
        row, = await db.get_command_audit()
        assert row["command_id"] == command_id
        assert row["outcome"] == ("timeout" if dispatched else "rejected")
        assert bool(command_id in lab.hub.commands.active) == dispatched
        if dispatched:
            lab.robot.fail = False
            await reply(lab, command_id, "command.result", outcome="succeeded")
            row, = await db.get_command_audit()
            assert row["outcome"] == "succeeded"
        else:
            assert not lab.robot.frames and not lab.session.websocket.frames
            acks = [frame for frame in browser_frames(lab.client) if frame["type"] == "command.ack"]
            assert acks[-1]["data"]["accepted"] is False
            assert ROBOT not in lab.hub.commands.reset_pending
            assert ROBOT not in lab.hub.commands.lidar_pending
    finally:
        lab.hub.db = None
        await db.close()


@pytest.mark.parametrize("previous", ["navigate_to_pose", "set_lidar_power", "software_reset"])
@pytest.mark.parametrize("boundary", ["retire_socket", "retire_authority", "scheduled_socket", "scheduled_authority", "cancel_before"])
async def test_refused_recovery_holds_retired_intents_until_explicit_reset(lab, publication, monkeypatch, previous, boundary):
    broker = lab.hub.commands
    old = ActiveCommand(str(uuid.uuid4()), previous, ROBOT, uncertain=True,
                        robot_session=lab.session, robot_websocket=lab.robot,
                        publication_started=True)
    broker.active[old.command_id] = old
    if previous == "software_reset":
        broker.reset_pending[ROBOT] = old.command_id
    if previous == "set_lidar_power":
        broker.lidar_pending[ROBOT] = old.command_id

    def change():
        if boundary.endswith("socket"):
            lab.session.websocket = Socket()
        else:
            lab.client.user.can_command = False

    original_finish, original_wait = broker._finish, asyncio.wait_for

    async def finish(*args):
        await original_finish(*args)
        change()

    async def wait(awaitable, timeout):
        if boundary == "cancel_before":
            awaitable.close()
            raise asyncio.CancelledError
        change()
        return await original_wait(awaitable, timeout)

    with monkeypatch.context() as patch:
        if boundary.startswith("retire"):
            patch.setattr(broker, "_finish", finish)
        else:
            patch.setattr(asyncio, "wait_for", wait)
        if boundary == "cancel_before":
            with pytest.raises(asyncio.CancelledError):
                await publication("software_reset")
        else:
            await publication("software_reset")
    held, = broker.active.values()
    assert broker.reset_pending[ROBOT] == held.command_id
    assert held.uncertain and not held.publication_started
    assert not lab.robot.frames
    lab.client.user.can_command = True
    lab.session.websocket = lab.robot
    await publication("motor_enable")
    assert not lab.robot.frames
    for kind, data in [("command.ack", {"accepted": True}), ("command.result", {"outcome": "succeeded"})]:
        await reply(lab, held.command_id, kind, **data)
    assert not held.acked and broker.active[held.command_id] is held
    retry = await publication("software_reset")
    assert held.command_id not in broker.active
    assert broker.reset_pending[ROBOT] == retry
    await reply(lab, retry, "command.result", outcome="succeeded")
    assert not broker.reset_pending and not broker.active


@pytest.mark.parametrize("outcome", ["succeeded", "failed", "timeout"])
async def test_dispatched_reset_reconciles_supervisor_result_on_current_connection_only(lab, publication, outcome):
    broker = lab.hub.commands
    command_id = await publication("software_reset")
    old_session = lab.session
    lab.session = await lab.hub.robot_connected(ROBOT, Socket())
    env, payload = decode(encode("command.result", ROBOT, 1,
        dict(command_id=command_id, outcome=outcome)))
    before = list(browser_frames(lab.client))
    await broker.handle_robot_reply(old_session, env, payload)
    assert browser_frames(lab.client) == before
    # A replayed ACK on a new connection is not fresh acceptance.
    await reply(lab, command_id)
    assert not broker.active[command_id].acked
    await broker.handle_robot_reply(lab.session, env, payload)
    assert (command_id in broker.active) == (outcome == "timeout")
    assert (ROBOT in broker.reset_pending) == (outcome == "timeout")


@pytest.mark.parametrize("command", ["stop", "motor_disable"])
@pytest.mark.parametrize("late", ["before_cleanup", "after_cleanup", "none"])
async def test_generic_uncertainty_survives_ack_timer_and_expires_without_duplicate_result(lab, publication, tmp_path, command, late):
    db = Database(str(tmp_path / "cleanup.db"))
    await db.init()
    lab.hub.db = db
    broker = lab.hub.commands
    lab.robot.fail = True
    try:
        command_id = await publication(command)
        entry = broker.active[command_id]
        first, = [frame["data"] for frame in browser_frames(lab.client) if frame["type"] == "command.result"]
        broker.ack_timeout_s = 0
        await broker._ack_timeout(entry)
        assert broker.active[command_id] is entry and not entry.acked
        row, = await db.get_command_audit()
        assert row["detail"] == first["detail"] and row["outcome"] == "timeout"
        lab.robot.fail = False
        if late == "before_cleanup":
            await reply(lab, command_id)
            assert entry.acked and entry.uncertain
            await reply(lab, command_id, "command.result", outcome="succeeded", detail="Confirmed")
        broker.result_timeout_s = 0
        await broker._result_timeout(entry)
        assert command_id not in broker.active
        before = list(browser_frames(lab.client))
        if late == "after_cleanup":
            await reply(lab, command_id)
            await reply(lab, command_id, "command.result", outcome="succeeded")
            assert not entry.acked and browser_frames(lab.client) == before
        results = [frame["data"] for frame in browser_frames(lab.client) if frame["type"] == "command.result"]
        assert results == ([first, dict(command_id=command_id, outcome="succeeded", detail="Confirmed")]
                           if late == "before_cleanup" else [first])
        row, = await db.get_command_audit()
        assert row["outcome"] == ("succeeded" if late == "before_cleanup" else "timeout")
        assert row["detail"] == ("Confirmed" if late == "before_cleanup" else first["detail"])
    finally:
        lab.hub.db = None
        await db.close()


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize("early", ["accepted", "rejected", "succeeded"])
async def test_early_reply_is_not_overwritten_when_send_later_times_out(lab, publication, monkeypatch, command, early):
    async def send(raw):
        frame = json.loads(raw)
        if frame["type"] != "command.request":
            return
        command_id = frame["data"]["command_id"]
        if early == "succeeded":
            await reply(lab, command_id, "command.result", outcome="succeeded")
        else:
            await reply(lab, command_id, accepted=early == "accepted")
        await asyncio.Event().wait()

    monkeypatch.setattr(lab.robot, "send_text", send)
    command_id = await publication(command)
    frames = [frame for frame in browser_frames(lab.client) if frame["type"].startswith("command.")]
    if early == "accepted":
        entry = lab.hub.commands.active[command_id]
        assert entry.acked and entry.uncertain
        assert [frame["type"] for frame in frames] == ["command.ack", "command.result"]
        assert frames[-1]["data"]["outcome"] == "timeout"
    else:
        assert command_id not in lab.hub.commands.active
        assert len(frames) == 1
        assert frames[0]["data"].get("outcome", "rejected") == early


@pytest.mark.parametrize("command", ["stop", "motor_disable", "set_initial_pose"])
async def test_running_timers_preserve_publication_uncertainty_and_clean_up(lab, publication, command):
    broker = lab.hub.commands
    broker.ack_timeout_s = .05
    broker.result_timeout_s = .1
    lab.robot.delay = True
    command_id = await publication(command)
    entry = broker.active[command_id]
    first = [frame for frame in browser_frames(lab.client) if frame["type"] == "command.result"]
    await entry.timers[0]
    assert broker.active[command_id] is entry and entry.uncertain and not entry.acked
    await entry.timers[1]
    assert command_id not in broker.active
    assert [frame for frame in browser_frames(lab.client) if frame["type"] == "command.result"] == first
