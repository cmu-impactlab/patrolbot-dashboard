"""Command execution for the web bridge — Phase 3, opt-in.

Only constructed when WEB_BRIDGE_ENABLE_COMMANDS=1; otherwise the node stays
strictly subscribe-only and every command.request is declined. Commands are
goal-based only (Nav2 NavigateToPose, /initialpose, goal cancel) plus the
guarded dock-manager operations (the /patrolbot/undock action and the
/patrolbot/charge_release and /patrolbot/motor_enable services) — there is
deliberately no /cmd_vel path, and nothing here talks to the SBC directly.

Undock is an *action on the robot*: this bridge asks for it and reports what
comes back. It never drives the robot itself, and the reverse distance and
speed come from this node's parameters rather than from the browser — the
dock manager bounds them again with its own hard caps.
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

# How stale the drive base's own reading may be before it stops being a basis
# for driving, and how long ago this node may have received it. Two separate
# questions; both match the dashboard server's thresholds in
# server/app/commands/gates.py.
MAX_TELEMETRY_AGE_S = 2.0
MAX_RECEIPT_AGE_S = 3.0
# How stale the robot's own position stream may be before navigation is
# refused. /odom arrives at ~22.7 Hz, so 3 s is roughly seventy missed samples
# — generous enough to ride out a scheduling hiccup, tight enough that a dead
# pose stream is caught. This is NOT waivable by the localization override:
# "the robot is unsure where it is" and "the robot has stopped saying where it
# is" are different failures, and only the first is the operator's to accept.
MAX_ODOM_AGE_S = 3.0

# action_msgs/GoalStatus terminal codes.
_STATUS_SUCCEEDED = 4
_STATUS_CANCELED = 5
_STATUS_ABORTED = 6

# action_msgs/CancelGoal.Response return codes.
_CANCEL_REJECTED = 1
_CANCEL_GOAL_TERMINATED = 3


def yaw_to_quaternion(yaw: float) -> tuple[float, float, float, float]:
    """(x, y, z, w) for a rotation of `yaw` about +z."""
    return (0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0))


def cancel_verdict(response: Any) -> str:
    """What a CancelGoal reply actually says: "canceling", "finished" or
    "refused".

    Only "canceling" means the server took the request on — and even then the
    robot has not stopped yet; its goal still has to reach a terminal state.
    ERROR_GOAL_TERMINATED is the one empty-list answer that is good news: the
    goal had already finished, so there was nothing left to cancel. Everything
    else — a refusal, an unknown goal id (the action server lost the goal, and
    with it any knowledge of whether the robot stopped), a malformed reply — is
    read as a refusal, because none of them is evidence the robot stopped.
    """
    if getattr(response, "goals_canceling", ()):
        return "canceling"
    if int(getattr(response, "return_code", _CANCEL_REJECTED)) == _CANCEL_GOAL_TERMINATED:
        return "finished"
    return "refused"


def stop_result(goal_outcome: str) -> tuple[str, str]:
    """Outcome and sentence for a stop, from the terminal state of the goal it
    was cancelling.

    Only a cancelled goal is a stop that did what it said. Arriving first is
    also a stopped robot, but a different story and the operator should be told
    which. An aborted goal is neither: Nav2 gave up for its own reasons, which
    is not confirmation that the robot came to rest, so the stop is reported as
    failed rather than quietly claiming success.
    """
    if goal_outcome == "canceled":
        return ("succeeded", "The robot has stopped.")
    if goal_outcome == "succeeded":
        return ("succeeded",
                "The robot reached its destination before it could be stopped.")
    return ("failed",
            "The robot's goal ended abnormally while it was being stopped — "
            "check the robot before assuming it has stopped.")


def map_goal_status(status: int) -> str:
    if status == _STATUS_SUCCEEDED:
        return "succeeded"
    if status == _STATUS_CANCELED:
        return "canceled"
    if status == _STATUS_ABORTED:
        return "failed"
    return "failed"


# patrolbot_interfaces/action/Undock result codes -> plain language. The dock
# manager returns a typed code AND a message; the code is the stable contract,
# so the operator-facing sentence is chosen from it and the manager's own
# message is appended when it adds detail.
UNDOCK_CODE_TEXT = {
    0: "The robot is clear of the dock.",
    1: "The robot refused the undock request.",
    2: "The robot's pre-move checks did not pass.",
    3: "The robot could not release its charger.",
    4: "The robot could not power its motors.",
    5: "A sensor the robot needs to back up safely is not reporting.",
    6: "The robot is not confident where it is, so it will not move.",
    7: "The robot's command path is not healthy.",
    8: "The robot started backing up but could not finish.",
    9: "Undocking was canceled.",
    10: "Undocking took too long and was stopped.",
    11: "The robot's drive base restarted mid-undock.",
    12: "The robot reported a safety fault and stopped.",
}

# Result codes shared by ChargeRelease.srv and MotorEnable.srv.
SERVICE_CODE_TEXT = {
    0: "Done.",
    1: "The robot rejected the request as invalid.",
    2: "The robot's link to its drive base is unavailable.",
    3: "The robot's pre-conditions for this were not met.",
    4: "The robot's hardware refused the request.",
    5: "The robot did not answer in time.",
    6: "The robot's drive base restarted while the request was in flight.",
    7: "The robot and the dashboard disagreed on the protocol.",
}

# normalize_base_state's charge_state strings that mean current is flowing.
# This is a legacy fallback only: a valid dock_state is authoritative because
# raw charge can re-latch after CLEAR_CONFIRMED.
CHARGING_STATES = frozenset({"charging", "charge", "bulk", "float", "overcharge"})
# ...and those that mean physically on the dock, charging or not.
ON_DOCK_STATES = CHARGING_STATES | frozenset({"docked", "dock", "on_dock"})

# Undock feedback `state` -> what to show while it runs.
UNDOCK_STAGE_TEXT = {
    "IDLE": "Preparing to undock",
    "PRECHECK": "Checking it is safe to move",
    "VERIFY_LOCALIZATION": "Confirming the robot's location",
    "VERIFY_COMMAND_PATH": "Checking the guarded motion path",
    "HARDWARE_UNDOCK": "Moving clear of the dock",
    "RESTORE_LOCALIZATION": "Refreshing the robot's location",
    "TURN_AWAY_FROM_DOCK": "Turning away from the dock",
    "STOPPED": "Stopping clear of the dock",
    "RELEASING": "Releasing the charger",
    "ENABLING_MOTORS": "Powering the motors",
    "SETTLING": "Waiting for the robot to settle",
    "MOVING": "Backing off the dock",
    "VERIFYING": "Confirming the robot is clear",
    "COMPLETE": "Clear of the dock",
}

HARDWARE_UNDOCK_STAGE_TEXT = {
    "PREPARING_UNDOCK": "Preparing the drive base",
    "RELEASING": "Releasing the charger",
    "ENABLING_MOTORS": "Powering the motors",
    "BACKING_OUT": "Backing clear of the dock",
    "VERIFYING_CLEARANCE": "Confirming physical clearance",
    "HANDOFF_READY": "Handing control back safely",
    "STOPPING": "Stopping the drive base",
}


def undock_detail(code: int, message: str, manual_recovery: bool = False) -> str:
    """Operator-facing sentence for an Undock result."""
    text = UNDOCK_CODE_TEXT.get(int(code), "Undocking did not complete.")
    if message and message.strip() and int(code) != 0:
        text = f"{text} ({message.strip()})"
    if manual_recovery:
        text += " Someone needs to check the robot at the dock before trying again."
    return text


def undock_outcome(success: bool, code: int) -> str:
    """Map an Undock result onto the dashboard's command outcomes."""
    if success and int(code) == 0:
        return "succeeded"
    if int(code) == 9:
        return "canceled"
    if int(code) == 10:
        return "timeout"
    return "failed"


