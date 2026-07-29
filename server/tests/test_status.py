from app.protocol.messages import (
    BaseStateData,
    BatteryData,
    DiagnosticItem,
    DiagnosticsData,
    GoalData,
    PathData,
    PoseData,
)
from app.telemetry.status import derive_health, derive_status


def base_state(**overrides) -> BaseStateData:
    defaults = dict(
        session_generation=1, link_connected=True, telemetry_age=0.1,
        hardware_state_valid=True, charge_state="not_charging", motors_enabled=True,
        estop_pressed=False, fault_flags=0, stall_value=0,
        bumpers_front=False, bumpers_rear=False,
    )
    defaults.update(overrides)
    return BaseStateData(**defaults)


def pose(**overrides) -> PoseData:
    defaults = dict(x=0.0, y=0.0, yaw=0.0, linear_velocity=0.0, angular_velocity=0.0)
    defaults.update(overrides)
    return PoseData(**defaults)


def test_offline_wins():
    result = derive_status(connection="offline", base_state=base_state(estop_pressed=True),
                           diagnostics=None, pose=None, path=None)
    assert result.status == "offline"


def test_estop_needs_attention():
    result = derive_status(connection="online", base_state=base_state(estop_pressed=True),
                           diagnostics=None, pose=None, path=None)
    assert result.status == "needs_attention"
    assert "emergency stop" in result.detail.lower()


def test_diag_error_needs_attention():
    diags = DiagnosticsData(items=[DiagnosticItem(name="laser", level="ERROR", message="No data")])
    result = derive_status(connection="online", base_state=base_state(),
                           diagnostics=diags, pose=None, path=None)
    assert result.status == "needs_attention"


def test_charging():
    result = derive_status(connection="online", base_state=base_state(charge_state="charging"),
                           diagnostics=None, pose=None, path=None)
    assert result.status == "charging"


def test_docked_not_charging():
    result = derive_status(connection="online", base_state=base_state(charge_state="docked"),
                           diagnostics=None, pose=None, path=None)
    assert result.status == "docked"


def test_clear_dock_observer_overrides_stale_float_charge_state():
    result = derive_status(
        connection="online",
        base_state=base_state(
            charge_state="float",
            dock_state="CLEAR_CONFIRMED",
            dock_state_valid=True,
            motors_enabled=False,
        ),
        diagnostics=None, pose=None, path=None,
    )
    assert result.status == "paused"


def test_undock_active_has_specific_status():
    result = derive_status(
        connection="online",
        base_state=base_state(
            charge_state="float",
            dock_state="DEPARTING",
            dock_state_valid=True,
            undock_active=True,
        ),
        diagnostics=None, pose=None, path=None,
    )
    assert result.status == "navigating"
    assert "charging dock" in result.detail


def test_paused_when_motors_off():
    result = derive_status(connection="online", base_state=base_state(motors_enabled=False),
                           diagnostics=None, pose=None, path=None)
    assert result.status == "paused"


def test_navigating_when_goal_and_moving():
    path = PathData(points=[(0, 0), (1, 1)], goal=GoalData(x=1.0, y=1.0))
    result = derive_status(connection="online", base_state=base_state(),
                           diagnostics=None, pose=pose(linear_velocity=0.3), path=path)
    assert result.status == "navigating"


def test_ready_default():
    result = derive_status(connection="online", base_state=base_state(),
                           diagnostics=None, pose=pose(), path=None)
    assert result.status == "ready"


def test_health_offline_overall():
    health = derive_health(connection="offline", base_state=None, battery=None,
                           diagnostics=None, pose=None, resources=None, lidar_age_s=None)
    assert health.overall == "offline"


def test_health_battery_warning():
    battery = BatteryData(voltage=23.0, percentage=15.0, charging=False)
    health = derive_health(connection="online", base_state=base_state(), battery=battery,
                           diagnostics=None, pose=pose(), resources=None, lidar_age_s=0.5)
    battery_sub = next(s for s in health.subsystems if s.id == "battery")
    assert battery_sub.level == "warning"
    assert health.overall in ("warning", "fault")


def test_health_localization_warning():
    health = derive_health(connection="online", base_state=base_state(), battery=None,
                           diagnostics=None, pose=pose(localized=False), resources=None, lidar_age_s=0.5)
    loc = next(s for s in health.subsystems if s.id == "localization")
    assert loc.level == "warning"
    assert loc.action is not None


def test_health_every_subsystem_has_plain_message():
    health = derive_health(connection="online", base_state=base_state(), battery=None,
                           diagnostics=None, pose=pose(), resources=None, lidar_age_s=0.1)
    for sub in health.subsystems:
        assert sub.message, sub.id
        # Plain language: no ROS jargon in user-facing strings.
        for term in ("amcl", "costmap", "ros", "nav2", "topic", "/scan"):
            assert term not in sub.message.lower(), (sub.id, sub.message)
