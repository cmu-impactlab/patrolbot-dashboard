"""Per-robot state store. Owned by the event loop — no locks.

Ported from the POC TelemetryStore, reshaped around typed protocol payloads
and asyncio single-loop ownership (the POC's RLock/deepcopy pattern dropped).
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from ..protocol.envelope import utc_now
from ..protocol.messages import (
    BaseStateData,
    BatteryData,
    ConnectionData,
    DiagnosticsData,
    EventData,
    LidarData,
    MapData,
    PathData,
    PoseData,
    ResourcesData,
    RobotStatusData,
    SnapshotData,
    SystemHealthData,
)
from ..settings import Settings
from .battery_estimator import BatteryEstimate, BatteryRuntimeEstimator
from .status import derive_health, derive_status


@dataclass
class Slice:
    data: Any = None
    received_mono: float | None = None

    def set(self, value: Any) -> None:
        self.data = value
        self.received_mono = time.monotonic()

    def age(self) -> float | None:
        if self.received_mono is None:
            return None
        return max(0.0, time.monotonic() - self.received_mono)


@dataclass
class RobotState:
    settings: Settings
    robot_id: str

    pose: Slice = field(default_factory=Slice)
    lidar: Slice = field(default_factory=Slice)
    path: Slice = field(default_factory=Slice)
    battery: Slice = field(default_factory=Slice)
    base_state: Slice = field(default_factory=Slice)
    diagnostics: Slice = field(default_factory=Slice)
    resources: Slice = field(default_factory=Slice)
    map: Slice = field(default_factory=Slice)

    last_heartbeat_mono: float | None = None
    last_seen: str | None = None
    connection: str = "offline"
    battery_estimate: BatteryEstimate | None = None

    events: deque[EventData] = field(default_factory=lambda: deque(maxlen=500))
    _next_event_id: int = 1
    _diag_levels: dict[str, str] = field(default_factory=dict)
    _prev_estop: bool = False
    _prev_bumper: bool = False
    _prev_battery_low: bool = False

    status: RobotStatusData | None = None
    health: SystemHealthData | None = None

    def __post_init__(self) -> None:
        self.estimator = BatteryRuntimeEstimator(
            low_percent=self.settings.battery_low_percent,
            cutoff_voltage=self.settings.battery_cutoff_voltage,
        )

    # -- event helpers -------------------------------------------------------

    def seed_event_id(self, next_id: int) -> None:
        self._next_event_id = next_id

    def add_event(self, severity: str, title: str, message: str) -> EventData:
        event = EventData(id=self._next_event_id, ts=utc_now(), severity=severity, title=title, message=message)
        self._next_event_id += 1
        self.events.appendleft(event)
        return event

    # -- telemetry ingestion; each returns new events to broadcast -----------

    def record_heartbeat(self) -> None:
        self.last_heartbeat_mono = time.monotonic()
        self.last_seen = utc_now()

    def heartbeat_age(self) -> float | None:
        if self.last_heartbeat_mono is None:
            return None
        return max(0.0, time.monotonic() - self.last_heartbeat_mono)

    def record_pose(self, data: PoseData) -> list[EventData]:
        self.pose.set(data)
        return []

    def record_lidar(self, data: LidarData) -> list[EventData]:
        self.lidar.set(data)
        return []

    def record_path(self, data: PathData) -> list[EventData]:
        self.path.set(data)
        return []

    def record_battery(self, data: BatteryData) -> list[EventData]:
        events: list[EventData] = []
        previous: BatteryData | None = self.battery.data
        self.estimator.add_sample(
            time.monotonic(), voltage=data.voltage, percentage=data.percentage, charging=data.charging
        )
        self.battery_estimate = self.estimator.estimate(
            charging=data.charging, has_percentage=data.percentage is not None
        )
        self.battery.set(data)

        low = self.settings.battery_low_percent
        is_low = data.percentage is not None and data.percentage <= low and not data.charging
        if is_low and not self._prev_battery_low:
            events.append(self.add_event(
                "warning", "Battery is getting low",
                f"Battery is at {data.percentage:.0f}%. The robot should return to its charging dock soon.",
            ))
        self._prev_battery_low = is_low
        if previous is not None and data.charging and not previous.charging:
            events.append(self.add_event("info", "Charging started", "The robot is charging."))
        if previous is not None and not data.charging and previous.charging:
            events.append(self.add_event("info", "Charging stopped", "The robot left its charger."))
        return events

    def record_base_state(self, data: BaseStateData) -> list[EventData]:
        events: list[EventData] = []
        if data.estop_pressed and not self._prev_estop:
            events.append(self.add_event(
                "critical", "Emergency stop pressed",
                "The robot's emergency stop was pressed. The robot cannot move until it is released.",
            ))
        if not data.estop_pressed and self._prev_estop:
            events.append(self.add_event("info", "Emergency stop released", "The robot can move again."))
        self._prev_estop = data.estop_pressed

        bumper = data.bumpers_front or data.bumpers_rear
        if bumper and not self._prev_bumper:
            where = "front" if data.bumpers_front else "rear"
            events.append(self.add_event(
                "warning", "Bumper pressed",
                f"The robot's {where} bumper touched something. Check that its path is clear.",
            ))
        self._prev_bumper = bumper
        self.base_state.set(data)
        return events

    def record_diagnostics(self, data: DiagnosticsData) -> list[EventData]:
        events: list[EventData] = []
        for item in data.items:
            previous = self._diag_levels.get(item.name)
            if item.level in ("WARN", "ERROR", "STALE") and previous != item.level:
                severity = "critical" if item.level == "ERROR" else "warning"
                events.append(self.add_event(severity, "A system needs attention", f"{item.name}: {item.message}"))
            elif item.level == "OK" and previous in ("WARN", "ERROR", "STALE"):
                events.append(self.add_event("info", "System recovered", f"{item.name} is OK again."))
            self._diag_levels[item.name] = item.level
        self.diagnostics.set(data)
        return events

    def record_resources(self, data: ResourcesData) -> list[EventData]:
        self.resources.set(data)
        return []

    def record_map(self, data: MapData) -> list[EventData]:
        self.map.set(data)
        return [self.add_event("info", "Map updated", f"The robot is now using map “{data.name}”.")]

    # -- derived state --------------------------------------------------------

    def set_connection(self, state: str) -> list[EventData]:
        events: list[EventData] = []
        if state != self.connection:
            if state == "offline":
                events.append(self.add_event(
                    "critical", "Robot disconnected",
                    "The dashboard stopped receiving data from the robot.",
                ))
            elif state == "online" and self.connection in ("offline", "stale"):
                events.append(self.add_event("info", "Robot connected", "The robot is sending live data."))
            self.connection = state
        return events

    def connection_data(self) -> ConnectionData:
        return ConnectionData(state=self.connection, last_seen=self.last_seen)

    def recompute(self) -> tuple[RobotStatusData | None, SystemHealthData | None]:
        """Returns (status, health) — each None when unchanged since last call."""
        status = derive_status(
            connection=self.connection,
            base_state=self.base_state.data,
            diagnostics=self.diagnostics.data,
            pose=self.pose.data,
            path=self.path.data,
        )
        health = derive_health(
            connection=self.connection,
            base_state=self.base_state.data,
            battery=self.battery.data,
            diagnostics=self.diagnostics.data,
            pose=self.pose.data,
            resources=self.resources.data,
            lidar_age_s=self.lidar.age(),
            battery_low_percent=self.settings.battery_low_percent,
            battery_critical_percent=self.settings.battery_critical_percent,
        )
        status_changed = self.status is None or status != self.status
        health_changed = self.health is None or health.overall != self.health.overall or [
            (s.id, s.level, s.message) for s in health.subsystems
        ] != [(s.id, s.level, s.message) for s in self.health.subsystems]
        self.status = status
        self.health = health
        return (status if status_changed else None, health if health_changed else None)

    def snapshot(self) -> SnapshotData:
        status = self.status or derive_status(
            connection=self.connection, base_state=None, diagnostics=None, pose=None, path=None
        )
        health = self.health
        if health is None:
            self.recompute()
            health = self.health
            status = self.status
        estimate_model = None
        if self.battery_estimate is not None:
            from ..protocol.messages import BatteryEstimate as EstimateModel

            estimate_model = EstimateModel(**self.battery_estimate.as_dict())
        battery: BatteryData | None = self.battery.data
        if battery is not None and estimate_model is not None:
            battery = battery.model_copy(update={"estimate": estimate_model})
        map_data: MapData | None = self.map.data
        return SnapshotData(
            connection=self.connection_data(),
            robot_status=status,
            system_health=health,
            map_version=map_data.map_version if map_data is not None else 0,
            pose=self.pose.data,
            battery=battery,
            battery_estimate=estimate_model,
            base_state=self.base_state.data,
            diagnostics=self.diagnostics.data,
            resources=self.resources.data,
            path=self.path.data,
            events=list(self.events)[:100],
        )
