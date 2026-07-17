"""Command execution for the web bridge — Phase 3, opt-in.

Only constructed when WEB_BRIDGE_ENABLE_COMMANDS=1; otherwise the node stays
strictly subscribe-only and every command.request is declined. Commands are
goal-based only (Nav2 NavigateToPose, /initialpose, goal cancel) — there is
deliberately no /cmd_vel path, and nothing here talks to the SBC.
"""
from __future__ import annotations

import logging
import math
import time
from typing import Any

log = logging.getLogger("web_bridge.commands")

DISABLED_REASON = (
    "Motion commands are disabled on the robot. An operator must set "
    "WEB_BRIDGE_ENABLE_COMMANDS=1 on the robot to allow them."
)

# action_msgs/GoalStatus terminal codes.
_STATUS_SUCCEEDED = 4
_STATUS_CANCELED = 5
_STATUS_ABORTED = 6


def yaw_to_quaternion(yaw: float) -> tuple[float, float, float, float]:
    """(x, y, z, w) for a rotation of `yaw` about +z."""
    return (0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0))


def map_goal_status(status: int) -> str:
    if status == _STATUS_SUCCEEDED:
        return "succeeded"
    if status == _STATUS_CANCELED:
        return "canceled"
    if status == _STATUS_ABORTED:
        return "failed"
    return "failed"


def precheck(command: str, goal: dict | None, base_state: Any | None) -> str | None:
    """Plain-language rejection reason, or None when the command may proceed.

    base_state is the latest normalized base_state payload (a dict) or None
    when the hardware bridge hasn't reported yet — fail closed for motion.
    """
    if command in ("navigate_to_pose", "set_initial_pose") and goal is None:
        return "This command needs a destination."
    if command == "navigate_to_pose":
        if base_state is None:
            return "The robot's drive base has not reported in yet — cannot navigate."
        if base_state.get("estop_pressed"):
            return "The emergency stop is pressed. Release it on the robot first."
        if not base_state.get("motors_enabled"):
            return "The robot's motors are off. Enable them on the robot first."
        if not base_state.get("hardware_state_valid"):
            return "The robot's drive base is not reporting valid data — cannot navigate."
    return None


