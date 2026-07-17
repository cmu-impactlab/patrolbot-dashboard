"""Typed payloads for every protocol message — the authoritative schema.

Mirrored by hand in frontend/src/types/protocol.ts; golden examples live in
shared/schemas/fixtures/ and are validated by both test suites.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DiagLevel = Literal["OK", "WARN", "ERROR", "STALE"]
ConnectionState = Literal["online", "stale", "offline"]
RobotStatus = Literal[
    "ready", "navigating", "recording", "docked", "charging", "paused", "needs_attention", "offline"
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
    frame_id: str = "map"
    x: float
    y: float
    yaw: float
    linear_velocity: float
    angular_velocity: float
    covariance_trace: float | None = None
    localized: bool = True


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
    voltage: float
    current: float | None = None
    percentage: float | None = None
    charging: bool
    # Added by the server before re-broadcast; robots never send it.
    estimate: BatteryEstimate | None = None


class BaseStateData(BaseModel):
    session_generation: int
    link_connected: bool
    telemetry_age: float
    hardware_state_valid: bool
    charge_state: str
    motors_enabled: bool
    estop_pressed: bool
    fault_flags: int
    stall_value: int
    bumpers_front: bool
    bumpers_rear: bool


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

CommandType = Literal["navigate_to_pose", "set_initial_pose", "stop"]
CommandOutcome = Literal["succeeded", "failed", "rejected", "canceled", "timeout"]


class CommandRequestData(BaseModel):
    command_id: str  # UUID minted by the browser; correlates the whole lifecycle
    command: CommandType
    goal: GoalData | None = None  # required for navigate_to_pose / set_initial_pose


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
    events: list[EventData] = Field(default_factory=list)


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