def service_detail(code: int, message: str) -> str:
    text = SERVICE_CODE_TEXT.get(int(code), "The request did not complete.")
    if message and message.strip() and int(code) != 0:
        text = f"{text} ({message.strip()})"
    return text


def undock_stage(state: str, hardware_phase: str = "") -> str:
    if (state or "").upper() == "HARDWARE_UNDOCK" and hardware_phase:
        return HARDWARE_UNDOCK_STAGE_TEXT.get(
            hardware_phase.upper(), hardware_phase)
    return UNDOCK_STAGE_TEXT.get((state or "").upper(), state or "Undocking")


def _on_dock(base_state: dict) -> bool:
    """Is the robot on its charger? Mirrors StateFacts.on_dock on the server.

    A *usable* dock observer is authoritative in both directions: raw
    charge_state can re-latch after a proven departure, so CLEAR_CONFIRMED
    stays clear. An observer reporting itself invalid answers nothing and falls
    back to charge_state — reading it as "not docked" would clear the dock for
    a robot sitting on its charger with a broken observer.
    """
    if base_state.get("dock_state_valid"):
        return base_state.get("dock_state", "").strip().upper() == "DOCKED_CONFIRMED"
    return base_state.get("charge_state", "").strip().lower() in ON_DOCK_STATES


