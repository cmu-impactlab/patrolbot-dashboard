from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from app.commands.gates import StateFacts, dock_reset_reason
from app.protocol.messages import CommandRequestData
from app.commands.broker import CommandBroker


def ready():
    return StateFacts(online=True, link_connected=True, telemetry_age=.01,
                      base_state_age=.01, pose_age=.01, hardware_state_valid=True,
                      stationary=True, capabilities=("dock_reset",),
                      dock_state="FAULT_STOPPED", dock_state_valid=True)


@pytest.mark.parametrize("changes", [
    {"estop_pressed": True}, {"fault_flags": 256},
    {"operation_outcome_unknown": True}, {"undock_active": True},
    {"navigating": True}, {"stationary": False}, {"pose_age": None},
    {"base_state_age": 10}, {"link_connected": False},
    {"hardware_state_valid": False}, {"dock_state_valid": False},
    {"dock_state": "DEPARTING"}, {"capabilities": ()},
])
def test_recovery_refuses_unresolved_or_unknown_conditions(changes):
    assert dock_reset_reason(replace(ready(), **changes))


@pytest.mark.parametrize("state", ["FAULT_STOPPED", "CLEAR_CONFIRMED", "DOCKED_CONFIRMED"])
def test_recovery_accepts_confirmed_stationary_state_without_localization_or_motor_enable(state):
    assert dock_reset_reason(replace(ready(), dock_state=state)) is None
    assert CommandRequestData(command_id="recover", command="dock_reset").command == "dock_reset"


@pytest.mark.parametrize("motion_age,session,moving,allowed", [
    (.01, 1, False, True), (2., 1, False, False),
    (.01, 2, False, False), (.01, 1, True, False),
])
def test_broker_uses_session_bound_raw_motion_not_amcl_pose(motion_age, session, moving, allowed):
    base = NS(link_connected=True, telemetry_age=.01, hardware_state_valid=True,
              charge_state="not_charging", motors_enabled=True, estop_pressed=False,
              fault_flags=0, bumpers_front=False, bumpers_rear=False,
              dock_state="FAULT_STOPPED", dock_state_valid=True, session_generation=1)
    state = NS(connection="online", capabilities=["dock_reset"],
               base_state=NS(data=base, age=lambda: .01),
               pose=NS(data=None, age=lambda: None),
               motion=NS(age=lambda: motion_age, data=NS(
                   sample_age_s=.01, receipt_age_s=.01, session_generation=session,
                   linear_velocity=.1 if moving else 0., angular_velocity=0.)))
    broker = CommandBroker.__new__(CommandBroker)
    broker.lidar_recovery_required = set()
    broker.lidar_state_after = {}
    broker.active = {}
    reason = broker._validate_robot_state(NS(robot_id="robot", state=state), "dock_reset")
    assert (reason is None) is allowed
