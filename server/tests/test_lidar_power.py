import math
from itertools import product

import pytest
from pydantic import ValidationError
from app.commands import gates
from app.protocol.messages import CommandRequestData

@pytest.mark.parametrize("command", ["navigate_to_pose", "set_initial_pose", "undock", "charge_release"])
@pytest.mark.parametrize("state,age", [("OFF", 0.0), ("UNKNOWN", 0.0), ("STARTING", 0.0), ("FAULT", 0.0), ("READY", 0.6), ("READY", -1)])
def test_lidar_blocks_commands_even_with_override(command, state, age):
    facts = gates.StateFacts(online=True, link_connected=True, telemetry_age=0.1,
        base_state_age=0.01, pose_age=0.01, hardware_state_valid=True,
        allow_unlocalized=True, lidar_state=state, lidar_scan_age=age)
    assert "LiDAR" in gates.rejection_reason(command, facts)

def test_pose_can_recover_localization_with_ready_lidar():
    facts = gates.StateFacts(online=True, link_connected=True, telemetry_age=0.1,
        base_state_age=0.01, pose_age=0.01, hardware_state_valid=True,
        lidar_state="READY", lidar_scan_age=0.1)
    assert gates.rejection_reason("set_initial_pose", facts) is None

def test_power_requires_explicit_boolean():
    for enabled in [None, "false", 1]:
        with pytest.raises(ValidationError):
            CommandRequestData(command_id="test", command="set_lidar_power", enabled=enabled)
    assert CommandRequestData(command_id="test", command="set_lidar_power", enabled=False).enabled is False

def test_power_can_turn_on_without_scans_but_requires_stationary_supported_robot():
    facts = gates.StateFacts(online=True, link_connected=True, telemetry_age=0.1,
        base_state_age=0.01, pose_age=0.01, hardware_state_valid=True, stationary=True,
        lidar_state="OFF", lidar_power_supported=True, capabilities=("set_lidar_power",))
    assert gates.rejection_reason("set_lidar_power", facts) is None
    facts.navigating = True
    assert gates.rejection_reason("set_lidar_power", facts)

@pytest.mark.parametrize("scan_age,receipt_age,reason", [
    (0.3, 0.3, "LiDAR scans are stale — wait for fresh scans."),
    (float("nan"), 0, "LiDAR scan age is unavailable — wait for fresh scans."),
    (0, -1, "BaseState telemetry is stale or invalid — wait for fresh hardware readings."),
    (0, None, "BaseState telemetry is unavailable — wait for fresh hardware readings."),
])
def test_lidar_counts_receipt_age_and_refuses_invalid_age(scan_age, receipt_age, reason):
    facts = gates.StateFacts(online=True, link_connected=True, hardware_state_valid=True,
        telemetry_age=.1, lidar_state="READY", lidar_scan_age=scan_age, base_state_age=receipt_age)
    assert gates.rejection_reason("set_initial_pose", facts) == reason

def test_missing_lidar_remains_unsafe_and_stop_is_available():
    assert gates.rejection_reason("set_initial_pose", gates.StateFacts())
    assert gates.rejection_reason("stop", gates.StateFacts()) is None

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import asyncio
import uuid
import time
from app.commands.broker import CommandBroker
from app.protocol.messages import CommandAckData, CommandResultData


def broker_setup():
    base = SimpleNamespace(link_connected=True, telemetry_age=.1, hardware_state_valid=True,
        charge_state="not_charging", motors_enabled=True, estop_pressed=False, fault_flags=0,
        bumpers_front=False, bumpers_rear=False, lidar_state="OFF", lidar_scan_age=-1,
        lidar_power_supported=True)
    pose = SimpleNamespace(linear_velocity=0, angular_velocity=0, localized=False)
    state = SimpleNamespace(connection="online", base_state=SimpleNamespace(data=base, age=lambda:.01),
        pose=SimpleNamespace(data=pose, age=lambda:.01), capabilities=["set_lidar_power"])
    session = SimpleNamespace(robot_id="robot", websocket=SimpleNamespace(send_text=AsyncMock()), state=state)
    hub = SimpleNamespace(settings=SimpleNamespace(command_rate_per_min=60), db=None,
        robots={"robot":session}, _next_seq=lambda:1, publish=Mock(), emit_event=AsyncMock())
    broker = CommandBroker(hub)
    client = SimpleNamespace(user=SimpleNamespace(id=1, username="operator", can_command=True))
    envelope = SimpleNamespace(robot_id="robot")
    return broker, session, client, envelope


