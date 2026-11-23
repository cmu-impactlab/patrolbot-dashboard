"""Real broker/adapter wire integration with fake endpoints, never a ROS graph."""
import ast
import asyncio
from collections import deque
from concurrent.futures import Future
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace as NS
from unittest.mock import Mock

from app.authentication.local import LOCAL_USER
from app.commands.broker import ActiveCommand, CommandBroker
from app.protocol.envelope import decode, encode
from app.protocol.messages import CommandRequestData
from app.settings import Settings

BRIDGE = Path(__file__).resolve().parents[2] / "ros2_ws/src/patrolbot_web_bridge"
sys.path.insert(0, str(BRIDGE))
from patrolbot_web_bridge.commands import CommandExecutor  # noqa: E402


def ready(value):
    future = Future()
    future.set_result(value)
    return future


class FakeGoal:
    accepted = True

    def __init__(self):
        self.goal_id = NS(uuid=[1] * 16)
        self.result = Future()
        self.cancel_calls = 0

    def get_result_async(self):
        return self.result

    def cancel_goal_async(self):
        self.cancel_calls += 1
        return ready(NS(return_code=0, goals_canceling=[self.goal_id]))

    def finish(self):
        self.result.set_result(NS(status=5, result=NS(error_code=0, error_msg="")))


class CommandLink:
    """In-memory wire link; production command owners on both ends."""

    def __init__(self):
        self.frames, self.tasks, self.sent, self.disabled = [], [], [], []
        empty = NS(data=None, age=lambda: None)
        self.session = NS(robot_id="review-robot", generation=1,
            state=NS(connection="online", base_state=empty, pose=empty,
                     capabilities=["motor_disable"]))
        self.session.websocket = NS(send_text=self.to_bridge)
        hub = NS(settings=Settings(), db=None, robots={"review-robot": self.session},
            _next_seq=lambda: 1, publish=lambda *args: self.frames.append(args),
            emit_event=self.ignore)
        self.broker = CommandBroker(hub, result_timeout_s=0)
        executor = self.executor = CommandExecutor.__new__(CommandExecutor)
        executor._ws = NS(send=self.to_server)
        executor._node = Mock()
        executor._active = executor._undock_active = None
        executor._nav_pending = executor._map_active = None
        executor._navigation_context = None
        executor._navigation_generation = 1
        executor._motor_disable_generation = 0
        executor._last_navigation_path = None
        executor._motor_disable_client = NS(
            service_is_ready=lambda: True, call_async=self.disable)
        executor._MotorDisable = NS(Request=lambda: NS())
        self.bridge = self.bridge_owner()

    @staticmethod
    async def ignore(*args):
        pass

    def bridge_owner(self):
        # Load the production queue/admission methods without importing ROS.
        source = BRIDGE / "patrolbot_web_bridge/bridge_node.py"
        methods = [node for node in ast.walk(ast.parse(source.read_text()))
                   if isinstance(node, ast.FunctionDef)
                   and node.name in {"_drain_commands", "_reset_reject_command"}]
        assert len(methods) == 2
        namespace = {}
        exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), "exec"),
             namespace)
        owner = NS(_teleop_lock=threading.Lock(), _teleop_transport_invalidated=False,
            _latest_teleop_frame=None, _teleop_release_frame=None, _teleop=None,
            _command_queue=deque(), _commands=self.executor,
            _system_reset=NS(commands_blocked=True),
            _latest_amcl=None, _latest_base_state=None, _latest_odom=None,
            _localization_is_usable=lambda: False, ws=self.executor._ws)
        owner._reset_reject_command = lambda *args: namespace["_reset_reject_command"](owner, *args)
        owner.drain = lambda: namespace["_drain_commands"](owner)
        return owner

    def disable(self, request):
        self.disabled.append(request.request_id)
        return ready(NS(request_id=request.request_id, accepted=True, success=True,
                        code=0, motors_enabled=False, message="disabled"))

    async def to_bridge(self, raw):
        frame = json.loads(raw)
        self.sent.append(frame)
        if frame["type"] == "command.cancel":
            self.executor.handle(
                {"command": "cancel", "command_id": frame["data"]["command_id"]},
                None, None)
        else:
            self.bridge._command_queue.append(frame["data"])
            self.bridge.drain()

    def to_server(self, kind, data):
        if kind.startswith("command."):
            envelope, payload = decode(encode(kind, "review-robot", 1, data))
            self.tasks.append(asyncio.create_task(
                self.broker.handle_robot_reply(self.session, envelope, payload)))

    async def flush(self):
        for _ in range(8):
            if not self.tasks:
                return
            tasks, self.tasks = self.tasks, []
            await asyncio.gather(*tasks)
        raise AssertionError("Replies failed to settle")

    def entry(self, command, command_id):
        entry = ActiveCommand(command_id, command, "review-robot",
                              robot_session=self.session,
                              robot_websocket=self.session.websocket)
        self.broker.active[command_id] = entry
        return entry

    def accept(self, command_id):
        goal = FakeGoal()
        self.executor._on_goal_response(command_id, ready(goal), 0, None, 1)
        return goal

    def results(self, command_id):
        return [payload for kind, _robot, payload in self.frames
                if kind == "command.result" and payload.command_id == command_id]


async def test_navigation_disconnect_retains_owner_until_actual_terminal():
    link = CommandLink()
    entry = link.entry("navigate_to_pose", "long-navigation")
    try:
        link.executor._nav_pending = {"command_id": entry.command_id, "cancel": False}
        goal = link.accept(entry.command_id)
        await link.flush()
        assert entry.acked
        await link.broker.robot_disconnected(link.session)
        await link.flush()
        assert goal.cancel_calls == 1
        assert link.sent[-1]["data"]["command_id"] == entry.command_id
        assert link.broker.active[entry.command_id].uncertain
        goal.finish()
        await link.flush()
        assert entry.command_id not in link.broker.active
        assert link.results(entry.command_id)[-1].outcome == "canceled"
    finally:
        await link.broker.shutdown()


async def test_stop_pending_acceptance_waits_for_actual_terminal():
    link = CommandLink()
    navigation = link.entry("navigate_to_pose", "pending-navigation")
    stop = link.entry("stop", "stop-pending")
    try:
        link.executor._nav_pending = {"command_id": navigation.command_id, "cancel": False}
        link.executor._stop(stop.command_id)
        await link.flush()
        assert not any(result.outcome == "succeeded" for result in link.results(stop.command_id))
        goal = link.accept(navigation.command_id)
        await link.flush()
        assert goal.cancel_calls == 1
        assert stop.command_id in link.broker.active
        goal.finish()
        await link.flush()
        assert link.results(navigation.command_id)[-1].outcome == "canceled"
        assert link.results(stop.command_id)[-1].outcome == "succeeded"
        assert navigation.command_id not in link.broker.active
        assert stop.command_id not in link.broker.active
    finally:
        await link.broker.shutdown()


async def test_motor_disable_passes_both_reset_barriers():
    link = CommandLink()
    try:
        link.broker.reset_pending["review-robot"] = "unresolved-reset"
        await link.broker.handle_browser_request(
            NS(user=LOCAL_USER, connected=True), NS(robot_id="review-robot"),
            CommandRequestData(command="motor_disable", command_id="disable-during-reset"))
        await link.flush()
        assert link.disabled == ["disable-during-reset"]
        assert link.results("disable-during-reset")[-1].outcome == "succeeded"
        assert link.broker.reset_pending["review-robot"] == "unresolved-reset"
    finally:
        await link.broker.shutdown()
