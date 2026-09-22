import asyncio
import json

from mock_robot.client import MockRobot


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
    data = {"command_id": command_id, "command": command}
    if goal is not None:
        data["goal"] = goal
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
