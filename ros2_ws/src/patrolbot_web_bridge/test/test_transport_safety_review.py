"""Offline R4/R5 regressions against actual bridge methods and transport loops."""
import ast
import asyncio
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
import contextlib
import json
from pathlib import Path
import threading
import time
from types import MethodType, SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from patrolbot_web_bridge.commands import CommandExecutor, DISABLED_REASON
from patrolbot_web_bridge.system_reset import SystemResetClient
from patrolbot_web_bridge.teleop import RosTeleop, TeleopSession
from patrolbot_web_bridge.ws_client import PROTECTED_TYPES, QUEUE_LIMIT, WsClient


def bridge():
    """Compile entire production methods, avoiding an unavailable ROS runtime."""
    source = Path(__file__).resolve().parents[1] / "patrolbot_web_bridge/bridge_node.py"
    tree = ast.parse(source.read_text())
    klass = next(item for item in tree.body
                 if isinstance(item, ast.ClassDef) and item.name == "WebBridgeNode")
    names = {"_drain_commands", "_enqueue_command", "_reset_reject_command", "_teleop_disconnected",
             "_transport_disconnected", "_teleop_context"}
    methods = [item for item in klass.body
               if isinstance(item, ast.FunctionDef) and item.name in names]
    namespace = {"time": time, "DISABLED_REASON": DISABLED_REASON}
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), "exec"), namespace)
    ws = WsClient("ws://unused", "offline", "robot", lambda: None)
    ws.connected.set()
    node = NS(ws=ws, _command_queue=deque(), _teleop_lock=threading.Lock(),
              _teleop_transport_invalidated=False, _latest_teleop_frame=None,
              _teleop_release_frame=None, _teleop_origin=None, _teleop_map_context=None,
              _teleop=None, _tuning=None, _system_reset=None,
              _commands=NS(handle=Mock()), _latest_amcl=None, _latest_base_state=None,
              _latest_odom=None, _localization_is_usable=lambda: False)
    for name in names:
        setattr(node, name, MethodType(namespace[name], node))
    ws.on_disconnect = node._transport_disconnected
    return node


def frames(ws):
    return [json.loads(frame) for frame in ws._queue]


@pytest.mark.parametrize("kind", ["command.ack", "command.progress", "command.result"])
def test_telemetry_flood_cannot_evict_command_lifecycle(kind):
    ws = bridge().ws
    assert ws.send(kind, {"command_id": "terminal"})
    for index in range(QUEUE_LIMIT * 8):
        ws.send("telemetry.heartbeat", {"uptime_s": index})
        assert len(ws._queue) <= QUEUE_LIMIT
    assert any(frame["type"] == kind for frame in frames(ws))
    assert not ws._failed.is_set()


def test_concurrent_telemetry_and_lifecycle_stress_remains_bounded_and_lossless():
    ws = bridge().ws
    barrier = threading.Barrier(8)

    def producer(producer_id):
        barrier.wait(timeout=5)
        for index in range(512):
            ws.send("telemetry.heartbeat", {"uptime_s": index})
            if index < 12:
                kind = ("command.ack", "command.progress", "command.result")[index % 3]
                assert ws.send(kind, {"command_id": f"{producer_id}:{index}"})
            with ws._lock:
                assert len(ws._queue) <= QUEUE_LIMIT

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(producer, range(8)))
    replies = [frame for frame in frames(ws) if frame["type"].startswith("command.")]
    assert len(replies) == 96
    assert len({frame["data"]["command_id"] for frame in replies}) == 96
    assert ws.stats["dropped"] > 0 and ws.stats["overflow_failures"] == 0


def test_only_envelope_type_is_protected_not_payload_type_lookalikes():
    ws = bridge().ws
    ws.send("telemetry.diagnostics", {"type": "command.result", "detail": "robot.hello"})
    for index in range(QUEUE_LIMIT):
        ws.send("command.result", {"command_id": str(index)})
    assert len(ws._queue) == QUEUE_LIMIT
    assert all(frame["type"] == "command.result" for frame in frames(ws))
    assert not ws._failed.is_set()


