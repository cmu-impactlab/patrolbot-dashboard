"""Simulated world: occupancy map, waypoint patrol kinematics, lidar ray-cast.

Map geometry ported from the custom-dashboard-demo simulator (build_map).
Pure deterministic math — unit-testable without any I/O.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

MAP_METERS = 18.0
MAP_ORIGIN = -9.0
RESOLUTION = 0.15
CRUISE_SPEED = 0.42  # m/s, matches the real PatrolBot's patrol speed
TURN_RATE = 0.9  # rad/s
LIDAR_MAX_RANGE = 8.0
LIDAR_RAYS = 181  # ±90° like the SICK LMS-200 half-scan the bridge publishes

WAYPOINTS: list[tuple[float, float]] = [
    (5.5, 0.0), (5.0, 5.0), (0.0, 6.5), (-5.0, 5.0),
    (-6.5, 0.0), (-5.0, -5.0), (0.0, -6.5), (5.0, -5.0),
]
DOCK = (-7.5, -7.0)


def build_cells(extra_wall: bool = False) -> list[int]:
    width = round(MAP_METERS / RESOLUTION)
    data = [0] * (width * width)

    def rectangle(x0: float, x1: float, y0: float, y1: float, value: int = 100) -> None:
        gy0 = max(0, int((y0 - MAP_ORIGIN) / RESOLUTION))
        gy1 = min(width, int((y1 - MAP_ORIGIN) / RESOLUTION) + 1)
        gx0 = max(0, int((x0 - MAP_ORIGIN) / RESOLUTION))
        gx1 = min(width, int((x1 - MAP_ORIGIN) / RESOLUTION) + 1)
        for gy in range(gy0, gy1):
            for gx in range(gx0, gx1):
                data[gy * width + gx] = value

    border = max(2, round(0.3 / RESOLUTION))
    for gy in range(width):
        for gx in range(width):
            if gx < border or gy < border or gx >= width - border or gy >= width - border:
                data[gy * width + gx] = 100
    rectangle(-2.4, -1.9, -2.8, 2.6)
    rectangle(1.2, 3.8, 1.5, 2.0)
    rectangle(1.2, 1.7, -3.2, -1.0)
    rectangle(-8.5, -7.8, 7.5, 8.3, -1)  # unexplored corner
    if extra_wall:
        rectangle(-4.5, -1.0, 4.0, 4.4)  # scenario "map changed" wall
    return data


@dataclass
class World:
    extra_wall: bool = False
    cells: list[int] = field(default_factory=build_cells)
    width: int = round(MAP_METERS / RESOLUTION)
    height: int = round(MAP_METERS / RESOLUTION)

    def rebuild(self, extra_wall: bool) -> None:
        self.extra_wall = extra_wall
        self.cells = build_cells(extra_wall)

    def occupied(self, x: float, y: float) -> bool:
        gx = int((x - MAP_ORIGIN) / RESOLUTION)
        gy = int((y - MAP_ORIGIN) / RESOLUTION)
        if gx < 0 or gy < 0 or gx >= self.width or gy >= self.height:
            return True
        return self.cells[gy * self.width + gx] == 100

    def ray_cast(self, x: float, y: float, angle: float) -> float | None:
        step = RESOLUTION * 0.9
        distance = step
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        while distance <= LIDAR_MAX_RANGE:
            if self.occupied(x + distance * cos_a, y + distance * sin_a):
                return round(distance, 2)
            distance += step
        return None

    def scan(self, x: float, y: float, yaw: float) -> list[float | None]:
        angle_min = -math.pi / 2
        increment = math.pi / (LIDAR_RAYS - 1)
        return [self.ray_cast(x, y, yaw + angle_min + i * increment) for i in range(LIDAR_RAYS)]


@dataclass
class Robot:
    x: float = 5.5
    y: float = 0.0
    yaw: float = math.pi / 2
    speed: float = 0.0
    yaw_rate: float = 0.0
    waypoint_index: int = 0
    goal: tuple[float, float] | None = None

    def set_goal(self, goal: tuple[float, float] | None) -> None:
        self.goal = goal

    def next_waypoint(self) -> tuple[float, float]:
        return WAYPOINTS[self.waypoint_index % len(WAYPOINTS)]

    def advance_waypoint(self) -> None:
        self.waypoint_index += 1

    def step(self, dt: float, moving_allowed: bool = True) -> bool:
        """Advance kinematics toward the goal. Returns True on arrival."""
        if self.goal is None or not moving_allowed:
            self.speed = 0.0
            self.yaw_rate = 0.0
            return False
        dx, dy = self.goal[0] - self.x, self.goal[1] - self.y
        distance = math.hypot(dx, dy)
        if distance < 0.15:
            self.speed = 0.0
            self.yaw_rate = 0.0
            return True
        target_yaw = math.atan2(dy, dx)
        error = math.atan2(math.sin(target_yaw - self.yaw), math.cos(target_yaw - self.yaw))
        self.yaw_rate = max(-TURN_RATE, min(TURN_RATE, 2.0 * error))
        self.yaw += self.yaw_rate * dt
        self.speed = CRUISE_SPEED if abs(error) < 0.6 else 0.12
        self.x += self.speed * math.cos(self.yaw) * dt
        self.y += self.speed * math.sin(self.yaw) * dt
        return False

    def remaining_path(self) -> list[tuple[float, float]]:
        if self.goal is None:
            return []
        return [(round(self.x, 2), round(self.y, 2)), (self.goal[0], self.goal[1])]