@pytest.mark.asyncio
async def test_broker_serializes_power_and_keeps_stop_available_after_timeout():
    broker, session, client, envelope = broker_setup()
    first = CommandRequestData(command_id=str(uuid.uuid4()), command="set_lidar_power", enabled=True)
    second = CommandRequestData(command_id=str(uuid.uuid4()), command="set_lidar_power", enabled=False)
    await asyncio.gather(broker.handle_browser_request(client,envelope,first), broker.handle_browser_request(client,envelope,second))
    assert session.websocket.send_text.await_count == 1
    assert len(broker.lidar_pending) == 1
    await broker._close_out(broker.active[first.command_id], "timeout", "lost reply")
    assert broker.lidar_pending["robot"] == first.command_id
    stop = CommandRequestData(command_id=str(uuid.uuid4()), command="stop")
    await broker.handle_browser_request(client,envelope,stop)
    assert session.websocket.send_text.await_count == 2
    # A late definitive result resolves the barrier; a newer request can then be explicit.
    await broker.handle_robot_reply(session, SimpleNamespace(type="command.result"),
        CommandResultData(command_id=first.command_id, outcome="succeeded", detail="Commanded on"))
    assert not broker.lidar_pending
    await broker.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("authorized,capability,commissioned", [(False,True,True),(True,False,True),(True,True,False)])
async def test_power_obeys_existing_permissions_capability_and_commissioning(authorized,capability,commissioned):
    broker, session, client, envelope = broker_setup()
    client.user.can_command = authorized
    session.state.capabilities = ["set_lidar_power"] if capability else []
    session.state.base_state.data.lidar_power_supported = commissioned
    await broker.handle_browser_request(client,envelope, CommandRequestData(command_id=str(uuid.uuid4()), command="set_lidar_power", enabled=True))
    session.websocket.send_text.assert_not_called()
    assert not broker.lidar_pending


@pytest.mark.asyncio
async def test_power_preserves_operator_lease():
    broker, session, client, envelope = broker_setup()
    broker.lease.acquire("robot", 2, "other", object())
    await broker.handle_browser_request(client,envelope, CommandRequestData(command_id=str(uuid.uuid4()), command="set_lidar_power", enabled=True))
    session.websocket.send_text.assert_not_called()
    assert "in control" in broker.hub.publish.call_args.args[2].reason


@pytest.mark.asyncio
async def test_failed_terminal_power_requires_explicit_successful_recovery_before_spatial_admission():
    broker, session, client, envelope = broker_setup()
    power = CommandRequestData(command_id=str(uuid.uuid4()), command="set_lidar_power", enabled=True)
    await broker.handle_browser_request(client,envelope,power)
    await broker.handle_robot_reply(session, SimpleNamespace(type="command.result"),
        CommandResultData(command_id=power.command_id,outcome="failed",detail="Typed timeout; FAULT"))
    assert not broker.lidar_pending
    session.state.base_state.data.lidar_state = "READY"
    session.state.base_state.data.lidar_scan_age = .01
    assert "recovery" in broker._validate_robot_state(session,"set_initial_pose")
    session.state.base_state.received_mono = time.monotonic()
    recovery = CommandRequestData(command_id=str(uuid.uuid4()),command="set_lidar_power",enabled=True)
    await broker.handle_browser_request(client,envelope,recovery)
    assert session.websocket.send_text.await_count == 2
    await broker.handle_robot_reply(session, SimpleNamespace(type="command.result"),
        CommandResultData(command_id=recovery.command_id,outcome="succeeded",detail="Commanded on"))
    session.state.base_state.received_mono = time.monotonic()
    assert broker._validate_robot_state(session,"set_initial_pose") is None
    await broker.shutdown()


