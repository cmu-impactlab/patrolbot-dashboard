"""Typed payloads for every protocol message — the authoritative schema.

Mirrored by hand in frontend/src/types/protocol.ts; golden examples live in
shared/schemas/fixtures/ and are validated by both test suites.
"""
from __future__ import annotations

import json
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

DiagLevel = Literal["OK", "WARN", "ERROR", "STALE"]
ConnectionState = Literal["online", "stale", "offline"]
RobotStatus = Literal[
    "ready", "navigating", "recording", "docked", "charging", "paused",
    "stuck", "needs_attention", "offline"
]
HealthLevel = Literal["healthy", "warning", "fault", "offline"]
EventSeverity = Literal["info", "warning", "critical"]

# --- Measured-motion envelope -------------------------------------------------
# Motion telemetry carries measured values, not commands, so these are generous
# physical sanity guards (far above the commissioned ±0.30 m/s / ±0.70 rad/s)
# that only reject corrupt or absurd data — never a real reading.
MOTION_MAX_ABS_LINEAR_M_S = 5.0
MOTION_MAX_ABS_ANGULAR_RAD_S = 10.0
MOTION_MAX_AGE_S = 86_400.0
MOTION_MAX_GENERATION = 2**31 - 1
MOTION_MAX_EPOCH_LENGTH = 128

# --- Tuning transport envelope ------------------------------------------------
# The dashboard-server -> robot `tuning.request` wrapper and the robot's
# `tuning.response`. The inner canonical tuning request/snapshot stay opaque
# bounded JSON here; the robot's core service validates their meaning.
TUNING_REQUEST_MAX_BYTES = 20 * 1024
TUNING_RESPONSE_MAX_BYTES = 256 * 1024
TUNING_DETAIL_MAX_LENGTH = 1024


# ---- Robot -> server ------------------------------------------------------

class HelloData(BaseModel):
    protocol_version: int
    capabilities: list[str]
    map_version: int
    software_version: str


class HeartbeatData(BaseModel):
    uptime_s: float


class MapContext(BaseModel):
    map_id: str | None = None
    map_revision: str | None = None


class PoseData(MapContext):
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
    # AMCL xx, yy (m²), yaw variance (rad²). Null entries retain invalidity
    # without putting NaN/Inf in JSON or rounding a small variance to zero.
    planar_covariance: tuple[float | None, float | None, float | None] | None = None
    covariance_limit: float | None = None
    localization_age_s: float | None = None
    localization_max_age_s: float | None = None
    # Absent means "the robot did not say", which the motion gates must read as
    # "not localized" — every real producer sends it explicitly.
    localized: bool = False


class LidarData(MapContext):
    angle_min: float
    angle_increment: float
    ranges: list[float | None]


class GoalData(MapContext):
    x: float
    y: float
    yaw: float | None = None


class PathData(MapContext):
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


class BaseStateData(MapContext):
    lidar_state: Literal["UNKNOWN", "OFF", "STARTING", "READY", "FAULT"] = "UNKNOWN"
    lidar_power_supported: bool = False
    lidar_scan_age: float = Field(default=-1.0, ge=-1.0, allow_inf_nan=False)
    map_activated_stamp_ns: int = Field(default=0, ge=0)
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
    # Missing epoch fields remain parseable for legacy recordings but are
    # unsafe for navigation and localization health.
    odom_epoch_valid: bool = False
    # Raw odometry receipt age at the bridge, independent of map/localization.
    # Missing evidence is never authorization for web manual drive.
    raw_odom_age_s: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    localization_recovery_required: bool = True
    localization_recovery_stage: str = ""
    localization_seed_stamp_ns: int = Field(default=0, ge=0)