def precheck(command: str, goal: dict | None, base_state: Any | None,
             localized: bool = False, base_state_age: float | None = None,
             allow_unlocalized: bool = False,
             odom_age: float | None = None,
             operator_authorized: bool = False) -> str | None:
    """Plain-language rejection reason, or None when the command may proceed.

    base_state is the latest normalized base_state payload (a dict) or None
    when the hardware bridge hasn't reported yet — fail closed for motion.
    `localized` is the node's own answer to "is the map-frame fix recent and
    confident" (BridgeNode._localization_is_usable); it defaults to False so a
    caller that cannot answer refuses rather than assumes. `base_state_age` is
    how long ago this node received that payload, which is a different question
    from the telemetry_age inside it: if the drive-base subscription dies, the
    last payload keeps reporting whatever age it had when it was sent, forever.

    These repeat the dashboard server's gates (server/app/commands/gates.py)
    deliberately, in the same words. The server refusing first is the normal
    case; this is what holds if a frame reaches the robot without having passed
    it — a replayed command.request, or a server that trusted stale telemetry.
    """
    if command in ("navigate_to_pose", "set_initial_pose") and goal is None:
        return "This command needs a destination."
    if command == "navigate_to_pose":
        if base_state is None:
            return "The robot's drive base has not reported in yet — cannot navigate."
        if (not base_state.get("link_connected")
                or not 0.0 <= float(base_state.get("telemetry_age", 999.0))
                <= MAX_TELEMETRY_AGE_S
                or base_state_age is None
                or base_state_age > MAX_RECEIPT_AGE_S):
            # The drive base link is down, its last reading is old, or it
            # stopped reporting to us altogether. Either way what follows would
            # be judged from numbers that no longer describe the robot.
            return ("The robot's hardware readings are stale — wait for fresh "
                    "data before commanding the robot.")
        if not base_state.get("hardware_state_valid"):
            return "The robot's drive base is not reporting valid data — cannot navigate."
        if base_state.get("fault_flags"):
            return (f"The drive base is reporting a fault "
                    f"(code {int(base_state['fault_flags'])}). "
                    "Clear it on the robot first.")
        if base_state.get("estop_pressed"):
            return "The emergency stop is pressed. Release it on the robot first."
        # Coming off the dock is undock's job — it releases the charger and
        # backs off under the dock manager's guarded profile. Checked before
        # the motors because a hand-docked robot can charge with its motors
        # still reported enabled, which would otherwise look ready to drive.
        # It also names the real cause: on 2026-07-28 an operator hit "Enable
        # motors", saw it succeed, and was told a second later to enable the
        # motors — the charger had re-latched and tripped the interlock, and
        # they went round that loop five times.
        if _on_dock(base_state):
            return ("The robot is on its charger. Move it off the dock before "
                    "sending it anywhere.")
        if not base_state.get("motors_enabled"):
            return "The robot's motors are off. Enable them on the robot first."
        # An exceptional flag needs the server's verified-role stamp, the same
        # way the guarded undock does. A browser cannot authorize itself past a
        # safety gate; anything arriving here unstamped is treated as if the
        # override had not been asked for.
        override = allow_unlocalized and operator_authorized
        # Bound to the override only, so the ordinary path keeps exactly the
        # behaviour it had. On that path `localized` already implies a recent
        # AMCL pose; the override is precisely what removes that implication,
        # and would otherwise leave the bridge with no pose-recency check at
        # all. "Unsure where it is" and "no longer saying where it is" are
        # different failures, and only the first is the operator's to accept.
        # This matters because the bridge is the barrier that has to hold when
        # a crafted or replayed frame arrives without passing the server.
        if override and (odom_age is None or odom_age > MAX_ODOM_AGE_S):
            return ("The robot has stopped reporting its position — wait for "
                    "fresh data before sending it anywhere.")
        if not localized and not override:
            return "The robot does not know where it is. Set its location first."
        # RViz has always been able to send a goal on a bad fix -- Nav2 has no
        # localization gate of its own -- so the override restores parity
        # rather than granting anything new. Every check above still applies.
    return None