class CommandExecutor:
    """Owns the Nav2 action client and /initialpose publisher.

    handle() is called from a ROS timer on the executor threads; WsClient.send
    is thread-safe, so replies can be emitted from any callback.
    """

    def __init__(self, node, ws) -> None:
        from geometry_msgs.msg import PoseWithCovarianceStamped
        from nav2_msgs.action import NavigateToPose
        from rclpy.action import ActionClient

        self._node = node
        self._ws = ws
        self._NavigateToPose = NavigateToPose
        self._PoseWithCovarianceStamped = PoseWithCovarianceStamped
        self._nav_client = ActionClient(node, NavigateToPose, "navigate_to_pose")
        self._initialpose_pub = node.create_publisher(
            PoseWithCovarianceStamped, "initialpose", 10)
        self._active: dict | None = None  # {command_id, goal_handle}
        self._last_progress_mono = 0.0
        node.get_logger().warning(
            "WEB_BRIDGE_ENABLE_COMMANDS=1 — motion commands are ENABLED")

    # -- replies ---------------------------------------------------------------

    def _ack(self, command_id: str, accepted: bool, reason: str | None = None) -> None:
        self._ws.send("command.ack", {"command_id": command_id,
                                      "accepted": accepted, "reason": reason})

    def _result(self, command_id: str, outcome: str, detail: str | None = None) -> None:
        self._ws.send("command.result", {"command_id": command_id,
                                         "outcome": outcome, "detail": detail})

    # -- dispatch --------------------------------------------------------------

    def handle(self, data: dict, base_state: dict | None, current_yaw: float | None) -> None:
        command_id = str(data.get("command_id", ""))
        command = data.get("command")
        goal = data.get("goal")

        reason = precheck(command, goal, base_state)
        if reason is not None:
            self._ack(command_id, False, reason)
            return

        if command == "navigate_to_pose":
            self._navigate(command_id, goal, current_yaw)
        elif command == "stop":
            self._stop(command_id)
        elif command == "set_initial_pose":
            self._set_initial_pose(command_id, goal)
        else:
            self._ack(command_id, False, f"Unknown command: {command}")

    # -- navigate_to_pose ------------------------------------------------------

    def _navigate(self, command_id: str, goal: dict, current_yaw: float | None) -> None:
        if not self._nav_client.server_is_ready():
            self._ack(command_id, False,
                      "The robot's navigation system is not running.")
            return

        nav_goal = self._NavigateToPose.Goal()
        nav_goal.pose.header.frame_id = "map"
        nav_goal.pose.header.stamp = self._node.get_clock().now().to_msg()
        nav_goal.pose.pose.position.x = float(goal["x"])
        nav_goal.pose.pose.position.y = float(goal["y"])
        # No requested yaw: keep the robot's current heading at the goal
        # rather than forcing a spin to zero.
        yaw = goal.get("yaw")
        if yaw is None:
            yaw = current_yaw if current_yaw is not None else 0.0
        qx, qy, qz, qw = yaw_to_quaternion(float(yaw))
        nav_goal.pose.pose.orientation.x = qx
        nav_goal.pose.pose.orientation.y = qy
        nav_goal.pose.pose.orientation.z = qz
        nav_goal.pose.pose.orientation.w = qw

        future = self._nav_client.send_goal_async(
            nav_goal, feedback_callback=lambda fb: self._on_feedback(command_id, fb))
        future.add_done_callback(lambda f: self._on_goal_response(command_id, f))

    def _on_goal_response(self, command_id: str, future) -> None:
        try:
            goal_handle = future.result()
        except Exception as exc:  # noqa: BLE001 — surfaced to the operator
            self._ack(command_id, False, f"Navigation refused the request: {exc}")
            return
        if not goal_handle.accepted:
            self._ack(command_id, False, "Navigation declined the destination.")
            return
        # Nav2 preempts a prior goal itself; close ours out for the UI.
        if self._active is not None:
            self._result(self._active["command_id"], "canceled",
                         "A newer destination replaced this one.")
        self._active = {"command_id": command_id, "goal_handle": goal_handle}
        self._ack(command_id, True)
        result_future = goal_handle.get_result_async()
        result_future.add_done_callback(lambda f: self._on_result(command_id, f))

    def _on_feedback(self, command_id: str, feedback_msg) -> None:
        now = time.monotonic()
        if now - self._last_progress_mono < 1.0:
            return
        self._last_progress_mono = now
        distance = getattr(feedback_msg.feedback, "distance_remaining", None)
        self._ws.send("command.progress", {
            "command_id": command_id, "stage": "navigating",
            "detail": "Heading to the destination",
            "distance_remaining": round(float(distance), 2) if distance is not None else None,
        })

    def _on_result(self, command_id: str, future) -> None:
        if self._active is None or self._active["command_id"] != command_id:
            return  # already superseded/canceled and reported
        self._active = None
        try:
            status = future.result().status
        except Exception as exc:  # noqa: BLE001
            self._result(command_id, "failed", f"Navigation ended abnormally: {exc}")
            return
        outcome = map_goal_status(status)
        detail = {"succeeded": "Arrived at the destination.",
                  "canceled": "Navigation was canceled.",
                  "failed": "The robot could not reach the destination."}[outcome]
        self._result(command_id, outcome, detail)

    # -- stop ------------------------------------------------------------------

    def _stop(self, command_id: str) -> None:
        self._ack(command_id, True)
        active = self._active
        if active is None:
            self._result(command_id, "succeeded", "The robot was not navigating.")
            return
        self._active = None
        self._result(active["command_id"], "canceled", "Stopped by the operator.")
        cancel_future = active["goal_handle"].cancel_goal_async()
        cancel_future.add_done_callback(
            lambda _f: self._result(command_id, "succeeded", "The robot has stopped."))

    # -- set_initial_pose ------------------------------------------------------

    def _set_initial_pose(self, command_id: str, goal: dict) -> None:
        msg = self._PoseWithCovarianceStamped()
        msg.header.frame_id = "map"
        msg.header.stamp = self._node.get_clock().now().to_msg()
        msg.pose.pose.position.x = float(goal["x"])
        msg.pose.pose.position.y = float(goal["y"])
        yaw = float(goal.get("yaw") or 0.0)
        qx, qy, qz, qw = yaw_to_quaternion(yaw)
        msg.pose.pose.orientation.x = qx
        msg.pose.pose.orientation.y = qy
        msg.pose.pose.orientation.z = qz
        msg.pose.pose.orientation.w = qw
        # Same uncertainty RViz's "2D Pose Estimate" uses.
        msg.pose.covariance[0] = 0.25
        msg.pose.covariance[7] = 0.25
        msg.pose.covariance[35] = 0.0685
        self._initialpose_pub.publish(msg)
        self._ack(command_id, True)
        self._result(command_id, "succeeded", "Robot location updated.")