def test_protected_only_saturation_discards_telemetry_but_faults_on_reply(caplog, monkeypatch):
    from patrolbot_web_bridge import ws_client

    # Older Ubuntu pytest captures only at the root logger. Native ROS logging
    # can change propagation, so attach capture to the actual transport logger
    # and restore its handlers after the test without changing production logs.
    monkeypatch.setattr(ws_client.log, "handlers", [*ws_client.log.handlers, caplog.handler])
    ws = bridge().ws
    for index in range(QUEUE_LIMIT):
        assert ws.send("command.result", {"command_id": str(index)})
    assert not ws.send("telemetry.heartbeat", {})
    assert len(ws._queue) == QUEUE_LIMIT and ws.connected.is_set()
    generation = ws.connection_generation
    assert not ws.send("command.ack", {"command_id": "overflow"})
    assert ws._failed.is_set() and not ws.connected.is_set()
    assert ws.connection_generation > generation
    assert not ws._queue and ws.stats["overflow_failures"] == 1
    assert "delivery is unknown" in caplog.text
    assert "supervised bridge recovery" in caplog.text
    for _ in range(QUEUE_LIMIT * 2):
        assert not ws.send("command.result", {"command_id": "no-replay"})
    assert not ws._queue and ws.stats["overflow_failures"] == 1


@pytest.mark.parametrize("reset_state", ["startup", "running", "uncertain", "ambiguous"])
@pytest.mark.parametrize("command", ["stop", "motor_disable", "cancel", "cancel_map", "teleop_stop"])
def test_reset_unknown_never_block_stop_disable_or_release(reset_state, command):
    node = bridge()
    reset = SystemResetClient("/unused", node.ws)
    reset.busy = reset_state != "uncertain"
    reset._uncertain = reset_state in {"uncertain", "ambiguous"}
    node._system_reset = reset
    node._teleop = NS(handle=Mock(), session=NS(active=True, end=Mock()))
    # Even an old socket can only release, never arm or enable.
    node._enqueue_command({"command": command, "command_id": "release"})
    node.ws.connection_generation += 1
    node.ws.connected.clear()
    node._drain_commands()
    if command == "teleop_stop":
        node._teleop.handle.assert_called_once()
    else:
        node._commands.handle.assert_called_once()
    assert not node.ws._queue


@pytest.mark.parametrize("command", ["motor_enable", "navigate_to_pose", "undock",
                                    "charge_release", "teleop_start", "set_initial_pose"])
def test_reset_barrier_still_refuses_motion_and_charge_release(command):
    node = bridge()
    node._system_reset = NS(commands_blocked=True)
    node._teleop = NS(handle=Mock(), session=NS(active=False))
    node._enqueue_command({"command": command, "command_id": "blocked"})
    assert not node._command_queue  # Refuse now, not after the barrier clears.
    node._system_reset.commands_blocked = False
    node._drain_commands()
    node._commands.handle.assert_not_called()
    node._teleop.handle.assert_not_called()
    reply = frames(node.ws)[-1]
    assert reply["type"] == "command.ack" and reply["data"]["accepted"] is False
    assert "reset" in reply["data"]["reason"]


@pytest.mark.parametrize("command", ["motor_enable", "navigate_to_pose", "teleop_start"])
def test_reset_barrier_is_rechecked_when_draining_previously_admitted_input(command):
    node = bridge()
    node._system_reset = NS(commands_blocked=False)
    node._teleop = NS(handle=Mock(), session=NS(active=False))
    node._enqueue_command({"command": command, "command_id": "before-reset"})
    assert node._command_queue
    node._system_reset.commands_blocked = True
    node._drain_commands()
    node._commands.handle.assert_not_called()
    node._teleop.handle.assert_not_called()
    assert frames(node.ws)[-1]["data"]["accepted"] is False


@pytest.mark.parametrize("command", ["motor_enable", "navigate_to_pose", "teleop_start", "software_reset"])
def test_queued_intents_cannot_cross_even_an_immediately_completed_reset(command):
    node = bridge()
    node._system_reset = NS(commands_blocked=False, request=Mock())
    node._teleop = NS(handle=Mock(), session=NS(active=False))
    node._enqueue_command({"command": "software_reset", "command_id": "first"})
    node._enqueue_command({"command": command, "command_id": "old-intent"})
    node._drain_commands()
    node._system_reset.request.assert_called_once()
    node._commands.handle.assert_not_called()
    node._teleop.handle.assert_not_called()
    assert frames(node.ws)[-1]["data"]["accepted"] is False
    # A genuinely new explicit reset can recover an uncertain outcome.
    node._system_reset.commands_blocked = True
    node._enqueue_command({"command": "software_reset", "command_id": "explicit-recovery"})
    node._drain_commands()
    assert node._system_reset.request.call_count == 2


