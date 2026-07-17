"""Robot status and system-health derivation. Pure functions, unit-tested.

All user-facing strings here are plain language — no ROS terminology. The
frontend has its own copy table for widget text; these strings are the
server-side fallbacks used in snapshots and events.
"""
from __future__ import annotations

from ..protocol.envelope import utc_now
from ..protocol.messages import (
    BaseStateData,
    BatteryData,
    DiagnosticsData,
    PathData,
    PoseData,
    ResourcesData,
    RobotStatusData,
    Subsystem,
    SystemHealthData,
)

CHARGING_STATES = {"charging", "bulk", "overcharge", "float"}
DOCKED_STATES = {"docked", "float", "charging", "bulk", "overcharge"}

STATUS_DETAILS = {
    "offline": "The robot is not connected to the dashboard.",
    "needs_attention": "The robot needs attention before it can continue.",
    "charging": "The robot is docked and charging.",
    "docked": "The robot is on its charging dock.",
    "paused": "The robot's motors are switched off.",
    "navigating": "The robot is driving to its destination.",
    "recording": "The robot is recording a route.",
    "ready": "The robot is ready for a new task.",
}


def derive_status(
    *,
    connection: str,
    base_state: BaseStateData | None,
    diagnostics: DiagnosticsData | None,
    pose: PoseData | None,
    path: PathData | None,
) -> RobotStatusData:
    status = "ready"
    detail = STATUS_DETAILS["ready"]

    if connection == "offline":
        status = "offline"
    elif base_state is not None and (
        base_state.estop_pressed or base_state.fault_flags != 0 or not base_state.hardware_state_valid
    ):
        status = "needs_attention"
        if base_state.estop_pressed:
            detail = "The emergency stop is pressed. Release the red button on the robot to continue."
        elif not base_state.hardware_state_valid:
            detail = "The robot's motor controller stopped sending data."
        else:
            detail = "The robot reported a hardware fault."
    elif diagnostics is not None and any(item.level == "ERROR" for item in diagnostics.items):
        status = "needs_attention"
        worst = next(item for item in diagnostics.items if item.level == "ERROR")
        detail = f"A system reported a problem: {worst.message}"
    elif base_state is not None and base_state.charge_state.lower() in CHARGING_STATES:
        status = "charging"
    elif base_state is not None and base_state.charge_state.lower() in DOCKED_STATES:
        status = "docked"
    elif base_state is not None and not base_state.motors_enabled:
        status = "paused"
    elif path is not None and path.goal is not None and pose is not None and abs(pose.linear_velocity) > 0.02:
        status = "navigating"

    if status in STATUS_DETAILS and status not in {"needs_attention"}:
        detail = STATUS_DETAILS[status]
    return RobotStatusData(status=status, detail=detail)


def _level_rank(level: str) -> int:
    return {"healthy": 0, "warning": 1, "fault": 2, "offline": 3}[level]


