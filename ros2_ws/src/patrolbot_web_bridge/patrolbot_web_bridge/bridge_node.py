"""patrolbot_web_bridge: read-only telemetry bridge for the dashboard.

Runs on the RPi5 inside the robot's ROS 2 graph (host-network container,
ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST). Subscribes to approved topics,
normalizes them, throttles, and streams them over an OUTBOUND WebSocket to
the dashboard server. It never opens connections to the SBC — SBC health is
whatever /patrolbot/base_state already reports.

Motion commands (Phase 3) are OFF by default: without
WEB_BRIDGE_ENABLE_COMMANDS=1 this node has no publishers and no
action/service clients, and every command.request is declined. When enabled,
commands are goal-based only (Nav2 NavigateToPose, /initialpose, cancel) —
never /cmd_vel, and never anything that talks to the SBC.
"""
from __future__ import annotations

import os
from collections import deque

import rclpy
from diagnostic_msgs.msg import DiagnosticArray
from geometry_msgs.msg import PoseWithCovarianceStamped
from nav_msgs.msg import OccupancyGrid, Odometry, Path
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy,
    QoSProfile,
    ReliabilityPolicy,
    qos_profile_sensor_data,
)
from sensor_msgs.msg import BatteryState, LaserScan

from . import normalizers, resources
from .commands import DISABLED_REASON, CommandExecutor
from .throttle import Throttle
from .ws_client import WsClient

try:
    from patrolbot_interfaces.msg import BaseState
except ImportError:  # allows running against the simulator without the iface pkg
    BaseState = None


