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

# How old a slice may be before the health card stops speaking for it. These
# are deliberately looser than the motion gates in commands/gates.py: a gate
# refusing for three seconds is safe, a status card flickering red every time a
# frame is late is noise an operator learns to ignore. Publish rates are in
# shared/schemas/protocol.md — base_state, battery, diagnostics and resources
# nominally 1 Hz, pose 10 Hz — though the ones sourced from upstream ROS
# publishers arrive at whatever rate those publish.
MAX_BASE_STATE_AGE_S = 15.0
MAX_BATTERY_AGE_S = 30.0
MAX_DIAGNOSTICS_AGE_S = 30.0
MAX_POSE_AGE_S = 15.0
MAX_RESOURCES_AGE_S = 60.0
# The robot's *own* report of how stale its drive-base link is — a different
# question from how long ago we heard from the robot. Matches the motion gates'
# MAX_TELEMETRY_AGE_S, loosened for display the same way as the rest.
MAX_ROBOT_TELEMETRY_AGE_S = 15.0

CHARGING_STATES = {"charging", "bulk", "overcharge", "float"}
DOCKED_STATES = {"docked", "float", "charging", "bulk", "overcharge"}

STATUS_DETAILS = {
    "offline": "The robot is not connected to the dashboard.",
    "needs_attention": "The robot needs attention before it can continue.",
    "charging": "The robot is docked and charging.",
    "docked": "The robot is on its charging dock.",
    "paused": "The robot's motors are switched off.",
    "stuck": "The robot stopped on its way to the destination and may be stuck.",
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
    recording: bool = False,
    stalled: bool = False,
    base_state_age_s: float | None = None,
    diagnostics_age_s: float | None = None,
    pose_age_s: float | None = None,
) -> RobotStatusData:
    """The one-line answer at the top of the dashboard.

    `base_state_age_s` is the server's receipt age for the drive-base slice.
    Without it this reported "charging", "docked", "paused" or "ready" from
    whatever the robot last said, indefinitely — and a robot that had connected
    but never sent any telemetry read "ready for a new task".
    """
    status = "ready"
    detail = STATUS_DETAILS["ready"]
    custom_detail = False
    on_dock = False
    if base_state is not None:
        if base_state.dock_state_valid is not None:
            on_dock = (
                base_state.dock_state_valid
                and (base_state.dock_state or "").strip().upper()
                == "DOCKED_CONFIRMED"
            )
        else:
            on_dock = base_state.charge_state.lower() in DOCKED_STATES

    base_stale = base_state is None or stale_age(base_state_age_s, MAX_BASE_STATE_AGE_S)
    # A hazard the robot reported before it went quiet is still a hazard. It is
    # reported first and then qualified as last-known, rather than replaced by
    # "stopped reporting" — losing a pressed e-stop because the robot fell
    # silent afterwards would be the same class of untruth this all guards
    # against, pointing the other way.
    if connection == "offline":
        status = "offline"
    elif base_state is not None and (
        base_state.estop_pressed or base_state.fault_flags != 0
        or not base_state.hardware_state_valid or not base_state.link_connected
        or base_state.telemetry_age > MAX_ROBOT_TELEMETRY_AGE_S
    ):
        # Same predicate the health card calls a drive-base fault. It used to be
        # narrower, so a robot whose link to its own drive base was down read
        # "ready" at the top of the screen while the health card showed a fault.
        status = "needs_attention"
        if base_state.estop_pressed:
            detail = ("The emergency stop is pressed. Release the red button on "
                      "the robot to continue.")
        elif not base_state.hardware_state_valid or not base_state.link_connected:
            detail = "The robot's motor controller stopped sending data."
        elif base_state.fault_flags != 0:
            detail = "The robot reported a hardware fault."
        else:
            detail = "The robot's readings from its motor controller are out of date."
        if base_stale:
            # Lead with the qualifier. "The emergency stop is pressed" reads as
            # a live fact, and it is not one any more.
            detail = ("The robot has stopped reporting. When it last reported: "
                      + detail[0].lower() + detail[1:])
        custom_detail = True
    elif base_stale:
        # Connected to the Pi, but nothing current from the robot itself, and
        # nothing alarming in what it last said.
        status = "needs_attention"
        detail = ("The robot's onboard computer is connected, but the robot "
                  "has stopped reporting its own state.")
        custom_detail = True
    elif (diagnostics is not None
          and not stale_age(diagnostics_age_s, MAX_DIAGNOSTICS_AGE_S)
          and any(item.level == "ERROR" for item in diagnostics.items)):
        status = "needs_attention"
        worst = next(item for item in diagnostics.items if item.level == "ERROR")
        detail = f"A system reported a problem: {worst.message}"
    elif base_state is not None and base_state.undock_active:
        status = "navigating"
        detail = "The robot is moving clear of its charging dock."
        custom_detail = True
    elif (base_state is not None and on_dock
          and base_state.charge_state.lower() in CHARGING_STATES):
        status = "charging"
    elif base_state is not None and on_dock:
        status = "docked"
    elif base_state is not None and not base_state.motors_enabled:
        status = "paused"
    elif stalled:
        status = "stuck"
    elif recording:
        status = "recording"
    elif (path is not None and path.goal is not None and pose is not None
          and not stale_age(pose_age_s, MAX_POSE_AGE_S)
          and abs(pose.linear_velocity) > 0.02):
        # A stale pose showing movement is not a robot that is still moving.
        status = "navigating"

    if status in STATUS_DETAILS and status != "needs_attention" and not custom_detail:
        detail = STATUS_DETAILS[status]
    return RobotStatusData(status=status, detail=detail)


