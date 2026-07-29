"""Charging / motor-power / dock behavior of the simulated robot.

The mock is what the dashboard is demonstrated and developed against. Dock and
undock are single operator actions: the robot opens its own charger and powers
its own motors as part of executing them, and undocking reverses in a straight
line rather than turning on the dock.

`charge_release` and `motor_enable` remain separate commands (the dashboard UI
does not send them) and keep their own interlocks.
"""
import asyncio
import math

from mock_robot.client import UNDOCK_DISTANCE_M, MockRobot
from mock_robot.world import DOCK

from test_commands import FakeWs, request


def docked_robot() -> MockRobot:
    """A robot sitting on the charger, exactly as it is after auto-docking."""
    robot = MockRobot("ws://unused", "t", "patrolbot-01", "calm")
    robot.robot.x, robot.robot.y = DOCK
    robot.mode = "to_dock"
    robot.robot.set_goal(DOCK)
    robot.step(0.1)  # arrival settles it onto the dock
    return robot


def test_auto_dock_leaves_motors_off_and_charging():
    robot = docked_robot()
    assert robot.on_dock is True
    assert robot.battery.charging is True
    assert robot.motors_enabled is False


def test_charging_pins_the_robot_in_place():
    robot = docked_robot()
    robot.robot.set_goal((0.0, 0.0))  # even with somewhere to be
    before = (robot.robot.x, robot.robot.y)
    for _ in range(20):
        robot.step(0.1)
    assert (robot.robot.x, robot.robot.y) == before


def test_charge_release_moves_nothing_and_leaves_motors_off():
    robot = docked_robot()
    ws = FakeWs([request("charge_release", command_id="rel-1")])

    async def run():
        before = (robot.robot.x, robot.robot.y)
        await robot._receive_loop(ws)
        assert robot.battery.charging is False
        assert robot.motors_enabled is False   # release is not "unlock wheels"
        assert robot.on_dock is True           # still physically on the dock
        for _ in range(20):
            robot.step(0.1)
        assert (robot.robot.x, robot.robot.y) == before
        results = ws.of_type("command.result")
        assert results and results[0]["data"]["outcome"] == "succeeded"

    asyncio.run(run())


def test_motor_enable_refused_while_charging():
    robot = docked_robot()
    ws = FakeWs([request("motor_enable", command_id="mot-1")])

    async def run():
        await robot._receive_loop(ws)
        ack = ws.of_type("command.ack")[0]["data"]
        assert ack["accepted"] is False
        assert "release charging" in ack["reason"].lower()
        assert robot.motors_enabled is False

    asyncio.run(run())


def test_undock_from_charging_is_one_command():
    """The dashboard's single button: undock accepted while still on charge
    with the motors off, and the robot sorts both out itself."""
    robot = docked_robot()
    ws = FakeWs([request("undock", command_id="und-1")])

    async def run():
        await robot._receive_loop(ws)
        ack = ws.of_type("command.ack")[0]["data"]
        assert ack["accepted"] is True
        assert robot.mode == "undocking"
        assert robot.battery.charging is False   # released by the robot
        assert robot.motors_enabled is True      # powered by the robot

        for _ in range(900):
            robot.step(0.1)
            if robot._command_arrived:
                break
        await robot._command_progress(ws)
        assert robot.on_dock is False
        result = [frame["data"] for frame in ws.of_type("command.result")
                  if frame["data"]["command_id"] == "und-1"]
        assert result and result[0]["outcome"] == "succeeded"

    asyncio.run(run())


def test_undock_reverses_in_a_straight_line():
    robot = docked_robot()
    robot.robot.yaw = 0.0  # facing +x, so it must back off toward -x
    start = (robot.robot.x, robot.robot.y)
    ws = FakeWs([request("undock", command_id="und-1")])

    async def run():
        await robot._receive_loop(ws)
        for _ in range(900):
            robot.step(0.1)
            if robot._command_arrived:
                break
        moved_x = robot.robot.x - start[0]
        assert moved_x < 0, "undock must reverse, not drive forward"
        assert abs(robot.robot.y - start[1]) < 1e-6, "undock must not turn"
        assert math.isclose(abs(moved_x), UNDOCK_DISTANCE_M, rel_tol=0.02)
        assert math.isclose(robot.robot.yaw, 0.0, abs_tol=1e-9)

    asyncio.run(run())


def test_dock_command_drives_home_and_starts_charging():
    robot = MockRobot("ws://unused", "t", "patrolbot-01", "calm")
    ws = FakeWs([request("dock", command_id="dock-1")])

    async def run():
        await robot._receive_loop(ws)
        assert robot.mode == "to_dock"
        assert robot.robot.goal == DOCK

        for _ in range(2000):
            robot.step(0.1)
            if robot._command_arrived:
                break
        await robot._command_progress(ws)
        assert robot.on_dock is True
        assert robot.battery.charging is True
        assert robot.motors_enabled is False  # back to the safe parked state
        result = [frame["data"] for frame in ws.of_type("command.result")
                  if frame["data"]["command_id"] == "dock-1"]
        assert result and result[0]["outcome"] == "succeeded"

    asyncio.run(run())


def test_dock_powers_its_own_motors():
    robot = MockRobot("ws://unused", "t", "patrolbot-01", "calm")
    robot.motors_enabled = False
    ws = FakeWs([request("dock", command_id="dock-1")])

    async def run():
        await robot._receive_loop(ws)
        assert ws.of_type("command.ack")[0]["data"]["accepted"] is True
        assert robot.motors_enabled is True

    asyncio.run(run())


def test_dock_command_preempts_an_active_destination():
    robot = MockRobot("ws://unused", "t", "patrolbot-01", "calm")
    ws = FakeWs([
        request("navigate_to_pose", command_id="nav-1", goal={"x": 1.0, "y": 1.0}),
        request("dock", command_id="dock-1"),
    ])

    async def run():
        await robot._receive_loop(ws)
        outcomes = {frame["data"]["command_id"]: frame["data"]["outcome"]
                    for frame in ws.of_type("command.result")}
        assert outcomes["nav-1"] == "canceled"
        assert robot.command["command_id"] == "dock-1"

    asyncio.run(run())


def test_capabilities_advertise_the_dock_commands():
    from mock_robot.client import CAPABILITIES

    for command in ("charge_release", "motor_enable", "dock", "undock"):
        assert command in CAPABILITIES
