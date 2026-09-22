from app.protocol.messages import (
    BaseStateData,
    BatteryData,
    DiagnosticItem,
    DiagnosticsData,
    GoalData,
    PathData,
    PoseData,
    ResourcesData,
)
from app.telemetry.status import derive_health, derive_status


def base_state(**overrides) -> BaseStateData:
    defaults = dict(
        session_generation=1, link_connected=True, telemetry_age=0.1,
        hardware_state_valid=True, charge_state="not_charging", motors_enabled=True,
        estop_pressed=False, fault_flags=0, stall_value=0,
        bumpers_front=False, bumpers_rear=False,
        odom_epoch_valid=True, localization_recovery_required=False,
        localization_seed_stamp_ns=1,
    )
    defaults.update(overrides)
    return BaseStateData(**defaults)


# Every slice received a moment ago. Health now judges age as well as value,
# so a test that does not say how old its data is is asking about a robot that
# has gone quiet — which is its own test, below.
FRESH = dict(base_state_age_s=0.5, battery_age_s=0.5, diagnostics_age_s=0.5,
             pose_age_s=0.5, resources_age_s=0.5)


def pose(**overrides) -> PoseData:
    defaults = dict(x=0.0, y=0.0, yaw=0.0, linear_velocity=0.0, angular_velocity=0.0)
    defaults.update(overrides)
    return PoseData(**defaults)


def test_offline_wins():
    result = derive_status(connection="offline", base_state=base_state(estop_pressed=True),
                           diagnostics=None, pose=None, path=None)
    assert result.status == "offline"


def test_estop_needs_attention():
    result = derive_status(base_state_age_s=0.5, connection="online", base_state=base_state(estop_pressed=True),
                           diagnostics=None, pose=None, path=None)
    assert result.status == "needs_attention"
    assert "emergency stop" in result.detail.lower()


def test_diag_error_needs_attention():
    diags = DiagnosticsData(items=[DiagnosticItem(name="laser", level="ERROR", message="No data")])
    result = derive_status(base_state_age_s=0.5, diagnostics_age_s=0.5, connection="online", base_state=base_state(),
                           diagnostics=diags, pose=None, path=None)
    assert result.status == "needs_attention"


def test_charging():
    result = derive_status(base_state_age_s=0.5, connection="online", base_state=base_state(charge_state="charging"),
                           diagnostics=None, pose=None, path=None)
    assert result.status == "charging"


def test_docked_not_charging():
    result = derive_status(base_state_age_s=0.5, connection="online", base_state=base_state(charge_state="docked"),
                           diagnostics=None, pose=None, path=None)
    assert result.status == "docked"