@pytest.mark.parametrize("preparing", [False, True])
def test_a_reset_disarms_existing_manual_input_without_an_explicit_release(preparing):
    node = bridge()
    published = []
    session = TeleopSession(publish=lambda *args: published.append(args),
                           send=node.ws.send, safety=lambda: None)
    node._teleop = NS(session=session)
    command_id = str(uuid4())
    context = node._teleop_context()
    assert session.start({"command_id": command_id, "operator_authorized": True},
                         context, received_at=time.monotonic(), defer_ack=preparing)
    node._system_reset = NS(commands_blocked=True)
    node._latest_teleop_frame = ({"command_id": command_id, "seq": 0, "linear": .1,
                                "angular": 0., "deadman": True},
                               time.monotonic(), node.ws.connection_generation)
    node._drain_commands()
    assert not session.active and published[-1] == (0., 0., False)
    assert not any(active for _, _, active in published)
    node._system_reset.commands_blocked = False
    node._drain_commands()
    assert not session.active
    assert not session.start({"command_id": command_id, "operator_authorized": True},
                             context, received_at=time.monotonic())


@pytest.mark.parametrize("command", ["stop", "motor_disable", "cancel", "cancel_map", "teleop_stop"])
def test_reset_exemptions_do_not_enable_a_disabled_bridge(command):
    node = bridge()
    node._commands = None
    node._system_reset = NS(commands_blocked=True)
    node._enqueue_command({"command": command, "command_id": "disabled"})
    node._drain_commands()
    assert frames(node.ws)[-1]["data"]["accepted"] is False


@pytest.mark.parametrize("command", ["motor_enable", "navigate_to_pose", "software_reset", "teleop_start"])
def test_queued_intents_cannot_start_after_reconnect_or_while_disconnected(command):
    node = bridge()
    node._system_reset = NS(commands_blocked=False, request=Mock())
    node._teleop = NS(handle=Mock(), session=NS(active=False, end=Mock()))
    node._enqueue_command({"command": command, "command_id": "stale", "_ws_generation": 999})
    assert node._command_queue[0]["_ws_generation"] == node.ws.connection_generation
    node.ws.connection_generation += 1
    node._drain_commands()
    node._enqueue_command({"command": command, "command_id": "offline"})
    node.ws.connected.clear()
    node._drain_commands()
    node._commands.handle.assert_not_called()
    node._system_reset.request.assert_not_called()
    node._teleop.handle.assert_not_called()
    assert all(frame["data"]["accepted"] is False for frame in frames(node.ws))


@pytest.mark.asyncio
@pytest.mark.parametrize("uncertain", [False, True])
async def test_server_to_actual_motor_disable_adapter_through_reset_and_stale_hardware(uncertain):
    from app.commands.broker import ActiveCommand, CommandBroker
    from app.protocol.messages import CommandRequestData

    node = bridge()
    node._system_reset = NS(commands_blocked=True)
    executor = CommandExecutor.__new__(CommandExecutor)
    executor._ws = node.ws
    executor._active = executor._undock_active = executor._map_active = None
    executor._MotorDisable = NS(Request=lambda: NS())
    executor._motor_disable_client = Mock(service_is_ready=lambda: True)
    node._commands = executor
    rid = str(uuid4())
    future = Future()
    future.set_result(NS(accepted=True, success=True, request_id=rid, code=0,
                         motors_enabled=False, message="Disabled"))
    executor._motor_disable_client.call_async.return_value = future
    received = []

    async def send_text(raw):
        data = json.loads(raw)["data"]
        received.append(data)
        node._enqueue_command(data)
        node._drain_commands()

    empty = NS(data=None, age=lambda: None)
    session = NS(robot_id="robot", websocket=NS(send_text=send_text),
                 state=NS(connection="online", base_state=empty, pose=empty,
                          capabilities=["motor_disable"]))
    hub = NS(settings=NS(command_rate_per_min=20), db=None, robots={"robot": session},
             publish=Mock(), _next_seq=lambda: 1, emit_event=AsyncMock())
    broker = CommandBroker(hub)
    broker.active["reset"] = ActiveCommand(command_id="reset", command="software_reset",
                                           robot_id="robot", uncertain=uncertain)
    broker.reset_pending["robot"] = "reset"
    operator = NS(user=NS(id=1, username="operator", can_command=True))
    try:
        await broker.handle_browser_request(operator, NS(robot_id="robot"),
            CommandRequestData(command_id=rid, command="motor_disable"))
        assert received[0]["operator_authorized"] is True
        assert executor._motor_disable_client.call_async.call_args.args[0].request_id == rid
        assert [(frame["type"], frame["data"].get("accepted", frame["data"].get("outcome")))
                for frame in frames(node.ws)] == [("command.ack", True), ("command.result", "succeeded")]
        assert broker.reset_pending["robot"] == "reset"
    finally:
        for entry in broker.active.values():
            entry.cancel_timers()


