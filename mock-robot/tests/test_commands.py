import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from mock_robot.client import MockRobot
from mock_robot.map_context import MAP_REVISIONS


class FakeWs:
    """Collects outbound frames and replays scripted inbound ones."""

    def __init__(self, incoming: list[dict] | None = None) -> None:
        self.sent: list[dict] = []
        self._incoming = [json.dumps(frame) for frame in (incoming or [])]

    async def send(self, frame: str) -> None:
        self.sent.append(json.loads(frame))

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if self._incoming:
            return self._incoming.pop(0)
        raise StopAsyncIteration

    def of_type(self, type_: str) -> list[dict]:
        return [frame for frame in self.sent if frame["type"] == type_]


def request(command: str, command_id: str = "cmd-1", goal: dict | None = None) -> dict:
    data = {"command_id": command_id, "command": command, "operator_authorized": True}
    if goal is not None:
        data["goal"] = {"map_id": "cmuq-floor2", "map_revision": MAP_REVISIONS["cmuq-floor2"], **goal}
    return {"version": 1, "type": "command.request", "robot_id": "patrolbot-01",
            "sequence": 1, "timestamp": "2026-07-17T00:00:00Z", "data": data}


def make_robot() -> MockRobot:
    return MockRobot("ws://unused", "t", "patrolbot-01", "calm")


def test_navigate_command_acks_and_completes():
    robot = make_robot()
    ws = FakeWs([request("navigate_to_pose", goal={"x": robot.robot.x + 0.5, "y": robot.robot.y})])

    async def run():
        await robot._receive_loop(ws)
        assert robot.mode == "commanded"
        acks = ws.of_type("command.ack")
        assert acks and acks[0]["data"]["accepted"] is True

        # Drive the sim until it arrives, then let the slow-loop hook report.
        for _ in range(600):
            robot.step(0.1)
            if robot._command_arrived:
                break
        await robot._command_progress(ws)
        results = ws.of_type("command.result")
        assert results and results[0]["data"]["outcome"] == "succeeded"
        assert robot.mode == "idle"

    asyncio.run(run())


def test_progress_reports_distance():
    robot = make_robot()
    ws = FakeWs([request("navigate_to_pose", goal={"x": robot.robot.x + 3.0, "y": robot.robot.y})])

    async def run():
        await robot._receive_loop(ws)
        await robot._command_progress(ws)
        progress = ws.of_type("command.progress")
        assert progress and progress[0]["data"]["distance_remaining"] > 2.0

    asyncio.run(run())


def test_stop_cancels_active_navigation():
    robot = make_robot()
    ws = FakeWs([
        request("navigate_to_pose", command_id="nav-1", goal={"x": 1.0, "y": 1.0}),
        request("stop", command_id="stop-1"),
    ])

    async def run():
        await robot._receive_loop(ws)
        results = {frame["data"]["command_id"]: frame["data"]["outcome"]
                   for frame in ws.of_type("command.result")}
        assert results == {"nav-1": "canceled", "stop-1": "succeeded"}
        assert robot.mode == "idle"
        assert robot.robot.goal is None

    asyncio.run(run())


@pytest.mark.parametrize("command", ["navigate_to_pose", "undock"])
def test_cancel_only_stops_matching_operation(command):
    robot = make_robot()
    if command == "undock":
        robot._settle_on_dock()
    start = FakeWs([request(command, "active", {"x": 3.0, "y": 3.0})])

    def cancel(command_id):
        return {"type": "command.cancel", "data": {"command_id": command_id}}

    async def run():
        await robot._receive_loop(start)
        robot.step(0.1)
        active, mode, goal = robot.command.copy(), robot.mode, robot.robot.goal
        unrelated = FakeWs([cancel("other"), cancel(None)])
        await robot._receive_loop(unrelated)
        assert (robot.command, robot.mode, robot.robot.goal) == (active, mode, goal)
        assert unrelated.sent == []

        # Cancellation must bypass readiness checks and clear pending arrival.
        robot.lidar_state = "OFF"
        robot._command_arrived = True
        canceled = FakeWs([cancel("active"), cancel("active")])
        await robot._receive_loop(canceled)
        assert robot.command is None and robot.mode == "idle"
        assert robot.robot.goal is None
        assert robot.robot.speed == robot.robot.yaw_rate == 0.0
        assert not robot._command_arrived
        position = (robot.robot.x, robot.robot.y)
        robot.step(0.1)
        await robot._command_progress(canceled)
        assert (robot.robot.x, robot.robot.y) == position
        assert [f["data"]["outcome"] for f in canceled.sent] == ["canceled"]
        assert canceled.sent[0]["type"] == "command.result"
        assert canceled.sent[0]["data"]["command_id"] == "active"

    asyncio.run(run())