class MotionData(BaseModel):
    """Measured drive-base twist from raw ``/odom``, independent of AMCL/map.

    This is the gauge's *measured* speed and deliberately does not ride inside
    ``telemetry.pose``: that slice is only sent once an active map plus AMCL
    context exist, which is exactly the unlocalized case manual drive is allowed
    in. The bridge sends this slice whenever fresh, finite raw odometry exists,
    so a robot driving without a localization fix still reports what its wheels
    are doing.

    Every value is finite and strictly bounded. Negative or non-finite ages are
    invalid (a negative sample age means the stamp is in the future). Generation
    binds the sample to a drive-base session and odometry epoch: ``None`` of
    these is ever invented, because a fabricated zero would read as a real stop.
    A sample the bridge cannot trust is not sent at all; nothing here is a
    command or a claim of physical motion.
    """
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    linear_velocity: float = Field(ge=-MOTION_MAX_ABS_LINEAR_M_S, le=MOTION_MAX_ABS_LINEAR_M_S)
    angular_velocity: float = Field(ge=-MOTION_MAX_ABS_ANGULAR_RAD_S, le=MOTION_MAX_ABS_ANGULAR_RAD_S)
    source: Literal["raw_odom"]
    # Seconds between the odom stamp and the bridge's clock, and between the
    # bridge receiving the message and forwarding this slice.
    sample_age_s: float = Field(ge=0.0, le=MOTION_MAX_AGE_S)
    receipt_age_s: float = Field(ge=0.0, le=MOTION_MAX_AGE_S)
    # Drive-base session generation the sample is bound to.
    session_generation: StrictInt = Field(ge=0, le=MOTION_MAX_GENERATION)
    # Odom-origin epoch (opaque) and its generation. Empty/0 mean the origin
    # state was not yet known; the UI must not treat that as a valid origin.
    odom_epoch: str = Field(max_length=MOTION_MAX_EPOCH_LENGTH)
    odom_epoch_generation: StrictInt = Field(ge=0, le=MOTION_MAX_GENERATION)


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


class MapData(MapContext):
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
    # Dashboard deadman teleop. `teleop_start` claims the single-operator
    # lease and opens a session (audited like every command); `teleop.velocity`
    # frames flow only inside it; `teleop_stop` closes it. The F710 on
    # `input/joy` (mux priority 8) always outranks this channel (priority 7).
    "teleop_start", "teleop_stop",
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
    "charge_release", "motor_enable", "motor_disable", "undock", "software_reset", "set_lidar_power",
]
CommandOutcome = Literal["succeeded", "failed", "rejected", "canceled", "timeout"]


class CommandRequestData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: str  # UUID minted by the browser; correlates the whole lifecycle
    command: CommandType
    seed_profile: Literal["measured"] | None = None
    enabled: StrictBool | None = None

    @model_validator(mode="after")
    def require_lidar_target(self):
        if self.command in {"teleop_start", "teleop_stop"}:
            UUID(self.command_id)
            if self.goal is not None or self.enabled is not None or self.allow_unlocalized:
                raise ValueError("manual input commands do not accept goals, power targets or localization overrides")
        if self.seed_profile is not None and self.command != "set_initial_pose":
            raise ValueError("seed_profile is only valid for set_initial_pose")
        if self.command == "set_lidar_power" and self.enabled is None:
            raise ValueError("LiDAR power requires an explicit boolean enabled target")
        return self

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


class TeleopVelocityData(BaseModel):
    """One sample of the dashboard's on-screen deadman teleop.

    Browser → server → robot at ~10–15 Hz while the operator holds the
    deadman. The server forwards it only inside an open, lease-held teleop
    session; the Pi web bridge republishes it as a Twist on `/input/teleop`
    (twist_mux priority 7 — below the F710, above Nav2) and publishes one
    fail-closed zero within 300 ms of the last frame. This manual input joins
    the FINAL mux after Nav2's collision-monitor output, like the F710. It uses
    the final smoother, bridge/SBC hardware guards and priority-10 watchdog;
    collision-monitor coverage and physical obstacle stopping are not claimed.
    """
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid", strict=True)
    command_id: str  # the teleop_start UUID that opened the session
    # Monotonic within the session; drops replayed or reordered frames.
    seq: int = Field(ge=0)
    # Bounded to the robot's teleop_velocity_smoother ceiling, pinned in
    # contracts/web-teleop.json (both repos) — the dashboard can never ask
    # for more than the robot's final smoother is configured to pass.
    linear: float = Field(ge=-0.3, le=0.3)
    angular: float = Field(ge=-0.7, le=0.7)
    # True while the operator actively holds; a False frame is the explicit
    # release — zero velocities, zeroing the robot without waiting for the
    # watchdog.
    deadman: bool

    @model_validator(mode="after")
    def release_must_be_a_zero_command(self):
        UUID(self.command_id)
        if not self.deadman and (self.linear != 0.0 or self.angular != 0.0):
            raise ValueError("a released deadman frame must be a zero command")
        return self