class Socket:
    """In-process socket whose blocked sender and reader expose cleanup."""
    def __init__(self, *, block_hello=False):
        self.block_hello = block_hello
        self.blocked = asyncio.Event()
        self.ready = asyncio.Event()
        self.release = asyncio.Event()
        self.reader_closed = False
        self.exited = False
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.exited = True

    async def send(self, frame):
        if self.block_hello or json.loads(frame)["type"] != "robot.hello":
            self.blocked.set()
            await self.release.wait()
        self.sent.append(json.loads(frame))

    async def recv(self):
        return json.dumps({"type": "server.hello_ack", "data": {"want_map": True}})

    def __aiter__(self):
        async def incoming():
            try:
                await asyncio.Future()
                yield ""  # Mark this as an async generator.
            finally:
                self.reader_closed = True
        return incoming()


async def stopped(task):
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2)


@pytest.mark.asyncio
async def test_overflow_closes_socket_invalidates_manual_and_tuning_and_never_reconnects():
    node = bridge()
    ws = node.ws
    socket = Socket()
    connects = Mock(return_value=socket)
    ws._connect = connects
    ws.on_map_wanted = socket.ready.set
    tuning = NS(disconnected=Mock())
    node._tuning = tuning
    published = []
    session = TeleopSession(publish=lambda *args: published.append(args), send=ws.send,
        safety=lambda: None if ws.connected.is_set() else "Transport lost.")
    node._teleop = NS(session=session)
    task = asyncio.create_task(ws._run())
    await asyncio.wait_for(socket.ready.wait(), 2)
    # Hold the sender while replies build up.
    ws.send("telemetry.heartbeat", {})
    await asyncio.wait_for(socket.blocked.wait(), 2)
    command_id = str(uuid4())
    context = node._teleop_context()
    assert session.start({"command_id": command_id, "operator_authorized": True},
                         context, received_at=time.monotonic())
    protected = sum(frame["type"] in PROTECTED_TYPES for frame in frames(ws))
    for index in range(QUEUE_LIMIT - protected):
        assert ws.send("command.result", {"command_id": str(index)})
    assert not ws.send("command.result", {"command_id": "lost-delivery-explicitly-unknown"})
    await stopped(task)
    assert socket.exited and socket.reader_closed
    node._tuning.disconnected.assert_called_once()
    node._drain_commands()
    assert not session.active and published[-1] == (0., 0., False)
    assert not session.frame({"command_id": command_id}, context, received_at=time.monotonic())
    assert not session.start({"command_id": command_id, "operator_authorized": True},
                             context, received_at=time.monotonic())
    assert ws._runner is None and ws._loop is None and not ws._queue
    await ws._run()  # Even a restarted worker cannot clear the fault.
    assert connects.call_count == 1 and ws.stats["reconnects"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["hello", "sender", "idle", "backoff"])
async def test_stop_interrupts_transport_waits_and_cleans_up_tasks(stage):
    node = bridge()
    ws = node.ws
    socket = Socket(block_hello=stage == "hello")
    ws.on_map_wanted = socket.ready.set
    connect_started = asyncio.Event()
    if stage == "backoff":
        def unavailable():
            connect_started.set()
            raise OSError("offline test")
        ws._connect = unavailable
    else:
        ws._connect = lambda: socket
    task = asyncio.create_task(ws._run())
    if stage == "backoff":
        await asyncio.wait_for(connect_started.wait(), 2)
    elif stage == "hello":
        await asyncio.wait_for(socket.blocked.wait(), 2)
    else:
        await asyncio.wait_for(socket.ready.wait(), 2)
        if stage == "sender":
            ws.send("telemetry.heartbeat", {})
            await asyncio.wait_for(socket.blocked.wait(), 2)
    ws.stop()
    await stopped(task)
    assert not ws.connected.is_set() and ws._loop is None and ws._runner is None
    if stage != "backoff":
        assert socket.exited
    if stage in {"sender", "idle"}:
        assert socket.reader_closed
        assert node._teleop_transport_invalidated
    assert not ws.send("command.result", {"command_id": "after-shutdown"})