class WebBridgeNode(Node):
    def __init__(self) -> None:
        super().__init__("patrolbot_web_bridge")

        p = self.declare_parameters("", [
            ("server_url", "ws://localhost:8000/ws/robot"),
            ("token", "dev-token"),
            ("robot_id", "patrolbot-01"),
            ("topic_map", "/map"),
            ("topic_amcl_pose", "/amcl_pose"),
            ("topic_odom", "/odom"),
            ("topic_scan", "/scan"),
            ("topic_plan", "/plan"),
            ("topic_battery", "/battery"),
            ("topic_diagnostics", "/diagnostics"),
            ("topic_base_state", "/patrolbot/base_state"),
            ("pose_interval", 0.1),
            ("scan_interval", 0.2),
            ("path_interval", 0.5),
            ("slow_interval", 1.0),
            ("scan_max_points", 360),
            ("path_max_points", 200),
            ("map_name", "PatrolBot map"),
            ("covariance_warn_threshold", 0.25),
            # Laser mounting relative to base_link. The dashboard projects the
            # scan from the robot's base pose (it doesn't consume /tf), so a
            # non-trivial laser mount must be applied here. scan_mirror handles
            # an upside-down laser (180deg roll: rays (r, theta) -> (r, -theta));
            # scan_angle_offset (radians) handles a laser mounted rotated in yaw.
            ("scan_mirror", False),
            ("scan_angle_offset", 0.0),
            # OFF by default: the 7 MB /map starves /scan when streamed off
            # the Pi (safety watchdog trips). The dashboard server serves a
            # local copy instead (PATROLBOT_STATIC_MAP_YAML).
            ("subscribe_map", False),
        ])
        get = {param.name: param.value for param in p}
        server_url = os.environ.get("WEB_BRIDGE_SERVER_URL", get["server_url"])
        token = os.environ.get("WEB_BRIDGE_TOKEN", get["token"])
        self.cfg = get

        self._command_queue: deque[dict] = deque(maxlen=16)
        self.ws = WsClient(server_url, token, get["robot_id"],
                           on_map_wanted=self._resend_map,
                           on_command=self._command_queue.append)
        self.ws.start()

        self._commands: CommandExecutor | None = None
        if os.environ.get("WEB_BRIDGE_ENABLE_COMMANDS", "0") == "1":
            self._commands = CommandExecutor(self, self.ws)
        self._latest_base_state: dict | None = None
        self.create_timer(0.1, self._drain_commands)

        self._latest_odom: Odometry | None = None
        self._latest_amcl: PoseWithCovarianceStamped | None = None
        self._latest_map: OccupancyGrid | None = None
        self._map_signature: tuple | None = None
        self._map_version = 0
        self._last_path_points: list | None = None
        self._start = self.get_clock().now()

        self._pose_throttle = Throttle(float(get["pose_interval"]))
        self._scan_throttle = Throttle(float(get["scan_interval"]))
        self._path_throttle = Throttle(float(get["path_interval"]))

        latched = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                             durability=DurabilityPolicy.TRANSIENT_LOCAL)
        reliable = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE)

        if bool(get["subscribe_map"]) or os.environ.get("WEB_BRIDGE_SUBSCRIBE_MAP") == "1":
            self.create_subscription(OccupancyGrid, get["topic_map"], self._on_map, latched)
        self.create_subscription(PoseWithCovarianceStamped, get["topic_amcl_pose"],
                                 self._on_amcl, latched)
        self.create_subscription(Odometry, get["topic_odom"], self._on_odom, reliable)
        self.create_subscription(LaserScan, get["topic_scan"], self._on_scan,
                                 qos_profile_sensor_data)
        self.create_subscription(Path, get["topic_plan"], self._on_plan, reliable)
        self.create_subscription(BatteryState, get["topic_battery"], self._on_battery, reliable)
        self.create_subscription(DiagnosticArray, get["topic_diagnostics"],
                                 self._on_diagnostics, reliable)
        if BaseState is not None:
            self.create_subscription(BaseState, get["topic_base_state"],
                                     self._on_base_state, reliable)
        else:
            self.get_logger().warning(
                "patrolbot_interfaces not available — base_state telemetry disabled")

        self.create_timer(float(get["slow_interval"]), self._slow_tick)
        self.get_logger().info(f"web bridge up; streaming to {server_url}")

    # -- callbacks -------------------------------------------------------------

    def _on_odom(self, msg: Odometry) -> None:
        self._latest_odom = msg
        self._maybe_send_pose()

    def _on_amcl(self, msg: PoseWithCovarianceStamped) -> None:
        self._latest_amcl = msg
        self._maybe_send_pose()

    def _maybe_send_pose(self) -> None:
        if not self._pose_throttle.ready():
            return
        payload = normalizers.normalize_pose(
            self._latest_amcl, self._latest_odom,
            covariance_warn=float(self.cfg["covariance_warn_threshold"]),
        )
        if payload:
            self.ws.send("telemetry.pose", payload)

    def _on_scan(self, msg: LaserScan) -> None:
        if self._scan_throttle.ready():
            self.ws.send("telemetry.lidar",
                         normalizers.normalize_scan(
                             msg, int(self.cfg["scan_max_points"]),
                             angle_offset=float(self.cfg["scan_angle_offset"]),
                             mirror=bool(self.cfg["scan_mirror"])))

    def _on_plan(self, msg: Path) -> None:
        if not self._path_throttle.ready():
            return
        payload = normalizers.normalize_path(msg, int(self.cfg["path_max_points"]))
        if payload["points"] != self._last_path_points:
            self._last_path_points = payload["points"]
            self.ws.send("telemetry.path", payload)

    def _on_battery(self, msg: BatteryState) -> None:
        self.ws.send("telemetry.battery", normalizers.normalize_battery(msg))

    def _on_diagnostics(self, msg: DiagnosticArray) -> None:
        self.ws.send("telemetry.diagnostics", normalizers.normalize_diagnostics(msg))

    def _on_base_state(self, msg) -> None:
        payload = normalizers.normalize_base_state(msg)
        self._latest_base_state = payload
        self.ws.send("telemetry.base_state", payload)

    def _on_map(self, msg: OccupancyGrid) -> None:
        signature = normalizers.map_signature(msg)
        if signature == self._map_signature:
            return
        self._map_signature = signature
        self._map_version += 1
        self._latest_map = msg
        self.ws.map_version = self._map_version
        self._send_map()

    def _send_map(self) -> None:
        if self._latest_map is None:
            return
        self.ws.send("telemetry.map", normalizers.normalize_map(
            self._latest_map, self._map_version, str(self.cfg["map_name"])))
        self.get_logger().info(f"sent map v{self._map_version}")

    def _resend_map(self) -> None:
        # Called from the WS thread after (re)connect when the server wants
        # the map; WsClient.send is thread-safe.
        self._send_map()

    def _drain_commands(self) -> None:
        while self._command_queue:
            data = self._command_queue.popleft()
            if self._commands is None:
                self.ws.send("command.ack", {
                    "command_id": str(data.get("command_id", "")),
                    "accepted": False, "reason": DISABLED_REASON,
                })
                continue
            current_yaw = None
            if self._latest_amcl is not None:
                q = self._latest_amcl.pose.pose.orientation
                current_yaw = normalizers.quaternion_to_yaw(q.x, q.y, q.z, q.w)
            self._commands.handle(data, self._latest_base_state, current_yaw)

    def _slow_tick(self) -> None:
        uptime = (self.get_clock().now() - self._start).nanoseconds / 1e9
        self.ws.send("telemetry.heartbeat", {"uptime_s": round(uptime, 1)})
        self.ws.send("telemetry.resources", resources.sample())


def main() -> None:
    rclpy.init()
    node = WebBridgeNode()
    executor = MultiThreadedExecutor(num_threads=2)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.ws.stop()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
