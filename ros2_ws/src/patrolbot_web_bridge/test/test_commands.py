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


GOOD_BASE = {"estop_pressed": False, "motors_enabled": True,
             "hardware_state_valid": True, "link_connected": True,
             "telemetry_age": 0.1, "fault_flags": 0}


def nav(base_state=GOOD_BASE, localized=True, base_state_age=0.2):
    """A navigate request that would be allowed; each test spoils one thing."""
    return precheck("navigate_to_pose", {"x": 1, "y": 2}, base_state, localized,
                    base_state_age)


def test_precheck_allows_healthy_navigate():
    assert nav() is None


def test_precheck_requires_goal():
    assert precheck("navigate_to_pose", None, GOOD_BASE, True, 0.2) is not None
    assert precheck("set_initial_pose", None, GOOD_BASE) is not None
    assert precheck("stop", None, GOOD_BASE) is None


def test_precheck_fails_closed_without_base_state():
    assert nav(None) is not None
    # stop and set_initial_pose don't move the robot — allowed regardless.
    assert precheck("stop", None, None) is None
    assert precheck("set_initial_pose", {"x": 1, "y": 2}, None) is None


def test_precheck_refuses_navigate_without_localization():
    """The robot's own answer, not the browser's: the dashboard's "location set
    this session" flag lives in a tab and never reaches here."""
    reason = nav(localized=False)
    assert reason is not None and "where it is" in reason
    # Default is fail-closed — a caller that cannot answer does not get a pass.
    assert precheck("navigate_to_pose", {"x": 1, "y": 2}, GOOD_BASE) is not None


def test_precheck_refuses_navigate_on_stale_or_faulted_base():
    """motors_enabled/hardware_state_valid keep their last true values when the
    drive base link drops, so those alone are not evidence of a healthy base."""
    for spoiled in (dict(GOOD_BASE, link_connected=False),
                    dict(GOOD_BASE, telemetry_age=5.0)):
        reason = nav(spoiled)
        assert reason is not None and "stale" in reason

    reason = nav(dict(GOOD_BASE, fault_flags=8))
    assert reason is not None and "fault (code 8)" in reason


def test_precheck_refuses_navigate_when_the_base_stopped_reporting():
    """telemetry_age is a number inside the last payload: if the drive-base
    subscription dies, it keeps saying 0.1 forever. Only this node's own
    receipt time notices, exactly as the dashboard server's gate does."""
    for age in (9.0, None):
        reason = nav(base_state_age=age)
        assert reason is not None and "stale" in reason
    # ...and a payload claiming a negative age is a broken clock, not a fresher
    # reading.
    assert nav(dict(GOOD_BASE, telemetry_age=-5.0)) is not None


def test_precheck_refuses_navigate_on_the_dock():
    """Coming off the dock is undock's job. A hand-docked robot charges with
    its motors still enabled, which passes every other check."""
    for docked in (dict(GOOD_BASE, charge_state="charging"),
                   dict(GOOD_BASE, charge_state="docked"),
                   dict(GOOD_BASE, charge_state="idle",
                        dock_state="DOCKED_CONFIRMED", dock_state_valid=True),
                   # An unusable dock observer says nothing about the dock. It
                   # must not overrule a charge_state reporting current flowing
                   # in — the normalizer emits exactly this when the observer
                   # is absent or invalid while the robot is on charge.
                   dict(GOOD_BASE, charge_state="charging",
                        dock_state="UNKNOWN", dock_state_valid=False)):
        reason = nav(docked)
        assert reason is not None and "on its charger" in reason.lower()


def test_precheck_names_the_charger_when_it_cut_the_motors():
    """The base cuts motor power while the charger is engaged. Saying "enable
    the motors" to an operator who has just enabled them sent them round that
    loop five times on 2026-07-28."""
    on_charge = dict(GOOD_BASE, motors_enabled=False, charge_state="charging")
    reason = nav(on_charge)
    assert reason is not None and "charger" in reason.lower()
    assert "enable" not in reason.lower()

    off_charge = dict(GOOD_BASE, motors_enabled=False, charge_state="not_charging")
    reason = nav(off_charge)
    assert reason is not None and "motors are off" in reason.lower()


def test_precheck_trusts_clear_dock_observer_over_stale_charge():
    clear = dict(
        GOOD_BASE,
        motors_enabled=False,
        charge_state="float",
        dock_state="CLEAR_CONFIRMED",
        dock_state_valid=True,
    )
    reason = nav(clear)
    assert reason is not None and "motors are off" in reason.lower()
    assert "charger is engaged" not in reason.lower()


def test_precheck_blocks_estop_and_motors_off():
    estop = dict(GOOD_BASE, estop_pressed=True)
    motors_off = dict(GOOD_BASE, motors_enabled=False)
    invalid = dict(GOOD_BASE, hardware_state_valid=False)
    for base_state in (estop, motors_off, invalid):
        reason = nav(base_state)
        assert reason is not None
        # Plain language, no ROS jargon.
        assert "/" not in reason and "_" not in reason