def stale_age(age_s: float | None, limit_s: float) -> bool:
    """No data, or data too old to describe the robot now. None — never
    received — reads exactly like a stale one."""
    return age_s is None or age_s > limit_s


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
    base_state_age_s: float | None = None,
    battery_age_s: float | None = None,
    diagnostics_age_s: float | None = None,
    pose_age_s: float | None = None,
    resources_age_s: float | None = None,
    battery_low_percent: float = 20.0,
    battery_critical_percent: float = 10.0,
) -> SystemHealthData:
    """Per-subsystem health from the latest telemetry *and how old it is*.

    Every `*_age_s` is the server's own receipt age for that slice. They are
    not optional refinements: a subsystem judged on value alone reports the
    last thing it ever heard, forever. With the drive-base controller switched
    off, the Raspberry Pi keeps its socket and its heartbeat alive, so the
    connection stays "online" while battery, laser, pose and drive-base data
    all stop — and this function used to answer "Battery level is OK" and "All
    monitored systems report OK" from values days out of date.

    None means "never received", which reads exactly like a stale one.
    """
    now = utc_now()
    subsystems: list[Subsystem] = []

    stale = stale_age

    def add(id_: str, label: str, level: str, message: str, action: str | None = None) -> None:
        subsystems.append(Subsystem(id=id_, label=label, level=level, message=message, action=action, updated_at=now))

    # Link to the robot's onboard computer. This is the bridge's heartbeat, and
    # the bridge runs on the Raspberry Pi — it keeps beating whether or not the
    # Pi can reach the drive-base controller. Saying "the robot is sending live
    # data" here claimed far more than this signal supports; the drive-base
    # subsystem below is what answers "can the Pi actually reach the robot".
    if connection == "online":
        add("connection", "Robot computer link", "healthy",
            "The dashboard is connected to the robot's onboard computer.")
    elif connection == "stale":
        add("connection", "Robot computer link", "warning",
            "Data from the robot's onboard computer is arriving slowly.",
            "Check the robot's Wi-Fi if this continues.")
    else:
        add("connection", "Robot computer link", "offline",
            "The dashboard is not connected to the robot.",
            "Check that the robot is powered on and connected to the network.")

    # Drive base (SBC link) — derived from data freshness reported by the
    # robot itself; the dashboard never opens its own SBC connections.
    if base_state is None or connection == "offline":
        add("drive_base", "Drive base", "offline", "No data from the robot's motor controller.")
    elif stale(base_state_age_s, MAX_BASE_STATE_AGE_S):
        # Deliberately not "the robot cannot move": losing the telemetry proves
        # the dashboard cannot see the robot, not that the robot is stopped.
        # Someone deciding whether it is safe to walk up to it needs that
        # distinction.
        #
        # A hazard in the last frame keeps its severity. Dropping a reported
        # e-stop or fault to "offline" because the robot then fell silent would
        # quietly downgrade a real hazard to grey.
        hazard = (base_state.estop_pressed or base_state.fault_flags != 0
                  or not base_state.link_connected
                  or not base_state.hardware_state_valid)
        add("drive_base", "Drive base", "fault" if hazard else "offline",
            "The robot last reported a problem with its motor controller and "
            "has since stopped reporting." if hazard else
            "The robot has stopped reporting its motor controller.",
            "The dashboard cannot tell whether the robot is moving. "
            "Check the robot before approaching it.")
    elif not base_state.link_connected or not base_state.hardware_state_valid:
        add("drive_base", "Drive base", "fault", "The robot's motor controller stopped responding.",
            "The dashboard cannot tell whether the robot is moving. "
            "Contact technical support if this persists.")
    elif base_state.telemetry_age > MAX_ROBOT_TELEMETRY_AGE_S:
        # Two separate ages: how long ago we heard from the robot, and how long
        # ago the robot heard from its own drive base. The motion gates already
        # require both; the health card was judging only the first.
        add("drive_base", "Drive base", "fault",
            "The robot's own readings from its motor controller are out of date.",
            "The dashboard cannot tell whether the robot is moving. "
            "Check the robot before approaching it.")
    elif base_state.estop_pressed:
        add("drive_base", "Drive base", "fault", "The emergency stop is pressed.",
            "Release the red emergency-stop button on the robot.")
    elif base_state.fault_flags != 0:
        add("drive_base", "Drive base", "fault", "The robot reported a hardware fault.")
    elif base_state.bumpers_valid is False:
        # Only an explicit False. A robot that never mentions its bumpers is not
        # asserting anything about them, and neither is this line — where the
        # undock gate is deliberately stricter, because that one authorises
        # motion rather than describing what the robot said.
        add("drive_base", "Drive base", "warning",
            "The robot cannot read its bumpers.",
            "It cannot tell whether anything is touching it. Check around the "
            "robot before moving it.")
    elif not base_state.motors_enabled:
        add("drive_base", "Drive base", "warning", "The robot's motors are switched off.")
    else:
        add("drive_base", "Drive base", "healthy", "Motor controller responding normally.")

    # Battery
    if battery is None or connection == "offline":
        add("battery", "Battery", "offline", "No battery data.")
    elif stale(battery_age_s, MAX_BATTERY_AGE_S):
        add("battery", "Battery", "offline", "The robot has stopped reporting its battery.",
            "The last reading is too old to rely on.")
    elif battery.charging:
        add("battery", "Battery", "healthy", "The battery is charging.")
    elif battery.percentage is not None and battery.percentage <= battery_critical_percent:
        add("battery", "Battery", "fault", f"Battery is critically low ({battery.percentage:.0f}%).",
            "Return the robot to its charging dock now.")
    elif battery.percentage is not None and battery.percentage <= battery_low_percent:
        add("battery", "Battery", "warning", f"Battery is getting low ({battery.percentage:.0f}%).",
            "Plan to return the robot to its charging dock soon.")
    elif battery.percentage is None:
        # A fresh frame that carries no level. The bridge sends None when the
        # robot's own percentage is not finite, and "OK" was the answer for a
        # battery whose charge nobody knew.
        add("battery", "Battery", "warning", "The robot is not reporting a battery level.",
            "Check the robot's battery before sending it on a long task.")
    else:
        add("battery", "Battery", "healthy", f"Battery level is OK ({battery.percentage:.0f}%).")

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
    elif stale(pose_age_s, MAX_POSE_AGE_S):
        # "Knows where it is" is a claim about now. A pose slice that stopped
        # arriving describes where the robot was when it last reported, and the
        # robot's own confidence in it travels with the payload — so a stale
        # frame keeps asserting that confidence long after it stopped being
        # measured.
        add("localization", "Robot location", "offline",
            "The robot has stopped reporting its position.",
            "Its last known position is too old to rely on.")
    elif not pose.localized:
        add("localization", "Robot location", "warning", "The robot is not confident about its current location.",
            "Use “Set Robot Location” before sending a new destination.")
    else:
        add("localization", "Robot location", "healthy", "The robot knows where it is.")

    # Other diagnostics roll-up. Never having heard from a subsystem is not the
    # same as that subsystem being well, and this used to report the two
    # identically: a robot that had never sent a diagnostic in its life read
    # "healthy". The wording also no longer claims coverage it does not have —
    # what is green is the systems that reported, not every system there is.
    if diagnostics is None or stale(diagnostics_age_s, MAX_DIAGNOSTICS_AGE_S):
        add("systems", "Robot systems", "offline",
            "No current reports from the robot's systems.")
    else:
        errors = [i for i in diagnostics.items if i.level == "ERROR"]
        warns = [i for i in diagnostics.items if i.level in ("WARN", "STALE")]
        if errors:
            add("systems", "Robot systems", "fault", f"{errors[0].name}: {errors[0].message}")
        elif warns:
            add("systems", "Robot systems", "warning", f"{warns[0].name}: {warns[0].message}")
        elif not diagnostics.items:
            add("systems", "Robot systems", "offline",
                "No current reports from the robot's systems.")
        else:
            count = len(diagnostics.items)
            add("systems", "Robot systems", "healthy",
                f"{count} reporting system{'' if count == 1 else 's'} "
                f"{'reports' if count == 1 else 'report'} OK.")

    # Robot computer (Raspberry Pi)
    if resources is None or connection == "offline":
        add("computer", "Robot computer", "offline", "No data from the robot's onboard computer.")
    elif stale(resources_age_s, MAX_RESOURCES_AGE_S):
        add("computer", "Robot computer", "offline",
            "The robot's onboard computer has stopped reporting itself.")
    elif resources.cpu_temp_c is not None and resources.cpu_temp_c >= 80.0:
        add("computer", "Robot computer", "warning", f"The onboard computer is hot ({resources.cpu_temp_c:.0f} °C).")
    elif resources.disk_percent >= 90.0:
        add("computer", "Robot computer", "warning", "The onboard computer is running out of storage.")
    else:
        # Names what was actually checked. "Running normally" implied a health
        # check of the whole machine; and when the robot reports no temperature
        # at all, claiming the temperature is OK is the same overclaim again.
        add("computer", "Robot computer", "healthy",
            "Onboard computer temperature and storage are OK."
            if resources.cpu_temp_c is not None else
            "Onboard computer storage is OK; it reports no temperature.")

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