def derive_health(
    *,
    connection: str,
    base_state: BaseStateData | None,
    battery: BatteryData | None,
    diagnostics: DiagnosticsData | None,
    pose: PoseData | None,
    resources: ResourcesData | None,
    lidar_age_s: float | None,
    battery_low_percent: float = 20.0,
    battery_critical_percent: float = 10.0,
) -> SystemHealthData:
    now = utc_now()
    subsystems: list[Subsystem] = []

    def add(id_: str, label: str, level: str, message: str, action: str | None = None) -> None:
        subsystems.append(Subsystem(id=id_, label=label, level=level, message=message, action=action, updated_at=now))

    # Connection to the robot
    if connection == "online":
        add("connection", "Connection", "healthy", "The robot is sending live data.")
    elif connection == "stale":
        add("connection", "Connection", "warning", "Data from the robot is arriving slowly.",
            "Check the robot's Wi-Fi if this continues.")
    else:
        add("connection", "Connection", "offline", "The robot is not connected.",
            "Check that the robot is powered on and connected to the network.")

    # Drive base (SBC link) — derived from data freshness reported by the
    # robot itself; the dashboard never opens its own SBC connections.
    if base_state is None or connection == "offline":
        add("drive_base", "Drive base", "offline", "No data from the robot's motor controller.")
    elif not base_state.link_connected or not base_state.hardware_state_valid:
        add("drive_base", "Drive base", "fault", "The robot's motor controller stopped responding.",
            "The robot cannot move. Contact technical support if this persists.")
    elif base_state.estop_pressed:
        add("drive_base", "Drive base", "fault", "The emergency stop is pressed.",
            "Release the red emergency-stop button on the robot.")
    elif base_state.fault_flags != 0:
        add("drive_base", "Drive base", "fault", "The robot reported a hardware fault.")
    elif not base_state.motors_enabled:
        add("drive_base", "Drive base", "warning", "The robot's motors are switched off.")
    else:
        add("drive_base", "Drive base", "healthy", "Motor controller responding normally.")

    # Battery
    if battery is None or connection == "offline":
        add("battery", "Battery", "offline", "No battery data.")
    elif battery.charging:
        add("battery", "Battery", "healthy", "The battery is charging.")
    elif battery.percentage is not None and battery.percentage <= battery_critical_percent:
        add("battery", "Battery", "fault", f"Battery is critically low ({battery.percentage:.0f}%).",
            "Return the robot to its charging dock now.")
    elif battery.percentage is not None and battery.percentage <= battery_low_percent:
        add("battery", "Battery", "warning", f"Battery is getting low ({battery.percentage:.0f}%).",
            "Plan to return the robot to its charging dock soon.")
    else:
        add("battery", "Battery", "healthy", "Battery level is OK.")

    # Sensors (laser)
    if connection == "offline" or lidar_age_s is None:
        add("sensors", "Laser sensor", "offline", "No laser data received.")
    elif lidar_age_s > 5.0:
        add("sensors", "Laser sensor", "warning", "The laser sensor stopped updating.",
            "Obstacle detection may be degraded.")
    else:
        add("sensors", "Laser sensor", "healthy", "Laser sensor updating normally.")

    # Localization
    if connection == "offline" or pose is None:
        add("localization", "Robot location", "offline", "The robot's position is unknown.")
    elif not pose.localized:
        add("localization", "Robot location", "warning", "The robot is not confident about its current location.",
            "Use “Set Robot Location” before sending a new destination.")
    else:
        add("localization", "Robot location", "healthy", "The robot knows where it is.")

    # Other diagnostics roll-up
    if diagnostics is not None:
        errors = [i for i in diagnostics.items if i.level == "ERROR"]
        warns = [i for i in diagnostics.items if i.level in ("WARN", "STALE")]
        if errors:
            add("systems", "Robot systems", "fault", f"{errors[0].name}: {errors[0].message}")
        elif warns:
            add("systems", "Robot systems", "warning", f"{warns[0].name}: {warns[0].message}")
        else:
            add("systems", "Robot systems", "healthy", "All monitored systems report OK.")
    else:
        add("systems", "Robot systems", "offline" if connection == "offline" else "healthy",
            "No system reports yet." if connection != "offline" else "No system reports.")

    # Robot computer (Raspberry Pi)
    if resources is None or connection == "offline":
        add("computer", "Robot computer", "offline", "No data from the robot's onboard computer.")
    elif resources.cpu_temp_c is not None and resources.cpu_temp_c >= 80.0:
        add("computer", "Robot computer", "warning", f"The onboard computer is hot ({resources.cpu_temp_c:.0f} °C).")
    elif resources.disk_percent >= 90.0:
        add("computer", "Robot computer", "warning", "The onboard computer is running out of storage.")
    else:
        add("computer", "Robot computer", "healthy", "Onboard computer running normally.")

    # Overall roll-up. "offline" as an overall state means the robot itself is
    # unreachable; a single subsystem with missing data while the robot is
    # online only warrants a warning.
    if connection == "offline":
        return SystemHealthData(overall="offline", subsystems=subsystems)
    overall = "healthy"
    for sub in subsystems:
        level = "warning" if sub.level == "offline" else sub.level
        if _level_rank(level) > _level_rank(overall):
            overall = level
    return SystemHealthData(overall=overall, subsystems=subsystems)
