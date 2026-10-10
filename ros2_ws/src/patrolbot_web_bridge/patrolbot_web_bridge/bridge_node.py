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
from .commands import (CHARGING_STATES, DISABLED_REASON, CommandExecutor,
                        localization_epoch_ready)
from .throttle import Debounce, Throttle
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
            ("scan_mirror", True),
            ("scan_angle_offset", 0.0),
            # OFF by default: the 7 MB /map starves /scan when streamed off
            # the Pi (safety watchdog trips). The dashboard server serves a
            # local copy instead (PATROLBOT_STATIC_MAP_YAML).
            ("subscribe_map", False),
            # Undock goal parameters. Deliberately node config, never browser
            # input; the dock manager bounds them again with its own hard caps.
            #
            # These DEFAULTS have to be correct on their own: the deployed
            # container starts the node with inline -p overrides and no
            # --params-file, so config/web_bridge.yaml is not read at all and
            # anything not overridden falls back to here.
            #
            # dock_id must equal the dock manager's own dock_id parameter or it
            # rejects the goal outright ("unknown dock ID"). Distance/speed sit
            # on the manager's validation-mode caps (0.10 m, 0.05 m/s), which
            # it rejects rather than clamps.
            ("undock_dock_id", "main_charger"),
            # Commissioned 2026-07-26 after a validation-capped undock was
            # accepted on the real robot: full reverse, validation mode off.
            # 0.60 m sits under the manager's 0.70 m hard cap; 0.10 m/s is the
            # cap. The manager still reverses via Nav2 BackUp and then turns
            # (undock_turn_yaw), so the robot ends up clear of the dock.
            ("undock_reverse_distance", 0.60),
            # 0.10 m/s is the SBC's reverse authorization ceiling, not a
            # cautious default — 0.20 got the motors cut mid-reverse.
            ("undock_reverse_speed", 0.10),
            # validation_mode is NOT a dry run — it just clamps to 0.10 m /
            # 0.05 m/s and REJECTS anything above. Off now that the real
            # sequence has been exercised.
            ("undock_validation_mode", False),
            # Manual-only. AMCL normally goes silent while a docked robot is
            # stationary, so pose age is not evidence that its last estimate
            # is wrong. Automatically treating that silence as a lost fix
            # republished the configured dock pose every 20-30 seconds and
            # overwrote operator-set /initialpose estimates. The typed guarded
            # initializer remains available for an explicit operator request,
            # but charging or dock residence must never authorize a pose reset.
            ("auto_dock_pose_on_charge", False),
            ("auto_dock_pose_retry_s", 20.0),
            # How old an AMCL fix may be and still count as localized for the
            # purposes of seeding. Generous: AMCL goes quiet on a stationary
            # robot, so this is about detecting a dead/restarted AMCL, not
            # ordinary idle silence.
            ("localization_max_age_s", 30.0),
            # A charger latching against marginal dock contacts inverts
            # charge_state every few seconds. Everything downstream — the dock
            # button's meaning, the command gates, the charging event log,
            # auto_dock_pose_on_charge — treated each frame as truth. A charge
            # reading now has to hold this long before it is published; real
            # dock/undock transitions are one-way and settle immediately.
            ("charge_settle_s", 4.0),
            # Below this the pack is not under charge, whatever the battery
            # driver's status bit claims. The charger holds ~29.7 V; a resting
            # pack sits near 26 V. See normalizers.normalize_battery.
            ("charge_voltage_min", 27.5),
        ])
        get = {param.name: param.value for param in p}
        server_url = os.environ.get("WEB_BRIDGE_SERVER_URL", get["server_url"])
        token = os.environ.get("WEB_BRIDGE_TOKEN", get["token"])
        self.cfg = get

        # Every attribute the WsClient's callbacks can touch must exist before
        # the socket thread starts: the server answers robot.hello with
        # want_map almost immediately, and that lands on _resend_map from the
        # ws thread. Starting the socket first left a window where the reply
        # beat these assignments and the thread died on AttributeError.
        self._command_queue: deque[dict] = deque(maxlen=16)
        self._latest_base_state: dict | None = None
        self._latest_base_state_at = 0.0
        self._latest_odom: Odometry | None = None
        self._latest_odom_at = 0.0
        self._latest_amcl: PoseWithCovarianceStamped | None = None
        self._latest_amcl_at = 0.0
        self._latest_map: OccupancyGrid | None = None
        self._map_signature: tuple | None = None
        self._map_version = 0

        self.ws = WsClient(server_url, token, get["robot_id"],
                           on_map_wanted=self._resend_map,
                           on_command=self._command_queue.append)

        self._commands: CommandExecutor | None = None
        self._announced_capabilities: list[str] | None = None
        self._last_dock_pose_attempt = 0.0
        if os.environ.get("WEB_BRIDGE_ENABLE_COMMANDS", "0") == "1":
            self._commands = CommandExecutor(self, self.ws, undock_config={
                "dock_id": get["undock_dock_id"],
                "reverse_distance": get["undock_reverse_distance"],
                "reverse_speed": get["undock_reverse_speed"],
                "validation_mode": get["undock_validation_mode"],
            })
            # Re-advertise as dock-manager servers come and go: capabilities
            # are what the dashboard gates its controls on, and the manager may
            # start after this node does.
            self.create_timer(2.0, self._refresh_capabilities)
        self.create_timer(0.1, self._drain_commands)

        self._last_path_points: list | None = None
        self._start = self.get_clock().now()

        # Both charge signals reach the dashboard on separate topics, so both
        # need settling — otherwise a debounced charge_state still sits beside
        # a battery widget flickering "Charging" on and off.
        self._charge_state_debounce = Debounce(float(get["charge_settle_s"]))
        self._charging_debounce = Debounce(float(get["charge_settle_s"]))

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

        # Last: the socket thread can call back into this node the moment it
        # connects, so everything it touches is in place before it starts.
        self.ws.start()
        self.get_logger().info(f"web bridge up; streaming to {server_url}")

    # -- callbacks -------------------------------------------------------------

    def _on_odom(self, msg: Odometry) -> None:
        self._latest_odom = msg
        self._latest_odom_at = self.get_clock().now().nanoseconds / 1e9
        self._maybe_send_pose()

    def _on_amcl(self, msg: PoseWithCovarianceStamped) -> None:
        self._latest_amcl = msg
        self._latest_amcl_at = self.get_clock().now().nanoseconds / 1e9
        self._maybe_send_pose()

    def _maybe_send_pose(self) -> None:
        if not self._pose_throttle.ready():
            return
        payload = normalizers.normalize_pose(
            self._latest_amcl, self._latest_odom,
            covariance_warn=float(self.cfg["covariance_warn_threshold"]),
        )
        if payload:
            # One definition of "localized", so the dashboard's navigation gate
            # and this node's own precheck cannot disagree. normalize_pose
            # judges the covariance alone; a confident fix that AMCL stopped
            # publishing stays confident forever, and only the age check here
            # notices.
            payload["localized"] = self._localization_is_usable()
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
        payload = normalizers.normalize_battery(
            msg, charge_voltage_min=float(self.cfg["charge_voltage_min"]))
        if payload is None:
            # Unmeasurable voltage — see normalize_battery. Don't feed the
            # charging debounce either: a NaN sample is absence of a reading,
            # not evidence that the robot stopped charging.
            return
        payload["charging"] = self._charging_debounce.update(payload["charging"])
        self.ws.send("telemetry.battery", payload)

    def _on_diagnostics(self, msg: DiagnosticArray) -> None:
        self.ws.send("telemetry.diagnostics", normalizers.normalize_diagnostics(msg))

    def _on_base_state(self, msg) -> None:
        payload = normalizers.normalize_base_state(msg)
        # Settle before anything reads it: _latest_base_state feeds the local
        # command prechecks, the payload feeds the server's gates and the UI,
        # and _maybe_seed_dock_pose re-seeds localization off it.
        payload["charge_state"] = self._charge_state_debounce.update(payload["charge_state"])
        self._latest_base_state = payload
        self._latest_base_state_at = self.get_clock().now().nanoseconds / 1e9
        self.ws.send("telemetry.base_state", payload)
        self._maybe_seed_dock_pose(payload)

    def _maybe_seed_dock_pose(self, base_state: dict) -> None:
        """A charging robot is, by definition, at the dock — and the dock does
        not move. So when we see charging without a usable localization fix,
        ask the dock manager to seed the pose it has configured.

        Deliberately routed through /patrolbot/initialize_dock_pose rather than
        publishing /initialpose here: that service re-checks that the robot is
        really docked and stationary before it publishes anything, so a stale
        or wrong charge reading cannot teleport the robot's estimate.
        """
        if self._commands is None or not bool(self.cfg["auto_dock_pose_on_charge"]):
            return
        # Retries stop on their own once AMCL has a tight enough fix, so there
        # is no "already done" flag to get out of sync with reality.
        if base_state.get("charge_state") not in CHARGING_STATES:
            return
        if self._localization_is_usable():
            return
        now = self.get_clock().now().nanoseconds / 1e9
        if now - self._last_dock_pose_attempt < float(self.cfg["auto_dock_pose_retry_s"]):
            return
        self._last_dock_pose_attempt = now
        if self._commands.initialize_dock_pose(str(self.cfg["undock_dock_id"])):
            self.get_logger().info(
                "charging without a localization fix — seeding the dock pose")

    def _localization_is_usable(self) -> bool:
        """A tight covariance is not enough — the fix has to be recent too.

        Nav2 restarting leaves this node holding the last pose AMCL ever sent,
        which still looks confident. Judging on covariance alone meant the
        bridge believed localization was fine and never re-seeded the dock
        pose, while the dock manager (which does check age) sat refusing to
        undock with "localization invalid".

        This dashboard age is a 30-second display/command freshness policy.
        Dock Manager retains the separate 0.5-second safety freshness policy;
        a parked valid pose may become unrefreshed, but that never authorizes
        undock. Keep the two policies explicit rather than changing either
        threshold to hide a contract mismatch.
        """
        if self._latest_amcl is None or self._latest_base_state is None:
            return False
        now_ns = self.get_clock().now().nanoseconds
        now = now_ns / 1e9
        base_age = now - self._latest_base_state_at
        try:
            stamp = self._latest_amcl.header.stamp
            amcl_stamp_ns = int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)
        except (AttributeError, TypeError, ValueError, OverflowError):
            return False
        if not localization_epoch_ready(self._latest_base_state,
                                        amcl_stamp_ns, base_age):
            return False
        age = now - self._latest_amcl_at
        if age > float(self.cfg["localization_max_age_s"]):
            return False
        return normalizers.amcl_localization_usable(
            self._latest_amcl,
            float(self.cfg["covariance_warn_threshold"]),
        )

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
            base_state_age = None
            if self._latest_base_state is not None:
                base_state_age = (self.get_clock().now().nanoseconds / 1e9
                                  - self._latest_base_state_at)
            odom_age = None
            if self._latest_odom is not None:
                odom_age = (self.get_clock().now().nanoseconds / 1e9
                            - self._latest_odom_at)
            self._commands.handle(data, self._latest_base_state, current_yaw,
                                  self._localization_is_usable(), base_state_age,
                                  odom_age)

    def _refresh_capabilities(self) -> None:
        """Tell the dashboard which dock operations are actually available.

        Advertising is the commissioning switch: the dashboard greys out any
        control whose capability the robot has not claimed, so nothing is
        offered until the dock manager is genuinely up and answering.
        """
        if self._commands is None:
            return
        # Same 2 s tick releases an undock whose action server died holding the
        # goal; otherwise the bridge refuses every later attempt with "already
        # undocking" until the container is restarted.
        self._commands.check_undock_health()
        available = self._commands.available_capabilities()
        # Log through the ROS logger, not the stdlib one: nothing configures a
        # stdlib handler in the container, so log.info there goes nowhere and
        # this transition is exactly what an operator needs to see.
        if available != self._announced_capabilities:
            self._announced_capabilities = available
            self.get_logger().info(
                f"dock capabilities: {', '.join(available) if available else 'none available'}")
        self.ws.set_dock_capabilities(available)

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
