"""Bounded web-manual sessions; no direct wheel, motor or localization authority.

The source joins the existing priority-7 mux input below the physical F710.
Receipt proves software delivery only, never physical movement or a safe stop.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import threading
import time
from typing import Callable
from uuid import UUID


CAPABILITY = "web_teleop_v1"
HARDWARE_TIMEOUT_S = 0.5  # Existing raw hardware/odometry freshness contract.
ACTION_NAMES = (
    "navigate_to_pose", "navigate_through_poses", "follow_path", "follow_waypoints",
    "spin", "backup", "wait", "drive_on_heading", "assisted_teleop",
    "patrolbot/undock", "patrolbot/hardware_undock", "patrolbot/undock_turn",
    "patrolbot/localize_on_map",
)


def read_contract() -> dict:
    """Load the reviewed, packaged robot/dashboard wire contract."""
    path = Path(__file__).resolve().parent.parent / "config/web_teleop.json"
    if not path.exists():
        from ament_index_python.packages import get_package_share_directory
        path = Path(get_package_share_directory("patrolbot_web_bridge")) / "config/web_teleop.json"
    data = json.loads(path.read_text())
    if data.get("contract") != "patrolbot-web-teleop" or data.get("version") != 1:
        raise ValueError("Unsupported web teleop contract")
    return data


def valid_id(value) -> bool:
    if not isinstance(value, str) or len(value) > 128:
        return False
    try:
        return str(UUID(value)) == value.lower()
    except (ValueError, AttributeError):
        return False


class TeleopSession:
    """Own one authorized transport-bound stream, expiring without retries."""

    def __init__(self, *, publish: Callable, send: Callable, safety: Callable,
                 clock: Callable = time.monotonic, contract: dict | None = None):
        self.publish = publish
        self.send = send
        self.safety = safety
        self.clock = clock
        self.contract = contract or read_contract()
        self.timeout = self.contract["timing"]["web_bridge_zero_watchdog_ms"] / 1000.0
        limits = self.contract["velocity_limits"]
        self.linear_limits = limits["linear_m_s"]
        self.angular_limits = limits["angular_rad_s"]
        self.command_id = None
        self.context = None
        self.sequence = -1
        self.last_received = None
        self.acknowledged = False
        self.preparing_at = None
        self._lock = threading.RLock()
        # Closed IDs cannot re-open after release, delayed dispatch or reconnect.
        self._seen: set[str] = set()

    @property
    def active(self) -> bool:
        return self.command_id is not None

    def _ack(self, command_id, accepted, reason=None):
        self.send("command.ack", {"command_id": command_id,
                                  "accepted": accepted, "reason": reason})

    def start(self, data: dict, context, *, received_at: float | None = None,
              defer_ack: bool = False):
        with self._lock:
            command_id = data.get("command_id", "")
            reason = None
            if data.get("operator_authorized") is not True or not valid_id(command_id):
                reason = "An authorized, valid manual-drive request is required."
            elif self.active:
                reason = "Manual drive already has an owner. End that session first."
            elif command_id in self._seen:
                reason = "This manual session has already ended; start a new session."
            elif received_at is None or not 0 <= self.clock() - received_at <= self.timeout:
                reason = "The manual-drive request is stale. Start again explicitly."
            else:
                reason = self.safety()
            if reason:
                self._ack(command_id, False, reason)
                return False
            if len(self._seen) >= 4096:
                # Fail closed instead of evicting a replay barrier silently.
                self._ack(command_id, False, "Manual session history is full; supervised bridge recovery is required.")
                return False
            self._seen.add(command_id)
            self.command_id = command_id
            self.context = context
            self.sequence = -1
            self.last_received = self.clock()
            self.preparing_at = self.clock()
            self.acknowledged = False
            try:
                self.publish(0.0, 0.0, False)
            except Exception:
                self.end("Manual zero publication failed; physical stop is not confirmed.", "failed")
                return False
            if not defer_ack:
                self.confirm_start(context)
            return True

    def confirm_start(self, context):
        with self._lock:
            if not self.active or self.acknowledged:
                return False
            reason = self.safety()
            if reason or context != self.context:
                self.end(reason or "The manual session changed before acceptance.", "failed")
                return False
            self.acknowledged = True
            self.last_received = self.clock()
            self._ack(self.command_id, True)
            self.send("command.progress", {"command_id": self.command_id,
                "stage": "manual_ready", "detail": "Manual input ready. Hold the joystick or keyboard deadman.",
                "distance_remaining": None})
            return True

    def frame(self, data: dict, context, *, received_at: float):
        with self._lock:
            if not isinstance(data, dict):
                self.end("Malformed manual input; session ended.", "failed")
                return False
            if not self.active or data.get("command_id") != self.command_id:
                return False
            if not self.acknowledged:
                return False
            if context != self.context:
                self.end("Connection or hardware session changed; manual input ended.", "failed")
                return False
            if not isinstance(data, dict) or set(data) != {"command_id", "seq", "linear", "angular", "deadman"}:
                self.end("Malformed manual input; session ended.", "failed")
                return False
            seq, linear, angular, deadman = (data.get(key) for key in ("seq", "linear", "angular", "deadman"))
            if (type(seq) is not int or seq < 0 or type(deadman) is not bool
                    or type(linear) not in (int, float) or type(angular) not in (int, float)
                    or not math.isfinite(linear) or not math.isfinite(angular)
                    or not self.linear_limits["min"] <= linear <= self.linear_limits["max"]
                    or not self.angular_limits["min"] <= angular <= self.angular_limits["max"]
                    or (not deadman and (linear != 0.0 or angular != 0.0))):
                self.end("Invalid or out-of-range manual input; session ended.", "failed")
                return False
            if seq <= self.sequence:
                return False
            now = self.clock()
            if not 0 <= now - received_at <= self.timeout:
                self.end("Manual input arrived too late; start a new session.", "timeout")
                return False
            if self.last_received is None or not 0 <= now - self.last_received < self.timeout:
                self.end("Manual input timed out; a new explicit start is required.", "timeout")
                return False
            self.sequence = seq
            if not deadman:
                self.end("Manual input released; zero requested.", "succeeded")
                return True
            reason = self.safety()
            if reason:
                self.end(reason, "failed")
                return False
            self.last_received = now
            try:
                self.publish(float(linear), float(angular), True)
            except Exception:
                self.end("Manual publication failed; physical stop is not confirmed.", "failed")
                return False
            return True

    def tick(self, context):
        with self._lock:
            if not self.active:
                return
            reason = self.safety()
            if context != self.context:
                reason = "Connection or hardware session changed; manual input ended."
            if reason:
                self.end(reason, "failed")
                return
            if not self.acknowledged:
                if self.clock() - self.preparing_at >= 5.0:
                    self.end("The manual command buffers could not be confirmed clear.", "timeout")
                return
            elapsed = self.clock() - self.last_received
            if not 0 <= elapsed < self.timeout:
                self.end("Manual input timed out; zero requested. Start again explicitly.", "timeout")

    def end(self, reason="Manual input ended; zero requested.", outcome="canceled"):
        with self._lock:
            command_id = self.command_id
            acknowledged = self.acknowledged
            self.command_id = None
            self.context = None
            self.last_received = None
            self.acknowledged = False
            self.preparing_at = None
            try:
                self.publish(0.0, 0.0, False)
            except Exception:
                reason += " Zero publication failed; physical stop is not confirmed."
                outcome = "failed"
            if command_id is not None:
                if acknowledged:
                    # We positively closed this input lease. `timeout` is
                    # reserved by the broker for an UNKNOWN RPC outcome, not
                    # a known local watchdog cancellation. No physical stop
                    # is claimed by either terminal.
                    terminal = "canceled" if outcome == "timeout" else outcome
                    self.send("command.result", {"command_id": command_id, "outcome": terminal, "detail": reason})
                else:
                    self._ack(command_id, False, reason)


class RosTeleop:
    """Publish solely through the existing web mux input when every gate holds."""

    def __init__(self, node, ws, *, state: Callable, context: Callable):
        from action_msgs.msg import GoalStatusArray
        from geometry_msgs.msg import Twist
        from std_msgs.msg import Bool
        from std_srvs.srv import Trigger

        self.node, self.ws = node, ws
        self.state, self.context = state, context
        self._Twist, self._Bool = Twist, Bool
        self._drain = {}
        self._drain_source = {}
        self._writers = None
        self.contract = read_contract()
        self.velocity = node.create_publisher(Twist, self.contract["topics"]["velocity"]["name"], 10)
        self.evidence = node.create_publisher(Bool, self.contract["topics"]["evidence"]["name"], 10)
        self.manual_at = None
        self.action_active = {name: False for name in ACTION_NAMES}
        self.idle_at = None
        self.idle_future = None
        self.idle_request_at = None
        self.last_idle_request = float("-inf")
        self.idle = node.create_client(Trigger, "/bt_navigator/navigation_idle")
        self._Trigger = Trigger
        self.session = TeleopSession(publish=self._publish, send=ws.send, safety=self.safety,
                                    contract=self.contract)
        node.create_subscription(Bool, self.contract["topics"]["joystick_evidence"]["name"],
                                 self._on_joystick, 10)
        from rclpy.qos import qos_profile_action_status_default
        for name in ACTION_NAMES:
            node.create_subscription(GoalStatusArray, "/" + name + "/_action/status",
                lambda msg, action=name: self._on_action(action, msg), qos_profile_action_status_default)
        for topic, publisher in (("/cmd_vel_out", "cmd_vel_mux"),
                                 ("/cmd_vel", "teleop_velocity_smoother")):
            node.create_subscription(Twist, topic,
                lambda msg, info, name=topic, owner=publisher: self._on_buffer(name, owner, msg, info), 10)
        node.create_timer(0.025, self.tick)
        self._publish(0.0, 0.0, False)

    def _publish(self, linear, angular, active):
        # False evidence precedes a stop; true never appears before its Twist.
        if not active:
            self.evidence.publish(self._Bool(data=False))
        message = self._Twist()
        message.linear.x, message.angular.z = float(linear), float(angular)
        self.velocity.publish(message)
        if active:
            self.evidence.publish(self._Bool(data=True))

    def _on_joystick(self, message):
        if message.data:
            self.manual_at = time.monotonic()
            if self.session.active:
                self.session.end("The physical joystick took control. Start web manual input again explicitly.")
        else:
            self.manual_at = None

    def _on_action(self, action, message):
        self.action_active[action] = any(item.status in (1, 2, 3) for item in message.status_list)
        if self.action_active[action] and self.session.active:
            self.session.end("Another robot action owns motion; manual input ended.", "failed")

    def _on_buffer(self, topic, publisher, message, info):
        """Observe the sole native producer; a Bool alone never proves a drain."""
        if not self.session.active:
            return
        try:
            endpoints = self.node.get_publishers_info_by_topic(topic)
            stamp = info["source_timestamp"] if isinstance(info, dict) else info.source_timestamp
            age = (self.node.get_clock().now().nanoseconds - stamp) / 1e9
            valid = (len(endpoints) == 1 and endpoints[0].node_name == publisher
                     and endpoints[0].node_namespace == "/"
                     and endpoints[0].topic_type == "geometry_msgs/msg/Twist"
                     and stamp > self._drain_source.get(topic, 0)
                     and 0 <= age <= HARDWARE_TIMEOUT_S)
            if not valid:
                raise ValueError("Manual output producer is stale or ambiguous")
            self._drain_source[topic] = stamp
            values = (message.linear.x, message.linear.y, message.linear.z,
                      message.angular.x, message.angular.y, message.angular.z)
            if not all(math.isfinite(value) for value in values):
                raise ValueError("Non-finite downstream velocity")
            now = time.monotonic()
            since = self._drain.get(topic, (None, None))[0]
            if all(value == 0 for value in values):
                self._drain[topic] = (now if since is None else since, now)
            elif not self.session.acknowledged:
                self._drain[topic] = (None, now)
        except (AttributeError, KeyError, TypeError, ValueError, RuntimeError):
            self.session.end("Manual command producer changed or became stale. Start again explicitly.", "failed")

    def _path_identity(self):
        identities = []
        for topic, publisher in ((self.contract["topics"]["velocity"]["name"], "patrolbot_web_bridge"),
                                 ("/cmd_vel_out", "cmd_vel_mux"),
                                 ("/cmd_vel", "teleop_velocity_smoother")):
            try:
                endpoints = self.node.get_publishers_info_by_topic(topic)
                if (len(endpoints) != 1 or endpoints[0].node_name != publisher
                        or endpoints[0].node_namespace != "/"
                        or endpoints[0].topic_type != "geometry_msgs/msg/Twist"):
                    return None
                identities.append(bytes(endpoints[0].endpoint_gid))
            except (AttributeError, TypeError, RuntimeError):
                return None
        return tuple(identities)

    def safety(self):
        state = self.state()
        base, base_at, odom, odom_at = state
        now = time.monotonic()
        if not self.ws.connected.is_set():
            return "Dashboard connection is unavailable; manual input ended."
        if base is None or base_at is None or not 0 <= now - base_at <= HARDWARE_TIMEOUT_S:
            return "Wait for fresh robot hardware readings."
        age = base.get("telemetry_age")
        scan_age = base.get("lidar_scan_age")
        if (not base.get("link_connected") or not base.get("hardware_state_valid")
                or type(age) not in (int, float) or not math.isfinite(age)
                or not 0 <= age + (now - base_at) <= HARDWARE_TIMEOUT_S):
            return "Robot hardware data is invalid or stale."
        if (base.get("lidar_state") != "READY" or type(scan_age) not in (int, float)
                or not math.isfinite(scan_age) or scan_age < 0
                or scan_age + now - base_at > HARDWARE_TIMEOUT_S):
            return "LiDAR is unavailable or stale; manual input ended."
        if base.get("odom_epoch_valid") is not True:
            return "The raw odometry origin is not valid."
        if odom is None or odom_at is None or not 0 <= now - odom_at <= HARDWARE_TIMEOUT_S:
            return "Raw odometry is missing or stale."
        try:
            p, q, velocity = odom.pose.pose.position, odom.pose.pose.orientation, odom.twist.twist
            values = (p.x, p.y, q.x, q.y, q.z, q.w, velocity.linear.x, velocity.angular.z)
            stamp = odom.header.stamp.sec * 10**9 + odom.header.stamp.nanosec
            age = (self.node.get_clock().now().nanoseconds - stamp) / 1e9
            if not all(math.isfinite(value) for value in values) or not 0 <= age <= HARDWARE_TIMEOUT_S:
                return "Raw odometry is invalid or stale."
        except (AttributeError, TypeError, ValueError):
            return "Raw odometry is invalid."
        if base.get("motors_enabled") is not True:
            return "Motors are off; manual input does not enable them."
        if base.get("estop_pressed") is not False or base.get("fault_flags") != 0:
            return "Robot safety fault or emergency stop is active."
        if base.get("dock_state_valid") is not True or base.get("dock_state") != "CLEAR_CONFIRMED":
            return "The robot is not confirmed clear of its dock. Use guarded Undock first."
        if (base.get("bumpers_valid") is not True or base.get("bumpers_front") is not False
                or base.get("bumpers_rear") is not False):
            return "A bumper is pressed or its reading is unavailable."
        if base.get("undock_active") or any(self.action_active.values()):
            return "Another navigation, map or undock action is active. Stop it first."
        commands = getattr(self.node, "_commands", None)
        if commands is not None and any(getattr(commands, key, None) is not None for key in (
                "_active", "_nav_pending", "_map_active", "_undock_active", "_lidar_pending")):
            return "Another command owns the robot. End it before manual input."
        reset = getattr(self.node, "_system_reset", None)
        if reset is not None and reset.commands_blocked:
            return "Software reset is pending or its outcome is unknown."
        if self.manual_at is not None and 0 <= now - self.manual_at <= HARDWARE_TIMEOUT_S:
            return "The physical joystick has priority. Release it before starting web manual input."
        if self.idle_at is None or not 0 <= now - self.idle_at <= HARDWARE_TIMEOUT_S:
            return "Waiting for a fresh navigation-idle confirmation."
        if self.session.active and self._path_identity() != self._writers:
            return "Manual input topology changed; a fresh start is required."
        return None

    def handle(self, data, *, received_at):
        command = data.get("command")
        if command == "teleop_start":
            if self.session.active:
                self.session.start(data, self.context(), received_at=received_at, defer_ack=True)
                return
            self._writers = self._path_identity()
            if self._writers is None:
                self.session._ack(data.get("command_id", ""), False, "Waiting for the sole manual mux/smoother path.")
                return
            self._drain.clear()
            self._drain_source.clear()
            self.session.start(data, self.context(), received_at=received_at, defer_ack=True)
        elif command == "teleop_stop":
            command_id = data.get("command_id", "")
            if data.get("operator_authorized") is not True or not valid_id(command_id):
                self.session._ack(command_id, False, "An authorized manual-stop request is required.")
                return
            self.session.end()
            self.session._ack(command_id, True)
            self.ws.send("command.result", {"command_id": command_id, "outcome": "succeeded",
                                           "detail": "Manual input ended; zero requested."})

    def tick(self):
        now = time.monotonic()
        if self.idle_future is not None and self.idle_future.done():
            try:
                result = self.idle_future.result()
                self.idle_at = self.idle_request_at if result and result.success is True else None
            except Exception as error:
                self.idle_at = None
                self.node.get_logger().warning(f"navigation idle request failed: {error}")
            self.idle_future = None
        elif self.idle_future is not None and now - self.idle_request_at > HARDWARE_TIMEOUT_S:
            self.idle.remove_pending_request(self.idle_future)
            self.idle_future = None
            self.idle_at = None
        if self.idle_future is None and now - self.last_idle_request >= 0.1 and self.idle.service_is_ready():
            self.last_idle_request = self.idle_request_at = now
            self.idle_future = self.idle.call_async(self._Trigger.Request())
        self.session.tick(self.context())
        if self.session.active and not self.session.acknowledged:
            self._publish(0.0, 0.0, False)
            # One owner observes the complete drain before acknowledging start;
            # there are no independently timed teleop/bridge zero barriers.
            if all(topic in self._drain and self._drain[topic][0] is not None
                   and now - self._drain[topic][0] >= HARDWARE_TIMEOUT_S
                   and 0 <= now - self._drain[topic][1] <= HARDWARE_TIMEOUT_S
                   for topic in ("/cmd_vel_out", "/cmd_vel")):
                self.session.confirm_start(self.context())

    def shutdown(self):
        self.session.end("Web bridge is closing; manual input ended.", "canceled")