@pytest.mark.asyncio
async def test_power_audit_records_explicit_target():
    broker, session, client, envelope = broker_setup()
    broker.hub.db = SimpleNamespace(command_recorded=AsyncMock(return_value=False), add_command_audit=AsyncMock(), complete_command_audit=AsyncMock())
    await broker.handle_browser_request(client,envelope, CommandRequestData(command_id=str(uuid.uuid4()),command="set_lidar_power",enabled=False))
    assert broker.hub.db.add_command_audit.call_args.args[3] == {"enabled":False}
    await broker.shutdown()


@pytest.mark.parametrize("changes", [dict(link_connected=False),dict(hardware_state_valid=False),dict(telemetry_age=.5),dict(telemetry_age=float("nan"))])
def test_lidar_admission_and_power_require_fresh_valid_hardware(changes):
    values = dict(online=True,link_connected=True,hardware_state_valid=True,telemetry_age=.1,base_state_age=.1,
        pose_age=.1,stationary=True,lidar_state="READY",lidar_scan_age=.1,lidar_power_supported=True,
        capabilities=("set_lidar_power",))
    values.update(changes)
    facts = gates.StateFacts(**values)
    assert gates.rejection_reason("set_initial_pose", facts)
    assert gates.rejection_reason("set_lidar_power", facts)


@pytest.mark.asyncio
async def test_stop_does_not_wait_for_a_blocked_power_send():
    import json
    broker, session, client, envelope = broker_setup()
    entered = asyncio.Event()
    release = asyncio.Event()
    async def send(frame):
        if json.loads(frame)["data"]["command"] == "set_lidar_power":
            entered.set()
            await release.wait()
    session.websocket.send_text.side_effect = send
    power = asyncio.create_task(broker.handle_browser_request(client,envelope,CommandRequestData(command_id=str(uuid.uuid4()),command="set_lidar_power",enabled=True)))
    await entered.wait()
    try:
        await asyncio.wait_for(broker.handle_browser_request(client,envelope,CommandRequestData(command_id=str(uuid.uuid4()),command="stop")), .05)
        assert session.websocket.send_text.await_count == 2
    finally:
        release.set()
        await power
        await broker.shutdown()


@pytest.mark.asyncio
async def test_terminal_power_cannot_reuse_a_pre_request_ready_sample():
    import time
    broker, session, client, envelope = broker_setup()
    session.state.base_state.received_mono = time.monotonic()
    power = CommandRequestData(command_id=str(uuid.uuid4()),command="set_lidar_power",enabled=True)
    await broker.handle_browser_request(client,envelope,power)
    await broker.handle_robot_reply(session, SimpleNamespace(type="command.result"),CommandResultData(command_id=power.command_id,outcome="succeeded"))
    session.state.base_state.data.lidar_state = "READY"
    session.state.base_state.data.lidar_scan_age = .01
    assert broker._validate_robot_state(session,"set_initial_pose")
    session.state.base_state.received_mono = time.monotonic()
    session.state.base_state.received_mono = time.monotonic()
    assert broker._validate_robot_state(session,"set_initial_pose") is None
    await broker.shutdown()


@pytest.mark.parametrize("state", ["OFF", "READY", "FAULT"])
def test_power_recovery_does_not_require_map_pose(state):
    facts = gates.StateFacts(online=True, link_connected=True, telemetry_age=.1,
        base_state_age=.01, hardware_state_valid=True,
        lidar_state=state, lidar_power_supported=True, capabilities=("set_lidar_power",))
    assert gates.rejection_reason("set_lidar_power", facts) is None