def test_cancel_is_not_replayed_on_reconnect(monkeypatch):
    robot = make_robot()

    class Session(FakeWs):
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def recv(self):
            return json.dumps({"type": "server.hello_ack"})

    first = Session([request("navigate_to_pose", "old", {"x": 3.0, "y": 3.0}),
                     {"type": "command.cancel", "data": {"command_id": "old"}}])
    # A fresh session has no cancellation intent, even for the same correlation ID.
    second = Session([request("navigate_to_pose", "old", {"x": 3.0, "y": 3.0})])
    sessions = iter([first, second])
    monkeypatch.setattr("mock_robot.client.websockets.connect", lambda *a, **k: next(sessions))
    for loop in ("_pose_loop", "_lidar_loop", "_path_loop", "_slow_loop", "_base_state_loop",
                 "_motion_loop", "_disconnect_watch"):
        monkeypatch.setattr(robot, loop, AsyncMock())

    async def run():
        await robot.run_session()
        assert robot.command is None
        await robot.run_session()
        await robot._command_progress(second)

    asyncio.run(run())
    assert robot.command == {"command_id": "old", "kind": "navigate"}
    assert robot.mode == "commanded"
    assert second.of_type("command.result") == []
    assert second.of_type("command.progress")


def test_cancel_during_arrival_publication_keeps_single_terminal_result():
    robot = make_robot()
    cancel = {"type": "command.cancel", "data": {"command_id": "arrived"}}

    async def run():
        await robot._receive_loop(FakeWs([
            request("navigate_to_pose", "arrived", {"x": 3.0, "y": 3.0}),
        ]))
        robot._command_arrived = True
        publishing = asyncio.Event()
        release = asyncio.Event()

        class PausedResultSocket(FakeWs):
            async def send(self, frame):
                publishing.set()
                await release.wait()
                await super().send(frame)

        success = PausedResultSocket()
        arrival = asyncio.create_task(robot._command_progress(success))
        await asyncio.wait_for(publishing.wait(), timeout=1)
        cancellation = FakeWs([cancel])
        await robot._receive_loop(cancellation)
        release.set()
        await arrival
        assert cancellation.sent == []
        assert [frame["data"]["outcome"] for frame in success.sent] == ["succeeded"]

    asyncio.run(run())


def test_set_initial_pose_teleports():
    robot = make_robot()
    ws = FakeWs([request("set_initial_pose", goal={"x": 2.5, "y": 3.5, "yaw": 1.0})])

    async def run():
        await robot._receive_loop(ws)
        assert (robot.robot.x, robot.robot.y, robot.robot.yaw) == (2.5, 3.5, 1.0)
        results = ws.of_type("command.result")
        assert results and results[0]["data"]["outcome"] == "succeeded"

    asyncio.run(run())


def test_unknown_command_rejected():
    robot = make_robot()
    ws = FakeWs([request("self_destruct")])

    async def run():
        await robot._receive_loop(ws)
        acks = ws.of_type("command.ack")
        assert acks and acks[0]["data"]["accepted"] is False

    asyncio.run(run())


def test_recovery_refuses_override_until_operator_pose_is_accepted():
    robot = make_robot()
    robot.localization_recovery_required = True
    old_seed = robot.localization_seed_stamp_ns
    goal = {"x": robot.robot.x, "y": robot.robot.y, "yaw": 0}
    blocked = request("navigate_to_pose", "blocked", goal)
    blocked["data"]["allow_unlocalized"] = True
    ws = FakeWs([blocked, request("set_initial_pose", "seed", goal),
                 request("navigate_to_pose", "recovered", goal)])
    asyncio.run(robot._receive_loop(ws))
    assert [f["data"]["accepted"] for f in ws.of_type("command.ack")] == [False, True, True]
    assert robot.localization_seed_stamp_ns >= old_seed
    assert not robot.localization_recovery_required


def test_legacy_base_payload_is_fail_closed_until_simulator_supplies_readiness():
    from mock_robot.diagnostics import base_state_payload
    payload = base_state_payload(session_generation=1, charging=False, docked=False,
                                 estop=False, bumper_front=False, bumper_rear=False)
    assert not payload["odom_epoch_valid"]
    assert payload["localization_recovery_required"]
    assert payload["localization_seed_stamp_ns"] == 0


def test_measured_profile_and_omitted_coarse_preserve_world_pose():
    for profile in ('measured',None):
        robot=make_robot()
        frame=request('set_initial_pose',goal={'x':-3.25,'y':8.5,'yaw':1.5707963267948966})
        if profile is not None: frame['data']['seed_profile']=profile
        ws=FakeWs([frame])
        asyncio.run(robot._receive_loop(ws))
        assert ws.of_type('command.ack')[0]['data']['accepted']
        assert robot.seed_profile==profile
        assert (robot.robot.x,robot.robot.y,robot.robot.yaw)==(-3.25,8.5,1.5707963267948966)


def test_disable_preempts_simulated_motion_even_when_lidar_off():
    robot=make_robot();robot.lidar_state='OFF';robot.motors_enabled=True
    robot.command={'command_id':'old','kind':'navigate'}
    robot.mode='commanded'
    ws=FakeWs([request('motor_disable','off')])
    asyncio.run(robot._receive_loop(ws))
    assert robot.motors_enabled is False and robot.command is None
    assert ws.of_type('command.ack')[0]['data']['accepted'] is True
    assert ws.of_type('command.result')[0]['data']['command_id']=='old'
