"""Outbound WebSocket client + emission loops for the mock robot.

Connection behavior (reconnect with jittered exponential backoff, hello
handshake, map-on-request) deliberately mirrors what the real
patrolbot_web_bridge does, so the server cannot tell them apart.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import time
from datetime import datetime, timezone

import psutil
import websockets

from .battery import Battery
from .diagnostics import base_state_payload, diagnostics_payload
from .scenarios import Scenario
from .world import DOCK, MAP_ORIGIN, RESOLUTION, Robot, World

log = logging.getLogger("mock_robot")

PROTOCOL_VERSION = 1
CAPABILITIES = ["pose", "lidar", "path", "battery", "base_state", "diagnostics", "resources", "map"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def encode_rle(cells: list[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for value in cells:
        if runs and runs[-1][0] == value:
            runs[-1][1] += 1
        else:
            runs.append([value, 1])
    return runs


class MockRobot:
    def __init__(self, server_url: str, token: str, robot_id: str, scenario: str) -> None:
        self.server_url = server_url
        self.token = token
        self.robot_id = robot_id
        self.scenario = Scenario(scenario)
        self.world = World()
        self.robot = Robot()
        self.battery = Battery()
        self.start_mono = time.monotonic()
        self.sequence = 0
        self.map_version = 1
        self.session_generation = 1
        self.mode = "patrol"  # patrol | to_dock | charging | commanded | idle
        self.command: dict | None = None  # active navigate command {command_id}
        self.robot.set_goal(self.robot.next_waypoint())

    def elapsed(self) -> float:
        return time.monotonic() - self.start_mono

    def frame(self, type_: str, data: dict) -> str:
        self.sequence += 1
        return json.dumps({
            "version": PROTOCOL_VERSION, "type": type_, "robot_id": self.robot_id,
            "sequence": self.sequence, "timestamp": utc_now(), "data": data,
        }, separators=(",", ":"))

    def map_payload(self) -> dict:
        return {
            "map_version": self.map_version,
            "name": "CMU-Q Floor 1 (simulated)" + (" — updated" if self.world.extra_wall else ""),
            "resolution": RESOLUTION,
            "width": self.world.width,
            "height": self.world.height,
            "origin": {"x": MAP_ORIGIN, "y": MAP_ORIGIN, "yaw": 0.0},
            "rle": encode_rle(self.world.cells),
        }

    # -- simulation stepping --------------------------------------------------

    def step(self, dt: float) -> None:
        elapsed = self.elapsed()
        estop = self.scenario.estop_active(elapsed)
        moving_allowed = not estop and self.mode != "charging"
        arrived = self.robot.step(dt, moving_allowed=moving_allowed)
        self.battery.step(dt, discharging_allowed=self.mode != "charging")

        if self.mode in ("patrol", "idle") and self.battery.needs_charge:
            self.mode = "to_dock"
            self.robot.set_goal(DOCK)
            log.info("battery low (%.0f%%) — heading to dock", self.battery.percentage)
        elif self.mode == "patrol":
            if arrived:
                self.robot.advance_waypoint()
                self.robot.set_goal(self.robot.next_waypoint())
        elif self.mode == "commanded" and arrived:
            self._command_arrived = True
        elif self.mode == "to_dock" and arrived:
            self.mode = "charging"
            self.battery.charging = True
            self.robot.set_goal(None)
            log.info("docked, charging")
        elif self.mode == "charging" and not self.battery.charging:
            self.mode = "patrol"
            self.robot.set_goal(self.robot.next_waypoint())
            log.info("charged — resuming patrol")

        wanted_wall = self.scenario.wants_extra_wall(elapsed)
        if wanted_wall != self.world.extra_wall:
            self.world.rebuild(wanted_wall)
            self.map_version += 1
            self._map_dirty = True

    _map_dirty = False
    _command_arrived = False

    # -- emission loops --------------------------------------------------------

    async def run_session(self) -> None:
        url = f"{self.server_url}?token={self.token}"
        async with websockets.connect(url, max_size=16 * 1024 * 1024) as ws:
            await ws.send(self.frame("robot.hello", {
                "protocol_version": PROTOCOL_VERSION, "capabilities": CAPABILITIES,
                "map_version": self.map_version, "software_version": "mock-0.1.0",
            }))
            ack = json.loads(await ws.recv())
            if ack.get("type") != "server.hello_ack":
                raise RuntimeError(f"unexpected reply to hello: {ack.get('type')}")
            if ack["data"].get("want_map", True):
                await ws.send(self.frame("telemetry.map", self.map_payload()))
            log.info("connected to %s", self.server_url)

            self.command = None  # a command does not survive a reconnect
            self._command_arrived = False
            tasks = [
                asyncio.create_task(self._pose_loop(ws)),
                asyncio.create_task(self._lidar_loop(ws)),
                asyncio.create_task(self._path_loop(ws)),
                asyncio.create_task(self._slow_loop(ws)),
                asyncio.create_task(self._receive_loop(ws)),
                asyncio.create_task(self._disconnect_watch()),
            ]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc is not None:
                    # Includes _DisconnectDrill: run_forever waits out the
                    # drill window before reconnecting.
                    raise exc

    async def _pose_loop(self, ws) -> None:
        last = time.monotonic()
        while True:
            await asyncio.sleep(0.1)
            now = time.monotonic()
            self.step(now - last)
            last = now
            phase = self.elapsed() % 30.0
            uncertain = 20.0 <= phase < 27.0 and int(self.elapsed() // 30) % 3 == 1
            await ws.send(self.frame("telemetry.pose", {
                "frame_id": "map",
                "x": round(self.robot.x, 3), "y": round(self.robot.y, 3),
                "yaw": round(math.atan2(math.sin(self.robot.yaw), math.cos(self.robot.yaw)), 3),
                "linear_velocity": round(self.robot.speed, 3),
                "angular_velocity": round(self.robot.yaw_rate, 3),
                "covariance_trace": 0.4 if uncertain else 0.05,
                "localized": not uncertain,
            }))

    async def _lidar_loop(self, ws) -> None:
        while True:
            await asyncio.sleep(0.2)
            ranges = self.world.scan(self.robot.x, self.robot.y, self.robot.yaw)
            await ws.send(self.frame("telemetry.lidar", {
                "angle_min": -math.pi / 2,
                "angle_increment": math.pi / (len(ranges) - 1),
                "ranges": ranges,
            }))

    async def _path_loop(self, ws) -> None:
        while True:
            await asyncio.sleep(0.5)
            goal = self.robot.goal
            await ws.send(self.frame("telemetry.path", {
                "frame_id": "map",
                "points": self.robot.remaining_path(),
                "goal": None if goal is None else {"x": goal[0], "y": goal[1], "yaw": None},
            }))

    async def _slow_loop(self, ws) -> None:
        """1 Hz: heartbeat, battery, base_state, diagnostics, resources, map changes."""
        while True:
            await asyncio.sleep(1.0)
            elapsed = self.elapsed()
            front, rear = self.scenario.bumper_active(elapsed)
            estop = self.scenario.estop_active(elapsed)
            await ws.send(self.frame("telemetry.heartbeat", {"uptime_s": round(elapsed, 1)}))
            await ws.send(self.frame("telemetry.battery", self.battery.payload()))
            await ws.send(self.frame("telemetry.base_state", base_state_payload(
                session_generation=self.session_generation,
                charging=self.battery.charging,
                docked=self.mode == "charging",
                estop=estop, bumper_front=front, bumper_rear=rear,
            )))
            await ws.send(self.frame("telemetry.diagnostics",
                                     diagnostics_payload(elapsed, self.battery.level, front or rear)))
            await ws.send(self.frame("telemetry.resources", {
                "cpu_percent": psutil.cpu_percent(),
                "memory_percent": psutil.virtual_memory().percent,
                "cpu_temp_c": _cpu_temp(),
                "disk_percent": psutil.disk_usage("/").percent,
                "wifi_signal_dbm": -55 - int(5 * math.sin(elapsed / 30.0)),
            }))
            await self._command_progress(ws)
            if self._map_dirty:
                self._map_dirty = False
                await ws.send(self.frame("telemetry.map", self.map_payload()))
                log.info("map changed -> version %d", self.map_version)

    # -- command handling ------------------------------------------------------

    async def _receive_loop(self, ws) -> None:
        """Handle command.request frames; mirrors what the real bridge does."""
        async for raw in ws:
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if frame.get("type") != "command.request":
                continue
            data = frame.get("data", {})
            command_id, command = data.get("command_id"), data.get("command")
            log.info("command received: %s (%s)", command, command_id)

            if command == "navigate_to_pose":
                await self._preempt(ws, "A newer destination replaced this one.")
                goal = data["goal"]
                self.command = {"command_id": command_id}
                self._command_arrived = False
                self.mode = "commanded"
                self.battery.charging = False
                self.robot.set_goal((goal["x"], goal["y"]))
                await ws.send(self.frame("command.ack",
                                         {"command_id": command_id, "accepted": True}))
            elif command == "stop":
                await ws.send(self.frame("command.ack",
                                         {"command_id": command_id, "accepted": True}))
                await self._preempt(ws, "Stopped by the operator.")
                self.mode = "idle"
                self.robot.set_goal(None)
                await ws.send(self.frame("command.result",
                                         {"command_id": command_id, "outcome": "succeeded",
                                          "detail": "The robot has stopped."}))
            elif command == "set_initial_pose":
                goal = data["goal"]
                self.robot.x, self.robot.y = goal["x"], goal["y"]
                if goal.get("yaw") is not None:
                    self.robot.yaw = goal["yaw"]
                await ws.send(self.frame("command.ack",
                                         {"command_id": command_id, "accepted": True}))
                await ws.send(self.frame("command.result",
                                         {"command_id": command_id, "outcome": "succeeded",
                                          "detail": "Robot location updated."}))
            else:
                await ws.send(self.frame("command.ack",
                                         {"command_id": command_id, "accepted": False,
                                          "reason": f"Unknown command: {command}"}))

    async def _preempt(self, ws, detail: str) -> None:
        if self.command is not None:
            await ws.send(self.frame("command.result",
                                     {"command_id": self.command["command_id"],
                                      "outcome": "canceled", "detail": detail}))
            self.command = None

    async def _command_progress(self, ws) -> None:
        if self.command is None:
            return
        if self._command_arrived:
            await ws.send(self.frame("command.result",
                                     {"command_id": self.command["command_id"],
                                      "outcome": "succeeded",
                                      "detail": "Arrived at the destination."}))
            self.command = None
            self._command_arrived = False
            self.mode = "idle"
            return
        goal = self.robot.goal
        if goal is not None:
            distance = math.hypot(goal[0] - self.robot.x, goal[1] - self.robot.y)
            await ws.send(self.frame("command.progress",
                                     {"command_id": self.command["command_id"],
                                      "stage": "navigating",
                                      "detail": "Heading to the destination",
                                      "distance_remaining": round(distance, 2)}))

    async def _disconnect_watch(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            if self.scenario.in_disconnect_window(self.elapsed()):
                log.info("scenario: disconnect drill — dropping connection for ~15 s")
                raise _DisconnectDrill()

    # -- top-level reconnect loop ----------------------------------------------

    async def run_forever(self) -> None:
        retry = 0
        while True:
            try:
                await self.run_session()
                retry = 0
            except _DisconnectDrill:
                self.session_generation += 1
                # Sit out the rest of the drill window, then reconnect quickly.
                while self.scenario.in_disconnect_window(self.elapsed()):
                    await asyncio.sleep(0.5)
                retry = 0
                continue
            except (OSError, websockets.WebSocketException, RuntimeError) as exc:
                delay = min(8.0, 0.5 * (2 ** retry)) * (1.0 + random.random() * 0.3)
                retry += 1
                self.session_generation += 1
                log.warning("connection lost (%s) — retrying in %.1f s", exc, delay)
                await asyncio.sleep(delay)


class _DisconnectDrill(Exception):
    pass


def _cpu_temp() -> float | None:
    try:
        temps = psutil.sensors_temperatures()
        for entries in temps.values():
            if entries:
                return round(entries[0].current, 1)
    except Exception:
        pass
    return None