class CommandExecutor:
    """Owns the Nav2 action client and /initialpose publisher.

    handle() is called from a ROS timer on the executor threads; WsClient.send
    is thread-safe, so replies can be emitted from any callback.
    """

    def __init__(self, node, ws, undock_config: dict | None = None) -> None:
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

        # Dock-manager interfaces. Optional: a robot without the dock manager
        # (or without patrolbot_interfaces built) simply never advertises the
        # capability, and the dashboard keeps the control greyed out.
        self._undock_config = undock_config or {}
        self._undock_client = None
        self._release_client = None
        self._motor_client = None
        self._undock_active: dict | None = None  # {command_id, goal_handle, started}
        self._last_undock_progress_mono = 0.0
        self._undock_server_misses = 0
        # Longer than the dock manager's own per-state timeouts, so this only
        # ever fires for a goal nobody is going to answer.
        self._undock_timeout_s = float(
            (undock_config or {}).get("timeout_s", 180.0))
        self._dock_pose_client = None
        try:
            from patrolbot_interfaces.action import Undock
            from patrolbot_interfaces.srv import (
                ChargeRelease, InitializeDockPose, MotorEnable,
            )
        except ImportError:
            node.get_logger().warning(
                "patrolbot_interfaces not available — dock commands disabled")
            return
        self._Undock = Undock
        self._ChargeRelease = ChargeRelease
        self._MotorEnable = MotorEnable
        self._InitializeDockPose = InitializeDockPose
        self._undock_client = ActionClient(node, Undock, "/patrolbot/undock")
        self._release_client = node.create_client(
            ChargeRelease, "/patrolbot/charge_release")
        self._motor_client = node.create_client(MotorEnable, "/patrolbot/motor_enable")
        self._dock_pose_client = node.create_client(
            InitializeDockPose, "/patrolbot/initialize_dock_pose")

    # -- dock-pose seeding -----------------------------------------------------

    def initialize_dock_pose(self, dock_id: str) -> bool:
        """Ask the dock manager to seed localization from its dock pose.

        Fire and forget: the manager refuses unless it can confirm the robot
        is actually docked and stationary, so a wrong guess cannot move or
        mislocate anything. Returns whether the request was sent at all.
        """
        if self._dock_pose_client is None or not self._dock_pose_client.service_is_ready():
            return False
        request = self._InitializeDockPose.Request()
        request.dock_id = dock_id
        request.operator_authorized = True
        future = self._dock_pose_client.call_async(request)
        future.add_done_callback(self._on_dock_pose_result)
        return True

    def _on_dock_pose_result(self, future) -> None:
        try:
            response = future.result()
        except Exception as exc:  # noqa: BLE001
            self._node.get_logger().warning(f"dock pose init failed: {exc}")
            return
        if response.success:
            self._node.get_logger().info(
                f"localization seeded from dock pose ({response.message})")
        else:
            self._node.get_logger().warning(
                f"dock pose init refused: {response.message}")

    # -- undock watchdog -------------------------------------------------------

    def check_undock_health(self) -> None:
        """Close out an undock whose action server died holding the goal.

        A crashed dock manager never resolves the result future, so without
        this `_undock_active` stays set forever and every later attempt is
        refused with "the robot is already undocking" — the bridge wedged
        until someone restarts the container. The dashboard's own 120 s
        timeout closes the *command*; only this clears the bridge's state.

        Two consecutive misses before declaring, because the server briefly
        reads unavailable while launch respawns it.
        """
        active = self._undock_active
        if active is None:
            self._undock_server_misses = 0
            return
        if self._undock_client is not None and not self._undock_client.server_is_ready():
            self._undock_server_misses += 1
            if self._undock_server_misses >= 2:
                self._fail_undock(
                    "The robot's docking system stopped responding mid-undock. "
                    "The robot has not moved — check the dock manager on the robot.")
            return
        self._undock_server_misses = 0
        if time.monotonic() - active["started"] > self._undock_timeout_s:
            self._fail_undock("Undocking did not finish in time and was abandoned.")

    def _fail_undock(self, detail: str) -> None:
        active = self._undock_active
        if active is None:
            return
        self._undock_active = None
        self._undock_server_misses = 0
        self._node.get_logger().warning(f"undock closed out: {detail}")
        self._result(active["command_id"], "failed", detail)
        for stop_command_id in self._claim_all_stops(active):
            self._result(stop_command_id, "failed",
                         "The robot stopped answering while being stopped. It may "
                         "still be moving — use the emergency stop on the robot.")

    # -- capability advertisement ----------------------------------------------

    def available_capabilities(self) -> list[str]:
        """Dock capabilities whose server is actually reachable right now.

        The dashboard offers a control only for a capability the robot claims,
        so this is the commissioning switch: nothing is advertised until the
        dock manager is up. `dock` is deliberately absent, and is no longer a
        command at all — the robot has no dock-in path to advertise.
        """
        capabilities: list[str] = []
        if self._undock_client is not None and self._undock_client.server_is_ready():
            capabilities.append("undock")
        if self._release_client is not None and self._release_client.service_is_ready():
            capabilities.append("charge_release")
        if self._motor_client is not None and self._motor_client.service_is_ready():
            capabilities.append("motor_enable")
        return capabilities

    # -- replies ---------------------------------------------------------------

    def _ack(self, command_id: str, accepted: bool, reason: str | None = None) -> None:
        self._ws.send("command.ack", {"command_id": command_id,
                                      "accepted": accepted, "reason": reason})

    def _result(self, command_id: str, outcome: str, detail: str | None = None) -> None:
        self._ws.send("command.result", {"command_id": command_id,
                                         "outcome": outcome, "detail": detail})

    # -- dispatch --------------------------------------------------------------

    def handle(self, data: dict, base_state: dict | None, current_yaw: float | None,
               localized: bool = False, base_state_age: float | None = None,
               odom_age: float | None = None) -> None:
        command_id = str(data.get("command_id", ""))
        command = data.get("command")
        goal = data.get("goal")

        allow_unlocalized = bool(data.get("allow_unlocalized", False))
        operator_authorized = bool(data.get("operator_authorized", False))
        reason = precheck(command, goal, base_state, localized, base_state_age,
                          allow_unlocalized, odom_age, operator_authorized)
        if reason is not None:
            self._ack(command_id, False, reason)
            return
        # Logged only once the command is actually going ahead, so a goal
        # refused for some other reason leaves no misleading override record
        # behind. A suppressed safety gate has to be visible in the log.
        if (allow_unlocalized and operator_authorized
                and command == "navigate_to_pose" and not localized):
            self._node.get_logger().warning(
                "LOCALIZATION GATE OVERRIDDEN by operator: navigating with an "
                "unusable map-frame fix (command %s)", command_id)

        if command == "navigate_to_pose":
            self._navigate(command_id, goal, current_yaw)
        elif command == "stop":
            self._stop(command_id)
        elif command == "set_initial_pose":
            self._set_initial_pose(command_id, goal)
        elif command == "undock":
            # operator_authorized is set by the dashboard server from the
            # verified session role — never by the browser (see
            # server/app/commands/broker.py). Absent means unauthorized.
            self._undock(command_id, bool(data.get("operator_authorized", False)))
        elif command == "charge_release":
            self._charge_release(command_id)
        elif command == "motor_enable":
            self._motor_enable(command_id)
        else:
            self._ack(command_id, False, f"Unknown command: {command}")

    # -- navigate_to_pose ------------------------------------------------------

    def _navigate(self, command_id: str, goal: dict, current_yaw: float | None) -> None:
        if self._undock_active is not None:
            # Two motion owners at once is a state nothing downstream handles —
            # a stop would cancel one of them and report on the other.
            self._ack(command_id, False,
                      "The robot is moving off its dock. Wait for that to finish "
                      "before sending it somewhere.")
            return
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
            # Any stop still waiting on that goal has lost its subject, and the
            # robot is now driving to a new one rather than standing still.
            for stop_command_id in self._claim_all_stops(self._active):
                self._result(stop_command_id, "failed",
                             "A new destination replaced the goal being stopped. "
                             "The robot is moving again — use the emergency stop "
                             "on the robot.")
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
        stops = self._claim_all_stops(self._active)
        self._active = None
        try:
            status = future.result().status
        except Exception as exc:  # noqa: BLE001
            self._result(command_id, "failed", f"Navigation ended abnormally: {exc}")
            outcome = "failed"
        else:
            outcome = map_goal_status(status)
            self._result(command_id, outcome,
                         {"succeeded": "Arrived at the destination.",
                          "canceled": "Navigation was canceled.",
                          "failed": "The robot could not reach the destination."}[outcome])
        # The goal is terminal, so Nav2 is no longer driving the robot — only
        # now can anything true be said about the robot having stopped.
        for stop_command_id in stops:
            self._result(stop_command_id, *stop_result(outcome))

    # -- stop ------------------------------------------------------------------

    def _stop(self, command_id: str) -> None:
        """Ask the running action to cancel, and say nothing about the robot
        having stopped until it actually has.

        The cancel request completing only means Nav2 (or the dock manager)
        answered. Reporting success there told the operator "the robot has
        stopped" for a cancellation the server had rejected, threw the goal
        away, and then ignored its result — while the robot kept driving.
        """
        self._ack(command_id, True)
        # An undock in progress is the thing to stop; otherwise the goal. The
        # two are mutually exclusive — _navigate refuses to start while an
        # undock is running, and _undock while one is.
        active = self._undock_active or self._active
        if active is None:
            self._result(command_id, "succeeded", "The robot was not navigating.")
            return
        stops = active.setdefault("stop_command_ids", [])
        already_cancelling = bool(stops)
        stops.append(command_id)
        if already_cancelling:
            # A cancel is already in flight for this goal. Asking again would
            # get ERROR_GOAL_TERMINATED — which the server returns for a goal
            # that is merely CANCELING, not only for one that has finished — and
            # this stop would report "already stopped" while the robot is still
            # coming to a halt. Both stops are reported off the goal's terminal
            # result instead.
            return
        try:
            cancel_future = active["goal_handle"].cancel_goal_async()
        except Exception as exc:  # noqa: BLE001 — surfaced to the operator
            self._fail_stop(command_id,
                            f"The robot could not be asked to stop: {exc}.")
            return
        cancel_future.add_done_callback(
            lambda f: self._on_cancel_response(command_id, f))

    def _on_cancel_response(self, command_id: str, future) -> None:
        try:
            verdict = cancel_verdict(future.result())
        except Exception as exc:  # noqa: BLE001 — surfaced to the operator
            self._fail_stop(command_id,
                            f"The robot did not answer the stop request: {exc}.")
            return
        if verdict == "canceling":
            return  # Reported once the goal reaches a terminal state.
        if verdict == "finished" and self._claim_stop(command_id):
            self._result(command_id, "succeeded", "The robot had already stopped.")
            return
        if verdict != "finished":
            self._fail_stop(command_id, "The robot refused the stop request.")

    def _fail_stop(self, command_id: str, detail: str) -> None:
        """A stop that did not take. The goal is left alone — it is still the
        robot's active goal — and the operator is pointed at the one control
        that does not depend on this path."""
        if not self._claim_stop(command_id):
            return
        self._result(command_id, "failed",
                     f"{detail} It may still be moving — use the emergency "
                     "stop on the robot.")

    def _claim_stop(self, command_id: str) -> bool:
        """Claim the right to report this stop, if nothing has yet.

        A stop can be resolved from either end — the cancel reply or the goal's
        terminal result, whichever arrives first — so both paths claim before
        reporting and the loser stays quiet.
        """
        for active in (self._active, self._undock_active):
            if active is not None and command_id in active.get("stop_command_ids", ()):
                active["stop_command_ids"].remove(command_id)
                return True
        return False

    def _claim_all_stops(self, active: dict | None) -> list[str]:
        """Claim every stop still waiting on a goal that has just ended."""
        return [] if active is None else active.pop("stop_command_ids", [])

    # -- undock ----------------------------------------------------------------

    def _undock(self, command_id: str, operator_authorized: bool) -> None:
        if self._undock_client is None:
            self._ack(command_id, False,
                      "This robot does not have undocking commissioned.")
            return
        if not operator_authorized:
            # Belt and braces: the server already refuses observers. If an
            # unauthorized request somehow reaches the robot, it stops here
            # rather than being passed to the dock manager as authorized.
            self._ack(command_id, False,
                      "Your account is not authorized to move the robot.")
            return
        if not self._undock_client.server_is_ready():
            self._ack(command_id, False,
                      "The robot's docking system is not running.")
            return
        if self._undock_active is not None:
            self._ack(command_id, False, "The robot is already undocking.")
            return
        if self._active is not None:
            # The other half of _navigate's guard: never two motion owners.
            self._ack(command_id, False,
                      "The robot is driving to a destination. Stop it before "
                      "backing it off the dock.")
            return

        goal = self._Undock.Goal()
        goal.dock_id = str(self._undock_config.get("dock_id", ""))
        goal.reverse_distance = float(self._undock_config.get("reverse_distance", 0.5))
        goal.reverse_speed = float(self._undock_config.get("reverse_speed", 0.1))
        goal.validation_mode = bool(self._undock_config.get("validation_mode", False))
        goal.operator_authorized = True

        future = self._undock_client.send_goal_async(
            goal, feedback_callback=lambda fb: self._on_undock_feedback(command_id, fb))
        future.add_done_callback(lambda f: self._on_undock_response(command_id, f))

    def _on_undock_response(self, command_id: str, future) -> None:
        try:
            goal_handle = future.result()
        except Exception as exc:  # noqa: BLE001 — surfaced to the operator
            self._ack(command_id, False, f"The robot refused the undock request: {exc}")
            return
        if not goal_handle.accepted:
            # A rejected ROS action goal carries no message, and the dock
            # manager's real reason (unknown dock id, out-of-range distance or
            # speed, another goal active) only reaches its own log. Echo the
            # goal we sent so the mismatch is diagnosable from the dashboard
            # alone rather than requiring someone to SSH into the robot.
            config = self._undock_config
            self._ack(command_id, False,
                      "The robot declined to undock — it did not accept the request "
                      f"(dock “{config.get('dock_id', '')}”, "
                      f"{float(config.get('reverse_distance', 0)):.2f} m at "
                      f"{float(config.get('reverse_speed', 0)):.2f} m/s"
                      f"{', validation mode' if config.get('validation_mode') else ''}). "
                      "The robot's dock manager log has the exact reason.")
            return
        self._undock_active = {"command_id": command_id, "goal_handle": goal_handle,
                               "started": time.monotonic()}
        self._undock_server_misses = 0
        self._ack(command_id, True)
        result_future = goal_handle.get_result_async()
        result_future.add_done_callback(lambda f: self._on_undock_result(command_id, f))

    def _on_undock_feedback(self, command_id: str, feedback_msg) -> None:
        now = time.monotonic()
        if now - self._last_undock_progress_mono < 1.0:
            return
        self._last_undock_progress_mono = now
        feedback = feedback_msg.feedback
        state = getattr(feedback, "state", "")
        hardware_phase = getattr(feedback, "hardware_phase", "")
        self._ws.send("command.progress", {
            "command_id": command_id,
            "stage": state or "undocking",
            "detail": undock_stage(state, hardware_phase),
            "distance_remaining": None,
        })

    def _on_undock_result(self, command_id: str, future) -> None:
        if self._undock_active is None or self._undock_active["command_id"] != command_id:
            return
        stops = self._claim_all_stops(self._undock_active)
        self._undock_active = None
        try:
            result = future.result().result
        except Exception as exc:  # noqa: BLE001
            self._result(command_id, "failed", f"Undocking ended abnormally: {exc}")
            outcome = "failed"
        else:
            code = int(getattr(result, "code", 1))
            outcome = undock_outcome(bool(result.success), code)
            self._result(command_id, outcome, undock_detail(
                code, getattr(result, "message", ""),
                bool(getattr(result, "manual_recovery_required", False))))
        for stop_command_id in stops:
            self._result(stop_command_id, *stop_result(outcome))

    # -- charge_release / motor_enable -----------------------------------------

    def _charge_release(self, command_id: str) -> None:
        self._call_dock_service(command_id, self._release_client, self._ChargeRelease,
                                "release charging")

    def _motor_enable(self, command_id: str) -> None:
        self._call_dock_service(command_id, self._motor_client, self._MotorEnable,
                                "turn the motors on")

    def _call_dock_service(self, command_id: str, client, srv_type, what: str) -> None:
        """Both services share a shape: a caller-generated request_id whose
        reuse replays the original result, and a typed response code. Passing
        the dashboard's command_id straight through as request_id makes a
        replayed frame idempotent on the robot, not just at the server."""
        if client is None:
            self._ack(command_id, False, f"This robot cannot {what} from the dashboard.")
            return
        if not client.service_is_ready():
            self._ack(command_id, False, "The robot's drive-base service is not running.")
            return
        request = srv_type.Request()
        request.request_id = command_id
        future = client.call_async(request)
        future.add_done_callback(lambda f: self._on_dock_service_result(command_id, f))

    def _on_dock_service_result(self, command_id: str, future) -> None:
        try:
            response = future.result()
        except Exception as exc:  # noqa: BLE001
            self._ack(command_id, False, f"The robot did not answer: {exc}")
            return
        if not getattr(response, "accepted", False):
            self._ack(command_id, False,
                      service_detail(int(getattr(response, "code", 1)),
                                     getattr(response, "message", "")))
            return
        self._ack(command_id, True)
        code = int(getattr(response, "code", 0))
        self._result(command_id, "succeeded" if response.success else "failed",
                     service_detail(code, getattr(response, "message", "")))

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
