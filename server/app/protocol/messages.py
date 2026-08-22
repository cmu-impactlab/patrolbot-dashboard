"""Typed payloads for every protocol message — the authoritative schema.

Mirrored by hand in frontend/src/types/protocol.ts; golden examples live in
shared/schemas/fixtures/ and are validated by both test suites.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DiagLevel = Literal["OK", "WARN", "ERROR", "STALE"]
ConnectionState = Literal["online", "stale", "offline"]
RobotStatus = Literal[
    "ready", "navigating", "recording", "docked", "charging", "paused",
    "stuck", "needs_attention", "offline"
]
HealthLevel = Literal["healthy", "warning", "fault", "offline"]
EventSeverity = Literal["info", "warning", "critical"]


# ---- Robot -> server ------------------------------------------------------

class HelloData(BaseModel):
    protocol_version: int
    capabilities: list[str]
    map_version: int
    software_version: str


class HeartbeatData(BaseModel):
    uptime_s: float


class PoseData(BaseModel):
    # Reject NaN/Inf outright: a non-finite pose would poison the map render
    # and any downstream geometry.
    model_config = ConfigDict(allow_inf_nan=False)
    frame_id: str = "map"
    x: float
    y: float
    yaw: float
    linear_velocity: float
    angular_velocity: float
    covariance_trace: float | None = None
    # Absent means "the robot did not say", which the motion gates must read as
    # "not localized" — every real producer sends it explicitly.
    localized: bool = False


class LidarData(BaseModel):
    angle_min: float
    angle_increment: float
    ranges: list[float | None]


class GoalData(BaseModel):
    x: float
    y: float
    yaw: float | None = None


class PathData(BaseModel):
    frame_id: str = "map"
    points: list[tuple[float, float]]
    goal: GoalData | None = None


class BatteryEstimate(BaseModel):
    state: str
    minutes_remaining: int | None
    confidence: str


class BatteryData(BaseModel):
    # Same reasoning as PoseData, plus one the browser forces: Python happily
    # encodes NaN/Infinity into JSON, but they are not JSON and JSON.parse
    # throws on them — a single NaN voltage would take down the whole
    # dashboard socket, not just the battery widget. Non-finite voltage is
    # rejected here (the gateway drops the frame); the bridge already maps
    # non-finite current/percentage to null before they get this far.
    model_config = ConfigDict(allow_inf_nan=False)
    voltage: float
    current: float | None = None
    percentage: float | None = None
    charging: bool
    # Added by the server before re-broadcast; robots never send it.
    estimate: BatteryEstimate | None = None


class BaseStateData(BaseModel):
    session_generation: int
    link_connected: bool
    # Seconds; a negative age is not a fresher reading, it is a broken clock.
    telemetry_age: float = Field(ge=0.0)
    hardware_state_valid: bool
    charge_state: str
    motors_enabled: bool
    estop_pressed: bool
    fault_flags: int
    stall_value: int
    bumpers_front: bool
    bumpers_rear: bool
    # Whether the two above mean anything. Optional, because recordings and
    # robots from before the flag exists do not send it — but absence is not a
    # claim that the readings are good: the UI shows Unknown and the undock
    # gate refuses without an explicit True.
    bumpers_valid: bool | None = None
    # The SBC dock observer is the source of truth for physical clearance.
    # These remain optional so a dashboard can still read recordings and
    # fixtures produced before that observer was commissioned.
    dock_state: str | None = None
    dock_state_valid: bool | None = None
    dock_phase: int | None = None
    dock_phase_name: str | None = None
    undock_active: bool = False
    undock_release_attempts: int | None = None
    minimum_rear_range: float | None = None
    rear_sonar_usable: bool | None = None
    redock_inhibited: bool | None = None
    redock_inhibit_remaining: float | None = None
    undock_profile_commissioned: bool | None = None


class DiagnosticItem(BaseModel):
    name: str
    level: DiagLevel
    message: str
    values: dict[str, str] | None = None


class DiagnosticsData(BaseModel):
    items: list[DiagnosticItem]


class ResourcesData(BaseModel):
    cpu_percent: float
    memory_percent: float
    cpu_temp_c: float | None = None
    disk_percent: float
    wifi_signal_dbm: int | None = None


class MapOrigin(BaseModel):
    x: float
    y: float
    yaw: float


class MapData(BaseModel):
    map_version: int
    name: str
    resolution: float
    width: int
    height: int
    origin: MapOrigin
    rle: list[tuple[int, int]]


# ---- Commands (browser -> server -> robot, responses flow back) -----------

CommandType = Literal[
    "navigate_to_pose", "set_initial_pose", "stop",
    # Charging / motor power / undock. Deliberately distinct commands, not one
    # "unlock wheels": charge release is zero-motion and leaves the motors off,
    # enabling the motors is a separate explicit request, and undock is the
    # guarded motion operation on top of both.
    #
    # There is no `dock`. Automatic dock-in has never existed on the robot —
    # its ROS graph offers /patrolbot/undock and /patrolbot/hardware_undock and
    # nothing to drive back onto the charger (verified 2026-08-08). Advertising
    # the command anyway meant the dashboard offered a control the real
    # executor would reject as unknown, and only the mock made it look
    # implemented. Reinstate it when a real dock-in path is commissioned.
    "charge_release", "motor_enable", "undock",
]
CommandOutcome = Literal["succeeded", "failed", "rejected", "canceled", "timeout"]


class CommandRequestData(BaseModel):
    command_id: str  # UUID minted by the browser; correlates the whole lifecycle
    command: CommandType
    goal: GoalData | None = None  # required for navigate_to_pose / set_initial_pose
    # Set only when the operator has explicitly confirmed taking control away
    # from whoever currently holds the single-operator lease.
    takeover: bool = False
    # Operator override for one command only, never persisted: send the goal
    # even though the robot reports it does not know where it is. RViz has
    # always been able to do this -- Nav2 has no localization gate of its own --
    # so this restores parity for the dashboard rather than granting anything
    # new. It is deliberately per-command so it cannot be left switched on.
    allow_unlocalized: bool = False
    # Stamped by the server from the verified session role before forwarding,
    # and ignored on the way in — a browser cannot authorize itself. The robot
    # requires it for guarded motion (the dock manager's Undock goal).
    operator_authorized: bool = False


class CommandAckData(BaseModel):
    command_id: str
    accepted: bool
    reason: str | None = None


class CommandProgressData(BaseModel):
    command_id: str
    stage: str
    detail: str | None = None
    distance_remaining: float | None = None


class CommandResultData(BaseModel):
    command_id: str
    outcome: CommandOutcome
    detail: str | None = None


# ---- Server -> robot ------------------------------------------------------

class HelloAckData(BaseModel):
    want_map: bool


# ---- Server -> browser ----------------------------------------------------

class ConnectionData(BaseModel):
    state: ConnectionState
    last_seen: str | None = None


class RobotStatusData(BaseModel):
    status: RobotStatus
    detail: str


class CapabilitiesData(BaseModel):
    """What the connected robot says it can do (from robot.hello).

    A control whose capability the robot has not claimed is shown disabled
    with the reason rather than silently dropped, so an operator can see it
    exists and why it is unavailable — Undock, when the robot is on its dock.
    This is presentation only: the server gates every command on its own.
    """
    capabilities: list[str] = Field(default_factory=list)


class Subsystem(BaseModel):
    id: str
    label: str
    level: HealthLevel
    message: str
    action: str | None = None
    updated_at: str


class SystemHealthData(BaseModel):
    overall: HealthLevel
    subsystems: list[Subsystem]


class EventData(BaseModel):
    id: int
    ts: str
    severity: EventSeverity
    title: str
    message: str


class SnapshotData(BaseModel):
    connection: ConnectionData
    robot_status: RobotStatusData
    system_health: SystemHealthData
    map_version: int
    pose: PoseData | None = None
    battery: BatteryData | None = None
    battery_estimate: BatteryEstimate | None = None
    base_state: BaseStateData | None = None
    diagnostics: DiagnosticsData | None = None
    resources: ResourcesData | None = None
    path: PathData | None = None
    last_known_pose: GoalData | None = None
    capabilities: list[str] = Field(default_factory=list)
    events: list[EventData] = Field(default_factory=list)
    # How long ago the server received each slice above, in seconds. A snapshot
    # is a catch-up for a browser that just connected, and the values in it can
    # be arbitrarily old — without these the browser would time them from its
    # own arrival and show days-old readings as current for the first 15
    # seconds. None means the slice was never received.
    slice_ages_s: dict[str, float | None] = Field(default_factory=dict)


TYPE_REGISTRY: dict[str, type[BaseModel]] = {
    "robot.hello": HelloData,
    "telemetry.heartbeat": HeartbeatData,
    "telemetry.pose": PoseData,
    "telemetry.lidar": LidarData,
    "telemetry.path": PathData,
    "telemetry.battery": BatteryData,
    "telemetry.base_state": BaseStateData,
    "telemetry.diagnostics": DiagnosticsData,
    "telemetry.resources": ResourcesData,
    "telemetry.map": MapData,
    "server.hello_ack": HelloAckData,
    "server.snapshot": SnapshotData,
    "state.connection": ConnectionData,
    "state.robot_status": RobotStatusData,
    "state.system_health": SystemHealthData,
    "state.capabilities": CapabilitiesData,
    "event.append": EventData,
    "command.request": CommandRequestData,
    "command.ack": CommandAckData,
    "command.progress": CommandProgressData,
    "command.result": CommandResultData,
}

COMMAND_PREFIX = "command."


# ---- Occupancy grid RLE ---------------------------------------------------

def encode_rle(cells: list[int]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    for value in cells:
        if runs and runs[-1][0] == value:
            runs[-1] = (value, runs[-1][1] + 1)
        else:
            runs.append((value, 1))
    return runs


def decode_rle(runs: list[tuple[int, int]] | list[list[int]]) -> list[int]:
    cells: list[int] = []
    for value, count in runs:
        cells.extend([value] * count)
    return cells