class CommandCancelData(BaseModel):
    # Server-only cancellation of the original map transaction, never a new goal.
    command_id: str


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


# ---- Tuning transport (server <-> robot) ----------------------------------
# Bounded wrappers around the canonical, robot-owned tuning request/response.
# The actor is stamped server-side from the verified session; a browser can
# never assert a role. Reads are permitted any authenticated role, every
# mutation is administrator-only and enforced on the robot as well.

TuningRole = Literal["administrator", "operator", "observer"]
TuningOutcome = Literal["ok", "conflict", "refused", "failed", "unknown"]


def _require_bounded_json(data: Any, limit: int, what: str) -> None:
    """Reject non-finite/non-serializable or oversized opaque payloads."""
    try:
        encoded = json.dumps(data, allow_nan=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{what} must be finite JSON: {exc}") from exc
    if len(encoded.encode("utf-8")) > limit:
        raise ValueError(f"{what} exceeds {limit} bytes")


class TuningActorData(BaseModel):
    """Server-derived identity attached to a tuning request.

    Strict on purpose: the robot trusts this to distinguish an administrator
    mutation from an ordinary read, so a smuggled string/float/bool must fail
    here rather than be coerced.
    """
    model_config = ConfigDict(extra="forbid", strict=True)
    id: StrictInt = Field(ge=0, le=MOTION_MAX_GENERATION)
    username: str = Field(min_length=1, max_length=128)
    role: TuningRole


class TuningRequestData(BaseModel):
    """``tuning.request``: a canonical tuning request plus the server actor."""
    model_config = ConfigDict(extra="forbid")
    request: dict[str, Any]
    actor: TuningActorData
    authorization: dict[str, Any] | None = None

    @model_validator(mode="after")
    def bounded(self):
        if not self.request:
            raise ValueError("tuning request payload is required")
        _require_bounded_json(self.model_dump(mode="json"),
                              TUNING_REQUEST_MAX_BYTES, "tuning request")
        return self


class TuningResponseData(BaseModel):
    """``tuning.response``: the robot's bounded answer for one request_id."""
    model_config = ConfigDict(extra="forbid")
    request_id: str
    outcome: TuningOutcome
    detail: str = Field(default="", max_length=TUNING_DETAIL_MAX_LENGTH)
    # Opaque runtime snapshot / job objects; their meaning is the core schema's
    # and the robot validates it. Here they only have to be bounded finite JSON.
    snapshot: dict[str, Any] | None = None
    job: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_response(self):
        try:
            UUID(self.request_id)
        except (ValueError, AttributeError, TypeError):
            raise ValueError("request_id must be a UUID") from None
        _require_bounded_json(self.model_dump(mode="json"),
                              TUNING_RESPONSE_MAX_BYTES, "tuning response")
        return self


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
    # Measured raw-odom twist, served independently of pose/AMCL. Optional:
    # recordings and snapshots produced before this slice existed simply omit
    # it, and absence must read as "no measurement", never as zero speed.
    motion: MotionData | None = None
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
    "telemetry.motion": MotionData,
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
    "command.cancel": CommandCancelData,
    "teleop.velocity": TeleopVelocityData,
    "command.ack": CommandAckData,
    "command.progress": CommandProgressData,
    "command.result": CommandResultData,
    # Bounded tuning transport over the existing authenticated robot socket.
    # It never travels through the ordinary command broker or teleop lease.
    "tuning.request": TuningRequestData,
    "tuning.response": TuningResponseData,
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