def test_clear_dock_observer_overrides_stale_float_charge_state():
    result = derive_status(
        base_state_age_s=0.5,
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
        base_state_age_s=0.5,
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
    result = derive_status(base_state_age_s=0.5, connection="online", base_state=base_state(motors_enabled=False),
                           diagnostics=None, pose=None, path=None)
    assert result.status == "paused"


def test_navigating_when_goal_and_moving():
    path = PathData(points=[(0, 0), (1, 1)], goal=GoalData(x=1.0, y=1.0))
    result = derive_status(base_state_age_s=0.5, pose_age_s=0.5, connection="online", base_state=base_state(),
                           diagnostics=None, pose=pose(linear_velocity=0.3), path=path)
    assert result.status == "navigating"


def test_ready_default():
    result = derive_status(base_state_age_s=0.5, connection="online", base_state=base_state(),
                           diagnostics=None, pose=pose(), path=None)
    assert result.status == "ready"


def test_health_offline_overall():
    health = derive_health(connection="offline", base_state=None, battery=None,
                           diagnostics=None, pose=None, resources=None, lidar_age_s=None)
    assert health.overall == "offline"


def test_health_battery_warning():
    battery = BatteryData(voltage=23.0, percentage=15.0, charging=False)
    health = derive_health(connection="online", base_state=base_state(), battery=battery,
                           diagnostics=None, pose=pose(), resources=None, lidar_age_s=0.5,
                           **FRESH)
    battery_sub = next(s for s in health.subsystems if s.id == "battery")
    assert battery_sub.level == "warning"
    assert health.overall in ("warning", "fault")


def test_health_localization_warning():
    health = derive_health(connection="online", base_state=base_state(), battery=None,
                           diagnostics=None, pose=pose(localized=False), resources=None,
                           lidar_age_s=0.5, **FRESH)
    loc = next(s for s in health.subsystems if s.id == "localization")
    assert loc.level == "warning"
    assert loc.action is not None


def test_health_localization_is_not_healthy_during_epoch_recovery():
    for overrides in (
        {"odom_epoch_valid": False},
        {"localization_recovery_required": True,
         "localization_recovery_stage": "WAIT_FOR_AMCL"},
        {"localization_seed_stamp_ns": 0},
    ):
        health = derive_health(connection="online", base_state=base_state(**overrides),
                               battery=None, diagnostics=None, pose=pose(localized=True),
                               resources=None, lidar_age_s=0.5, **FRESH)
        loc = next(s for s in health.subsystems if s.id == "localization")
        assert loc.level == "warning"
        assert "recover" in loc.message.lower()


def test_health_every_subsystem_has_plain_message():
    health = derive_health(connection="online", base_state=base_state(), battery=None,
                           diagnostics=None, pose=pose(), resources=None, lidar_age_s=0.1,
                           **FRESH)
    for sub in health.subsystems:
        assert sub.message, sub.id
        # Plain language: no ROS jargon in user-facing strings.
        for term in ("amcl", "costmap", "ros", "nav2", "topic", "/scan"):
            assert term not in sub.message.lower(), (sub.id, sub.message)


# -- the drive base goes quiet while the Raspberry Pi stays up ---------------

def sbc_off_health(**overrides):
    """The production state on 2026-08-09: the Pi is connected and heartbeating,
    the drive-base controller has been off for days, and every slice that comes
    through it stopped arriving. The last values are still in hand and still
    say pleasant things — motors_enabled true, bumpers not pressed."""
    kwargs = dict(
        connection="online",
        base_state=base_state(link_connected=False, hardware_state_valid=False,
                              motors_enabled=True, charge_state="unknown",
                              bumpers_valid=False),
        battery=BatteryData(voltage=24.0, percentage=80.0, charging=False),
        diagnostics=DiagnosticsData(items=[DiagnosticItem(
            name="lifecycle_manager_navigation: Nav2 Health", level="OK",
            message="Managed nodes are active")]),
        pose=pose(localized=True),
        resources=None,
        lidar_age_s=270000.0,
        base_state_age_s=270000.0, battery_age_s=270000.0,
        diagnostics_age_s=0.5,  # the Pi's own nodes keep reporting
        pose_age_s=270000.0, resources_age_s=0.5,
    )
    kwargs.update(overrides)
    return derive_health(**kwargs)


def levels(health) -> dict[str, str]:
    return {sub.id: sub.level for sub in health.subsystems}


def test_nothing_stale_is_reported_as_healthy():
    """The whole finding: no subsystem may claim to be well on the strength of
    a reading that stopped arriving days ago."""
    health = sbc_off_health()
    for subsystem_id in ("drive_base", "battery", "sensors", "localization"):
        assert levels(health)[subsystem_id] != "healthy", subsystem_id


def test_the_connection_subsystem_speaks_only_for_the_pi():
    """It is the bridge's heartbeat, and the bridge runs on the Pi. Saying "the
    robot is sending live data" claimed the drive base was alive too."""
    connection = next(s for s in sbc_off_health().subsystems if s.id == "connection")
    assert connection.level == "healthy"  # the Pi genuinely is reachable
    assert "onboard computer" in connection.message
    assert "the robot is sending live data" not in connection.message.lower()


def test_a_silent_battery_is_not_a_good_battery():
    battery = next(s for s in sbc_off_health().subsystems if s.id == "battery")
    assert battery.level == "offline"
    assert "OK" not in battery.message


def test_localization_does_not_claim_a_position_it_stopped_receiving():
    location = next(s for s in sbc_off_health().subsystems if s.id == "localization")
    assert location.level == "offline"
    assert "knows where it is" not in location.message.lower()


def test_systems_green_does_not_overclaim_its_coverage():
    """One Nav2 lifecycle message is what the Pi can still report. It is not
    grounds for "All monitored systems report OK"."""
    systems = next(s for s in sbc_off_health().subsystems if s.id == "systems")
    assert "all monitored systems" not in systems.message.lower()


def test_never_having_heard_from_the_systems_is_not_healthy():
    health = sbc_off_health(diagnostics=None, diagnostics_age_s=None)
    assert levels(health)["systems"] == "offline"
    health = sbc_off_health(diagnostics=DiagnosticsData(items=[]))
    assert levels(health)["systems"] == "offline"


def test_a_live_robot_still_reads_healthy():
    """The counterweight: none of the above may turn a healthy robot amber."""
    health = derive_health(
        connection="online", base_state=base_state(),
        battery=BatteryData(voltage=25.0, percentage=90.0, charging=False),
        diagnostics=DiagnosticsData(items=[DiagnosticItem(
            name="Nav2", level="OK", message="Managed nodes are active")]),
        pose=pose(localized=True),
        resources=ResourcesData(cpu_percent=10.0, memory_percent=30.0,
                                cpu_temp_c=45.0, disk_percent=40.0),
        lidar_age_s=0.2, **FRESH)
    assert health.overall == "healthy"
    assert all(sub.level == "healthy" for sub in health.subsystems), levels(health)


def test_a_hazard_reported_before_the_robot_went_quiet_keeps_its_severity():
    """A pressed e-stop does not stop mattering because the robot then fell
    silent. Reporting only "stopped reporting" would downgrade a real hazard to
    grey — the same untruth this guards against, pointing the other way."""
    health = sbc_off_health(base_state=base_state(
        link_connected=False, hardware_state_valid=False, estop_pressed=True,
        bumpers_valid=False))
    assert levels(health)["drive_base"] == "fault"
    assert health.overall == "fault"

    status = derive_status(connection="online", base_state_age_s=270000.0,
                           base_state=base_state(estop_pressed=True),
                           diagnostics=None, pose=None, path=None)
    assert status.status == "needs_attention"
    assert "emergency stop" in status.detail.lower()
    assert "has stopped reporting" in status.detail.lower()


def test_a_quiet_robot_with_nothing_alarming_is_not_dressed_up_as_a_fault():
    status = derive_status(connection="online", base_state_age_s=270000.0,
                           base_state=base_state(), diagnostics=None,
                           pose=None, path=None)
    assert status.status == "needs_attention"
    assert "stopped reporting" in status.detail.lower()


def test_a_robot_that_has_said_nothing_is_not_ready():
    """Connected to the Pi, no telemetry at all. This used to read "The robot is
    ready for a new task."."""
    status = derive_status(connection="online", base_state=None,
                           diagnostics=None, pose=None, path=None)
    assert status.status != "ready"


def test_unreadable_bumpers_show_up_on_the_health_card():
    """The gate refuses an undock on this and the widget shows Unknown; the
    health card used to say the motor controller was responding normally."""
    health = derive_health(
        connection="online", base_state=base_state(bumpers_valid=False),
        battery=None, diagnostics=None, pose=pose(), resources=None,
        lidar_age_s=0.5, **FRESH)
    drive_base = next(s for s in health.subsystems if s.id == "drive_base")
    assert drive_base.level == "warning"
    assert "bumpers" in drive_base.message


def test_the_robots_own_link_age_counts_too():
    """Two ages: how long ago we heard from the robot, and how long ago the
    robot heard from its drive base. A fresh frame carrying an old internal
    reading was reported as responding normally."""
    health = derive_health(
        connection="online", base_state=base_state(telemetry_age=300.0),
        battery=None, diagnostics=None, pose=pose(), resources=None,
        lidar_age_s=0.5, **FRESH)
    assert levels(health)["drive_base"] == "fault"


def test_a_battery_with_no_level_is_not_a_healthy_battery():
    health = derive_health(
        connection="online", base_state=base_state(),
        battery=BatteryData(voltage=20.0, percentage=None, charging=False),
        diagnostics=None, pose=pose(), resources=None, lidar_age_s=0.5, **FRESH)
    battery = next(s for s in health.subsystems if s.id == "battery")
    assert battery.level != "healthy"
    assert "not reporting a battery level" in battery.message


def test_a_fault_flag_alone_survives_the_robot_going_quiet():
    """Isolated from the e-stop and link cases: a fault code and nothing else,
    on a frame that then stopped arriving."""
    health = sbc_off_health(base_state=base_state(fault_flags=4))
    assert levels(health)["drive_base"] == "fault"


def test_a_broken_link_on_a_fresh_frame_is_not_ready():
    """The top line and the health card have to agree. This combination —
    current frame, dead link to the robot's own drive base — read "ready" at the
    top of the screen while the health card showed a fault."""
    fresh_but_disconnected = base_state(link_connected=False)
    status = derive_status(connection="online", base_state_age_s=0.5,
                           base_state=fresh_but_disconnected,
                           diagnostics=None, pose=None, path=None)
    health = derive_health(connection="online", base_state=fresh_but_disconnected,
                           battery=None, diagnostics=None, pose=pose(),
                           resources=None, lidar_age_s=0.5, **FRESH)
    assert status.status == "needs_attention"
    assert levels(health)["drive_base"] == "fault"


def test_a_stale_error_diagnostic_is_not_a_current_problem():
    status = derive_status(connection="online", base_state_age_s=0.5,
                           base_state=base_state(), pose=None, path=None,
                           diagnostics_age_s=270000.0,
                           diagnostics=DiagnosticsData(items=[DiagnosticItem(
                               name="x", level="ERROR", message="boom")]))
    assert status.status != "needs_attention"


def test_a_stale_moving_pose_is_not_a_navigating_robot():
    status = derive_status(
        connection="online", base_state_age_s=0.5, base_state=base_state(),
        diagnostics=None, pose=pose(linear_velocity=0.5), pose_age_s=270000.0,
        path=PathData(points=[], goal=GoalData(x=1.0, y=1.0)))
    assert status.status != "navigating"


def test_bumpers_the_robot_never_mentions_are_not_reported_either_way():
    """The undock gate is deliberately stricter than this line: it authorises
    motion, so it requires an explicit yes. The health card only repeats what
    the robot said, and about an unmentioned bumper it says nothing."""
    health = derive_health(
        connection="online", base_state=base_state(bumpers_valid=None),
        battery=None, diagnostics=None, pose=pose(), resources=None,
        lidar_age_s=0.5, **FRESH)
    drive_base = next(s for s in health.subsystems if s.id == "drive_base")
    assert drive_base.level == "healthy"
    assert "bumper" not in drive_base.message.lower()


def test_fresh_pose_does_not_hide_stale_localization_readiness():
    for age in (None, 16.0, float("nan"), float("inf"), -1.0):
        ages = {**FRESH, "base_state_age_s": age}
        health = derive_health(connection="online", base_state=base_state(),
                               battery=None, diagnostics=None, pose=pose(localized=True),
                               resources=None, lidar_age_s=0.5, **ages)
        loc = next(s for s in health.subsystems if s.id == "localization")
        assert loc.level == "warning"
        assert "not current" in loc.message