@pytest.mark.asyncio
async def test_regular_disconnect_preserves_command_results_but_never_replays_observations():
    node = bridge()
    ws = node.ws
    socket = Socket()
    ws._connect = lambda: socket
    ws._loop = asyncio.get_running_loop()
    ws._wake = asyncio.Event()
    ws.on_map_wanted = socket.ready.set
    task = asyncio.create_task(ws._session())
    await asyncio.wait_for(socket.ready.wait(), 2)
    ws.send("command.result", {"command_id": "completed"})
    await asyncio.wait_for(socket.blocked.wait(), 2)
    socket_bound = ["tuning.response", "telemetry.motion", "telemetry.base_state",
                    "telemetry.pose", "telemetry.heartbeat", "telemetry.map", "robot.hello"]
    for kind in socket_bound:
        assert ws.send(kind, {"context": "old-socket"})
    task.cancel()
    await stopped(task)
    assert [frame["type"] for frame in frames(ws)] == ["command.result"]
    for kind in socket_bound:
        assert not ws.send(kind, {"context": "offline"})
    assert [frame["type"] for frame in frames(ws)] == ["command.result"]
    assert node._teleop_transport_invalidated


@pytest.mark.asyncio
async def test_ambiguous_send_retries_only_reply_not_queued_robot_intent_on_reconnect():
    node = bridge()
    ws = node.ws
    first = Socket()
    original_send = first.send

    async def failed_send(frame):
        await original_send(frame)
        if json.loads(frame)["type"] != "robot.hello":
            raise OSError("ambiguous offline send")

    first.send = failed_send
    ws._connect = lambda: first
    ws._loop = asyncio.get_running_loop()
    ws._wake = asyncio.Event()
    ws.on_map_wanted = first.ready.set
    task = asyncio.create_task(ws._session())
    await asyncio.wait_for(first.ready.wait(), 2)
    node._enqueue_command({"command": "motor_enable", "command_id": "old-intent"})
    ws.send("command.result", {"command_id": "completed", "outcome": "succeeded"})
    await asyncio.wait_for(first.blocked.wait(), 2)
    first.release.set()
    with pytest.raises(OSError, match="ambiguous"):
        await asyncio.wait_for(task, 2)
    assert [frame["type"] for frame in frames(ws)] == ["command.result"]
    assert first.reader_closed and first.exited

    second = Socket()
    second.release.set()
    delivered = asyncio.Event()
    original_send = second.send

    async def delivered_send(frame):
        await original_send(frame)
        if json.loads(frame)["type"] == "command.result":
            delivered.set()

    second.send = delivered_send
    ws._connect = lambda: second
    ws.on_map_wanted = second.ready.set
    task = asyncio.create_task(ws._session())
    await asyncio.wait_for(delivered.wait(), 2)
    node._drain_commands()
    node._commands.handle.assert_not_called()
    assert any(frame["data"].get("command_id") == "completed" for frame in second.sent)
    task.cancel()
    await stopped(task)


@pytest.mark.parametrize("preparing", [False, True])
def test_actual_manual_release_during_unknown_reset_needs_no_fresh_arm(preparing):
    node = bridge()
    node._system_reset = NS(commands_blocked=True)
    published = []
    teleop = RosTeleop.__new__(RosTeleop)
    teleop.ws = node.ws
    teleop.session = TeleopSession(publish=lambda *args: published.append(args),
        send=node.ws.send, safety=lambda: None)
    node._teleop = teleop
    command_id = str(uuid4())
    assert teleop.session.start({"command_id": command_id, "operator_authorized": True},
        node._teleop_context(), received_at=time.monotonic(), defer_ack=preparing)
    node._enqueue_command({"command": "teleop_stop", "command_id": str(uuid4()),
                           "operator_authorized": True})
    node.ws.connection_generation += 1
    node.ws.connected.clear()
    node._drain_commands()
    assert not teleop.session.active and published[-1] == (0., 0., False)
    assert frames(node.ws)[-1]["data"]["outcome"] == "succeeded"
