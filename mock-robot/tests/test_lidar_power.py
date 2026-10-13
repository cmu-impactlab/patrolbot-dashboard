import asyncio
from test_commands import FakeWs, make_robot, request


def test_power_off_blocks_pose_and_goals_but_not_stop_and_requires_new_pose_after_on():
    robot = make_robot()
    robot.mode = "idle"
    off = request("set_lidar_power", "off")
    off["data"]["enabled"] = False
    pose = request("set_initial_pose", "pose", {"x": 1, "y": 1})
    stop = request("stop", "stop")
    ws = FakeWs([off, pose, stop])
    asyncio.run(robot._receive_loop(ws))
    acks = {frame["data"]["command_id"]: frame["data"]["accepted"] for frame in ws.of_type("command.ack")}
    assert acks == {"off": True, "pose": False, "stop": True}
    assert robot.lidar_state == "OFF"
    assert robot.localization_recovery_required is True


def test_power_control_refuses_non_boolean_target_and_active_navigation():
    for enabled in [None, "false", 1]:
        robot = make_robot()
        power = request("set_lidar_power")
        power["data"]["enabled"] = enabled
        ws = FakeWs([power])
        asyncio.run(robot._receive_loop(ws))
        assert ws.of_type("command.ack")[0]["data"]["accepted"] is False
