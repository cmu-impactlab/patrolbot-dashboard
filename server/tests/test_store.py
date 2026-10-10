from app.protocol.messages import BaseStateData, BatteryData, DiagnosticItem, DiagnosticsData
from app.settings import Settings
from app.telemetry.store import RobotState


def make_state() -> RobotState:
    return RobotState(Settings(), "patrolbot-01")


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


def test_estop_transition_emits_single_event():
    state = make_state()
    events = state.record_base_state(base_state(estop_pressed=True))
    assert len(events) == 1 and events[0].severity == "critical"
    # Repeated estop frames do not spam events.
    assert state.record_base_state(base_state(estop_pressed=True)) == []
    # Release emits an info event.
    release = state.record_base_state(base_state(estop_pressed=False))
    assert len(release) == 1 and release[0].severity == "info"


def test_bumper_transition_event():
    state = make_state()
    events = state.record_base_state(base_state(bumpers_front=True))
    assert any("bumper" in e.title.lower() for e in events)
    assert state.record_base_state(base_state(bumpers_front=True)) == []


def test_diagnostics_level_transitions():
    state = make_state()
    warn = DiagnosticsData(items=[DiagnosticItem(name="laser", level="WARN", message="Slow")])
    ok = DiagnosticsData(items=[DiagnosticItem(name="laser", level="OK", message="Fine")])
    assert len(state.record_diagnostics(warn)) == 1
    assert state.record_diagnostics(warn) == []  # unchanged level: no event
    recovered = state.record_diagnostics(ok)
    assert len(recovered) == 1 and recovered[0].severity == "info"


def test_diagnostic_sources_merge_stale_and_expire_deterministically(monkeypatch):
    now = 100.0
    monkeypatch.setattr("app.telemetry.store.time.monotonic", lambda: now)
    state = make_state()
    fault = DiagnosticItem(name="laser", level="ERROR", message="No scan")
    ok = DiagnosticItem(name="base", level="OK", message="Ready")

    state.record_diagnostics(DiagnosticsData(items=[fault]))
    state.record_diagnostics(DiagnosticsData(items=[ok]))
    assert [(item.name, item.level) for item in state.diagnostics.data.items] == [
        ("base", "OK"), ("laser", "ERROR")
    ]

    # A fresh report from one component must not keep an absent component
    # looking current forever; it becomes explicitly stale after the bound.
    now += 31
    state.record_diagnostics(DiagnosticsData(items=[ok]))
    laser = next(item for item in state.diagnostics.data.items if item.name == "laser")
    assert laser.level == "STALE"
    assert state.health is None
    state.recompute()
    assert next(item for item in state.health.subsystems if item.id == "systems").level == "warning"

    now += 151
    state.recompute()
    assert state.diagnostics.data is None


def test_reconnect_clears_diagnostic_sources():
    state = make_state()
    state.record_diagnostics(DiagnosticsData(items=[
        DiagnosticItem(name="laser", level="ERROR", message="No scan")
    ]))
    state.set_connection("online")
    state.set_connection("offline")
    state.set_connection("online")
    assert state.diagnostics.data is None
    assert state._diag_levels == {}


def test_battery_low_threshold_event_once():
    state = make_state()
    assert state.record_battery(BatteryData(voltage=24.5, percentage=50.0, charging=False)) == []
    low = state.record_battery(BatteryData(voltage=23.5, percentage=19.0, charging=False))
    assert len(low) == 1 and low[0].severity == "warning"
    assert state.record_battery(BatteryData(voltage=23.4, percentage=18.0, charging=False)) == []


def test_charging_transitions_events():
    state = make_state()
    state.record_battery(BatteryData(voltage=24.0, percentage=50.0, charging=False))
    started = state.record_battery(BatteryData(voltage=24.2, percentage=50.0, charging=True))
    assert any("charging started" in e.title.lower() for e in started)
    stopped = state.record_battery(BatteryData(voltage=25.0, percentage=80.0, charging=False))
    assert any("charging stopped" in e.title.lower() for e in stopped)


def test_connection_transitions():
    state = make_state()
    assert state.connection == "offline"
    events = state.set_connection("online")
    assert len(events) == 1 and events[0].severity == "info"
    assert state.set_connection("online") == []
    events = state.set_connection("offline")
    assert len(events) == 1 and events[0].severity == "critical"


def test_snapshot_includes_battery_estimate():
    state = make_state()
    state.set_connection("online")
    state.record_battery(BatteryData(voltage=24.5, percentage=80.0, charging=False))
    snap = state.snapshot()
    assert snap.battery is not None
    assert snap.battery.estimate is not None
    assert snap.battery_estimate is not None
    assert snap.robot_status.status is not None


def test_stall_detection_flags_stuck():
    import time as _time

    from app.protocol.messages import GoalData, PathData, PoseData

    state = RobotState(Settings(stall_warning_s=0.05), "patrolbot-01")
    state.connection = "online"
    state.record_base_state(base_state())
    state.record_path(PathData(points=[], goal=GoalData(x=5.0, y=5.0)))

    still = PoseData(x=1.0, y=1.0, yaw=0.0, linear_velocity=0.0, angular_velocity=0.0)
    assert state.record_pose(still) == []  # stall clock starts, no event yet
    _time.sleep(0.08)
    events = state.record_pose(still)
    assert len(events) == 1 and events[0].severity == "warning"
    assert "stuck" in events[0].title.lower()
    status, _health = state.recompute()
    assert status is not None and status.status == "stuck"
    # No event spam while still stalled.
    assert state.record_pose(still) == []

    # Movement clears the stall.
    moving = PoseData(x=1.2, y=1.0, yaw=0.0, linear_velocity=0.3, angular_velocity=0.0)
    assert state.record_pose(moving) == []
    status, _health = state.recompute()
    assert status is not None and status.status == "navigating"


def test_stall_not_counted_when_stop_is_explained():
    import time as _time

    from app.protocol.messages import GoalData, PathData, PoseData

    state = RobotState(Settings(stall_warning_s=0.05), "patrolbot-01")
    state.connection = "online"
    state.record_base_state(base_state(motors_enabled=False))
    state.record_path(PathData(points=[], goal=GoalData(x=5.0, y=5.0)))
    still = PoseData(x=1.0, y=1.0, yaw=0.0, linear_velocity=0.0, angular_velocity=0.0)
    state.record_pose(still)
    _time.sleep(0.08)
    assert state.record_pose(still) == []  # paused motors explain the stop
    status, _health = state.recompute()
    assert status is not None and status.status == "paused"
