"""Pure-logic tests for the command path (no ROS graph needed)."""
import math

from patrolbot_web_bridge.commands import (
    map_goal_status,
    precheck,
    yaw_to_quaternion,
)


def test_yaw_to_quaternion_round_trip():
    for yaw in (0.0, math.pi / 2, -math.pi / 2, math.pi - 0.01):
        x, y, z, w = yaw_to_quaternion(yaw)
        assert x == 0.0 and y == 0.0
        recovered = 2.0 * math.atan2(z, w)
        assert abs(recovered - yaw) < 1e-9
        assert abs(x * x + y * y + z * z + w * w - 1.0) < 1e-9


def test_goal_status_mapping():
    assert map_goal_status(4) == "succeeded"
    assert map_goal_status(5) == "canceled"
    assert map_goal_status(6) == "failed"
    assert map_goal_status(0) == "failed"  # unknown codes fail closed


GOOD_BASE = {"estop_pressed": False, "motors_enabled": True, "hardware_state_valid": True}


def test_precheck_allows_healthy_navigate():
    assert precheck("navigate_to_pose", {"x": 1, "y": 2}, GOOD_BASE) is None


def test_precheck_requires_goal():
    assert precheck("navigate_to_pose", None, GOOD_BASE) is not None
    assert precheck("set_initial_pose", None, GOOD_BASE) is not None
    assert precheck("stop", None, GOOD_BASE) is None


def test_precheck_fails_closed_without_base_state():
    assert precheck("navigate_to_pose", {"x": 1, "y": 2}, None) is not None
    # stop and set_initial_pose don't move the robot — allowed regardless.
    assert precheck("stop", None, None) is None
    assert precheck("set_initial_pose", {"x": 1, "y": 2}, None) is None


def test_precheck_blocks_estop_and_motors_off():
    estop = dict(GOOD_BASE, estop_pressed=True)
    motors_off = dict(GOOD_BASE, motors_enabled=False)
    invalid = dict(GOOD_BASE, hardware_state_valid=False)
    for base_state in (estop, motors_off, invalid):
        reason = precheck("navigate_to_pose", {"x": 1, "y": 2}, base_state)
        assert reason is not None
        # Plain language, no ROS jargon.
        assert "/" not in reason and "_" not in reason