def ready_facts(**changes):
    values = dict(online=True, link_connected=True, hardware_state_valid=True,
                  telemetry_age=.1, base_state_age=0, lidar_state="READY", lidar_scan_age=.1)
    return gates.StateFacts(**(values | changes))


@pytest.mark.parametrize("changes,reason", [
    (dict(online=False), "The robot is not connected right now."),
    (dict(base_state_age=None), "BaseState telemetry is unavailable — wait for fresh hardware readings."),
    (dict(link_connected=False), "The drive base is disconnected — LiDAR readiness is unavailable."),
    (dict(hardware_state_valid=False), "BaseState hardware readings are invalid — LiDAR readiness is unavailable."),
    (dict(telemetry_age=.501), "BaseState telemetry is stale or invalid — wait for fresh hardware readings."),
    (dict(lidar_state="OFF", lidar_scan_age=-1), "LiDAR is off — turn it on before waiting for fresh scans."),
    (dict(lidar_state="STARTING", lidar_scan_age=-1), "LiDAR is warming up — wait for fresh scans."),
    (dict(lidar_state="UNKNOWN"), "LiDAR state is unavailable — wait for a ready state."),
    (dict(lidar_state="FAULT"), "LiDAR is reporting a fault — wait for a ready state."),
    (dict(lidar_scan_age=-1), "LiDAR scan age is unavailable — wait for fresh scans."),
    (dict(lidar_scan_age=float("nan")), "LiDAR scan age is unavailable — wait for fresh scans."),
    (dict(lidar_scan_age=float("inf")), "LiDAR scan age is unavailable — wait for fresh scans."),
    (dict(lidar_scan_age=.501), "LiDAR scans are stale — wait for fresh scans."),
    (dict(lidar_scan_age=.4, base_state_age=.101), "LiDAR scans are stale — wait for fresh scans."),
])
@pytest.mark.parametrize("command", ["set_initial_pose", "navigate_to_pose", "undock", "charge_release"])
def test_explicit_lidar_reasons_keep_priority_over_other_command_gates(changes, reason, command):
    # Other command prerequisites deliberately remain unmet; LiDAR still goes first.
    facts = ready_facts(allow_unlocalized=True, **changes)
    assert gates.rejection_reason(command, facts) == reason
    assert gates.rejection_reason("stop", facts) is None


def test_stale_base_precedes_scanner_off_and_scan_age():
    assert gates.lidar_reason(ready_facts(telemetry_age=.6, lidar_state="OFF", lidar_scan_age=-1)) == (
        "BaseState telemetry is stale or invalid — wait for fresh hardware readings.")


@pytest.mark.parametrize("scan_age,telemetry_age,receipt", [(.5, 0, 0), (.4, .4, .1), (0, .5, 0)])
def test_inclusive_half_second_boundary(scan_age, telemetry_age, receipt):
    assert gates.lidar_reason(ready_facts(lidar_scan_age=scan_age, telemetry_age=telemetry_age,
                                   base_state_age=receipt)) is None


def test_original_admission_predicate_is_unchanged():
    ages = [-1, 0, .1, .5, .501, float("nan"), float("inf"), -float("inf")]
    for state, age, telemetry, receipt in product(
            ["READY", "OFF", "STARTING", "UNKNOWN", "FAULT"], ages, ages, [None, *ages]):
        facts = ready_facts(lidar_state=state, lidar_scan_age=age,
                            telemetry_age=telemetry, base_state_age=receipt)
        refused = (not facts.online or not facts.link_connected or not facts.hardware_state_valid
                   or not math.isfinite(telemetry) or telemetry < 0
                   or receipt is None or telemetry + receipt > .5
                   or state != "READY"
                   or not math.isfinite(age) or not math.isfinite(receipt)
                   or age < 0 or receipt < 0 or age + receipt > .5)
        assert (gates.lidar_reason(facts) is not None) == refused
