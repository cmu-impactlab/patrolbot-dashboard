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


def test_precheck_names_the_charger_when_it_cut_the_motors():
    """The base cuts motor power while the charger is engaged. Saying "enable
    the motors" to an operator who has just enabled them sent them round that
    loop five times on 2026-07-28."""
    on_charge = dict(GOOD_BASE, motors_enabled=False, charge_state="charging")
    reason = precheck("navigate_to_pose", {"x": 1, "y": 2}, on_charge)
    assert reason is not None and "charger" in reason.lower()
    assert "enable" not in reason.lower()

    off_charge = dict(GOOD_BASE, motors_enabled=False, charge_state="not_charging")
    reason = precheck("navigate_to_pose", {"x": 1, "y": 2}, off_charge)
    assert reason is not None and "motors are off" in reason.lower()


def test_precheck_trusts_clear_dock_observer_over_stale_charge():
    clear = dict(
        GOOD_BASE,
        motors_enabled=False,
        charge_state="float",
        dock_state="CLEAR_CONFIRMED",
        dock_state_valid=True,
    )
    reason = precheck("navigate_to_pose", {"x": 1, "y": 2}, clear)
    assert reason is not None and "motors are off" in reason.lower()
    assert "charger is engaged" not in reason.lower()


def test_precheck_blocks_estop_and_motors_off():
    estop = dict(GOOD_BASE, estop_pressed=True)
    motors_off = dict(GOOD_BASE, motors_enabled=False)
    invalid = dict(GOOD_BASE, hardware_state_valid=False)
    for base_state in (estop, motors_off, invalid):
        reason = precheck("navigate_to_pose", {"x": 1, "y": 2}, base_state)
        assert reason is not None
        # Plain language, no ROS jargon.
        assert "/" not in reason and "_" not in reason
