"""Pure-logic tests for the command path (no ROS graph needed)."""
import math
from types import SimpleNamespace

from patrolbot_web_bridge.commands import (
    CommandExecutor,
    cancel_verdict,
    localization_epoch_ready,
    map_goal_status,
    precheck,
    yaw_to_quaternion,
)


def test_yaw_to_quaternion_round_trip():
    for yaw in (0.0, math.pi / 2, -math.pi / 2, math.pi - 0.01):
        x, y, z, w = yaw_to_quaternion(yaw)
        assert x == 0.0 and y == 0.0
        recovered = 2.0 * math.atan2(z, w)
        assert abs(recovered - yaw) < 1e-9
        assert abs(x * x + y * y + z * z + w * w - 1.0) < 1e-9


def test_goal_status_mapping():
    assert map_goal_status(4) == "succeeded"
    assert map_goal_status(5) == "canceled"
    assert map_goal_status(6) == "failed"
    assert map_goal_status(0) == "failed"  # unknown codes fail closed


GOOD_BASE = {"estop_pressed": False, "motors_enabled": True,
             "hardware_state_valid": True, "link_connected": True,
             "telemetry_age": 0.1, "fault_flags": 0,
             "odom_epoch_valid": True,
             "localization_recovery_required": False,
             "localization_seed_stamp_ns": 100}


def nav(base_state=GOOD_BASE, localized=True, base_state_age=0.2):
    """A navigate request that would be allowed; each test spoils one thing."""
    return precheck("navigate_to_pose", {"x": 1, "y": 2}, base_state, localized,
                    base_state_age)


def test_precheck_allows_healthy_navigate():
    assert nav() is None


def test_precheck_requires_goal():
    assert precheck("navigate_to_pose", None, GOOD_BASE, True, 0.2) is not None
    assert precheck("set_initial_pose", None, GOOD_BASE) is not None
    assert precheck("stop", None, GOOD_BASE) is None


def test_precheck_fails_closed_without_base_state():
    assert nav(None) is not None
    # stop and set_initial_pose don't move the robot — allowed regardless.
    assert precheck("stop", None, None) is None
    assert precheck("set_initial_pose", {"x": 1, "y": 2}, None) is None


def test_precheck_refuses_navigate_without_localization():
    """The robot's own answer, not the browser's: the dashboard's "location set
    this session" flag lives in a tab and never reaches here."""
    reason = nav(localized=False)
    assert reason is not None and "where it is" in reason
    # Default is fail-closed — a caller that cannot answer does not get a pass.
    assert precheck("navigate_to_pose", {"x": 1, "y": 2}, GOOD_BASE) is not None


def test_precheck_epoch_and_recovery_gates_run_before_override():
    goal = {"x": 1, "y": 2}
    for spoiled in (
        dict(GOOD_BASE, odom_epoch_valid=False),
        dict(GOOD_BASE, localization_recovery_required=True,
             localization_recovery_stage="WAIT_FOR_AMCL"),
        dict(GOOD_BASE, localization_seed_stamp_ns=0),
    ):
        reason = precheck("navigate_to_pose", goal, spoiled, localized=False,
                          base_state_age=0.1, allow_unlocalized=True,
                          odom_age=0.1, operator_authorized=True)
        assert reason is not None
        assert "localization" in reason.lower() or "odometry" in reason.lower()


def test_localization_epoch_requires_fresh_post_seed_amcl_stamp():
    base = dict(GOOD_BASE)
    assert localization_epoch_ready(base, 101, 0.1)
    for stamp in (100, 99, 0):
        assert not localization_epoch_ready(base, stamp, 0.1)
    assert not localization_epoch_ready(dict(base, localization_seed_stamp_ns=0),
                                        101, 0.1)
    assert not localization_epoch_ready(dict(base, odom_epoch_valid=False),
                                        101, 0.1)
    assert not localization_epoch_ready(
        dict(base, localization_recovery_required=True), 101, 0.1)
    assert not localization_epoch_ready(base, 101, None)


def test_set_initial_pose_keeps_existing_independent_guards():
    # A pose seed is the recovery mechanism and remains allowed even when the
    # current epoch is invalid or recovery is requested.
    stale = dict(GOOD_BASE, odom_epoch_valid=False,
                 localization_recovery_required=True,
                 localization_seed_stamp_ns=0)
    assert precheck("set_initial_pose", {"x": 1, "y": 2}, stale,
                    localized=False, base_state_age=99.0) is None


def test_precheck_refuses_navigate_on_stale_or_faulted_base():
    """motors_enabled/hardware_state_valid keep their last true values when the
    drive base link drops, so those alone are not evidence of a healthy base."""
    for spoiled in (dict(GOOD_BASE, link_connected=False),
                    dict(GOOD_BASE, telemetry_age=5.0)):
        reason = nav(spoiled)
        assert reason is not None and "stale" in reason

    reason = nav(dict(GOOD_BASE, fault_flags=8))
    assert reason is not None and "fault (code 8)" in reason


def test_precheck_refuses_navigate_when_the_base_stopped_reporting():
    """telemetry_age is a number inside the last payload: if the drive-base
    subscription dies, it keeps saying 0.1 forever. Only this node's own
    receipt time notices, exactly as the dashboard server's gate does."""
    for age in (9.0, None):
        reason = nav(base_state_age=age)
        assert reason is not None and "stale" in reason
    # ...and a payload claiming a negative age is a broken clock, not a fresher
    # reading.
    assert nav(dict(GOOD_BASE, telemetry_age=-5.0)) is not None


def test_precheck_refuses_navigate_on_the_dock():
    """Coming off the dock is undock's job. A hand-docked robot charges with
    its motors still enabled, which passes every other check."""
    for docked in (dict(GOOD_BASE, charge_state="charging"),
                   dict(GOOD_BASE, charge_state="docked"),
                   dict(GOOD_BASE, charge_state="idle",
                        dock_state="DOCKED_CONFIRMED", dock_state_valid=True),
                   # An unusable dock observer says nothing about the dock. It
                   # must not overrule a charge_state reporting current flowing
                   # in — the normalizer emits exactly this when the observer
                   # is absent or invalid while the robot is on charge.
                   dict(GOOD_BASE, charge_state="charging",
                        dock_state="UNKNOWN", dock_state_valid=False)):
        reason = nav(docked)
        assert reason is not None and "on its charger" in reason.lower()


def test_precheck_names_the_charger_when_it_cut_the_motors():
    """The base cuts motor power while the charger is engaged. Saying "enable
    the motors" to an operator who has just enabled them sent them round that
    loop five times on 2026-07-28."""
    on_charge = dict(GOOD_BASE, motors_enabled=False, charge_state="charging")
    reason = nav(on_charge)
    assert reason is not None and "charger" in reason.lower()
    assert "enable" not in reason.lower()

    off_charge = dict(GOOD_BASE, motors_enabled=False, charge_state="not_charging")
    reason = nav(off_charge)
    assert reason is not None and "motors are off" in reason.lower()


def test_precheck_trusts_clear_dock_observer_over_stale_charge():
    clear = dict(
        GOOD_BASE,
        motors_enabled=False,
        charge_state="float",
        dock_state="CLEAR_CONFIRMED",
        dock_state_valid=True,
    )
    reason = nav(clear)
    assert reason is not None and "motors are off" in reason.lower()
    assert "charger is engaged" not in reason.lower()


def test_precheck_blocks_estop_and_motors_off():
    estop = dict(GOOD_BASE, estop_pressed=True)
    motors_off = dict(GOOD_BASE, motors_enabled=False)
    invalid = dict(GOOD_BASE, hardware_state_valid=False)
    for base_state in (estop, motors_off, invalid):
        reason = nav(base_state)
        assert reason is not None
        # Plain language, no ROS jargon.
        assert "/" not in reason and "_" not in reason


# -- stop / cancellation ----------------------------------------------------

def test_cancel_verdict_reads_the_reply_not_just_its_arrival():
    """A CancelGoal reply arriving says nothing on its own. Only a non-empty
    goals_canceling means this server took the cancellation on."""
    assert cancel_verdict(SimpleNamespace(goals_canceling=[object()],
                                          return_code=0)) == "canceling"
    # Nothing left to cancel: the goal had already finished.
    assert cancel_verdict(SimpleNamespace(goals_canceling=[],
                                          return_code=3)) == "finished"
    # An unknown goal id means the action server lost the goal, and with it
    # any knowledge of whether the robot stopped — not a stop.
    assert cancel_verdict(SimpleNamespace(goals_canceling=[],
                                          return_code=2)) == "refused"
    # Refused, and a malformed reply is read as a refusal rather than a stop.
    assert cancel_verdict(SimpleNamespace(goals_canceling=[],
                                          return_code=1)) == "refused"
    assert cancel_verdict(SimpleNamespace()) == "refused"


class _Ws:
    def __init__(self):
        self.sent = []

    def send(self, kind, data):
        self.sent.append((kind, data))

    def results(self, command_id):
        return [d for kind, d in self.sent
                if kind == "command.result" and d["command_id"] == command_id]


class _Future:
    """rclpy calls a done-callback immediately on an already-resolved future."""

    def __init__(self, value=None, error=None):
        self._value, self._error = value, error

    def result(self):
        if self._error is not None:
            raise self._error
        return self._value

    def add_done_callback(self, callback):
        callback(self)


class _GoalHandle:
    def __init__(self, cancel_response):
        self._cancel_response = cancel_response

    def cancel_goal_async(self):
        return _Future(self._cancel_response)


def _executor(cancel_response, undocking=False):
    """A CommandExecutor without its ROS graph — __init__ builds action clients
    and publishers, and none of the stop path needs them."""
    executor = CommandExecutor.__new__(CommandExecutor)
    executor._ws = _Ws()
    executor._active = None
    executor._undock_active = None
    slot = {"command_id": "goal-1", "goal_handle": _GoalHandle(cancel_response)}
    if undocking:
        executor._undock_active = slot
    else:
        executor._active = slot
    return executor


def _accepted():
    return SimpleNamespace(goals_canceling=[object()], return_code=0)


def test_stop_says_nothing_until_the_goal_is_terminal():
    """The whole finding: an accepted cancellation is Nav2 answering, not the
    robot stopping."""
    executor = _executor(_accepted())
    executor._stop("stop-1")
    assert executor._ws.results("stop-1") == []
    # The goal is still ours until it ends — it was previously discarded here,
    # so its eventual result was ignored while the robot may still be moving.
    assert executor._active is not None

    executor._on_result("goal-1", _Future(SimpleNamespace(status=5)))
    assert executor._ws.results("goal-1")[0]["outcome"] == "canceled"
    stop = executor._ws.results("stop-1")[0]
    assert stop["outcome"] == "succeeded" and stop["detail"] == "The robot has stopped."


def test_refused_cancellation_is_not_a_stop():
    executor = _executor(SimpleNamespace(goals_canceling=[], return_code=1))
    executor._stop("stop-1")
    stop = executor._ws.results("stop-1")[0]
    assert stop["outcome"] == "failed"
    assert "emergency stop" in stop["detail"]
    # The goal was refused a cancellation, so it is still the robot's goal.
    assert executor._active is not None
    # ...and when it does end, the stop is not reported a second time.
    executor._on_result("goal-1", _Future(SimpleNamespace(status=4)))
    assert len(executor._ws.results("stop-1")) == 1


def test_stop_on_an_already_finished_goal_succeeds():
    executor = _executor(SimpleNamespace(goals_canceling=[], return_code=3))
    executor._stop("stop-1")
    stop = executor._ws.results("stop-1")[0]
    assert stop["outcome"] == "succeeded" and "already stopped" in stop["detail"]


def test_unanswered_cancellation_is_reported_as_a_failed_stop():
    executor = _executor(_accepted())
    executor._active["goal_handle"].cancel_goal_async = (
        lambda: _Future(error=RuntimeError("action server gone")))
    executor._stop("stop-1")
    stop = executor._ws.results("stop-1")[0]
    assert stop["outcome"] == "failed" and "may still be moving" in stop["detail"]


def test_arriving_before_the_stop_lands_says_so():
    executor = _executor(_accepted())
    executor._stop("stop-1")
    executor._on_result("goal-1", _Future(SimpleNamespace(status=4)))
    stop = executor._ws.results("stop-1")[0]
    assert stop["outcome"] == "succeeded"
    assert "reached its destination" in stop["detail"]


def test_stopping_an_undock_waits_for_the_dock_manager():
    executor = _executor(_accepted(), undocking=True)
    executor._stop("stop-1")
    assert executor._ws.results("stop-1") == []

    executor._on_undock_result("goal-1", _Future(SimpleNamespace(
        result=SimpleNamespace(success=False, code=9, message=""))))
    assert executor._ws.results("goal-1")[0]["outcome"] == "canceled"
    assert executor._ws.results("stop-1")[0]["outcome"] == "succeeded"


def test_stop_with_nothing_running_is_immediate():
    executor = _executor(_accepted())
    executor._active = None
    executor._stop("stop-1")
    assert executor._ws.results("stop-1")[0]["outcome"] == "succeeded"


def test_an_aborted_goal_is_not_a_confirmed_stop():
    """Nav2 giving up for its own reasons is not the robot reporting that it
    came to rest, so the stop is not quietly called a success."""
    executor = _executor(_accepted())
    executor._stop("stop-1")
    executor._on_result("goal-1", _Future(SimpleNamespace(status=6)))
    stop = executor._ws.results("stop-1")[0]
    assert stop["outcome"] == "failed" and "check the robot" in stop["detail"]


def test_every_stop_is_reported_exactly_once():
    """An operator pressing Stop twice must not leave the first press hanging
    until the server times it out two minutes later — nor resolve the second
    one early. A goal that is merely CANCELING answers a second cancel request
    with ERROR_GOAL_TERMINATED, so the second press would otherwise report
    "already stopped" while the robot was still coming to a halt."""
    executor = _executor(_accepted())
    handle = executor._active["goal_handle"]
    cancels = []

    def counting_cancel():
        cancels.append(None)
        # What a goal already CANCELING answers a second request with.
        response = _accepted() if len(cancels) == 1 else SimpleNamespace(
            goals_canceling=[], return_code=3)
        return _Future(response)

    handle.cancel_goal_async = counting_cancel
    executor._stop("stop-1")
    executor._stop("stop-2")
    # One goal, one cancellation: the second press joins the first and waits.
    assert len(cancels) == 1
    assert executor._ws.results("stop-1") == []
    assert executor._ws.results("stop-2") == []

    executor._on_result("goal-1", _Future(SimpleNamespace(status=5)))
    for command_id in ("stop-1", "stop-2"):
        assert len(executor._ws.results(command_id)) == 1
        assert executor._ws.results(command_id)[0]["outcome"] == "succeeded"


class _DeferredFuture(_Future):
    """A future whose callback fires only when the test says so."""

    def __init__(self, value):
        super().__init__(value)
        self._callbacks = []

    def add_done_callback(self, callback):
        self._callbacks.append(callback)

    def resolve(self):
        for callback in self._callbacks:
            callback(self)


def test_a_goal_ending_before_the_cancel_reply_reports_the_stop_once():
    """The two ends race: the goal can reach a terminal state before the cancel
    reply arrives. Whichever gets there first reports, and the other stays
    quiet."""
    executor = _executor(_accepted())
    cancel_future = _DeferredFuture(_accepted())
    executor._active["goal_handle"].cancel_goal_async = lambda: cancel_future
    executor._stop("stop-1")

    executor._on_result("goal-1", _Future(SimpleNamespace(status=5)))
    cancel_future.resolve()  # the reply, arriving late
    assert len(executor._ws.results("stop-1")) == 1


def test_navigate_is_refused_while_an_undock_is_running():
    """Two motion owners at once is a state the stop path cannot describe."""
    executor = _executor(_accepted(), undocking=True)
    executor._navigate("goal-2", {"x": 1.0, "y": 2.0}, None)
    acks = [d for kind, d in executor._ws.sent if kind == "command.ack"]
    assert acks[0]["accepted"] is False and "off its dock" in acks[0]["reason"]
    assert executor._active is None


def test_the_localization_override_is_honoured_on_the_robot_too():
    """Defence in depth has to know about the override, or it defeats it.

    This precheck deliberately repeats the dashboard server's gates, so a
    bypass the server honours and the robot does not would simply be refused
    one hop later, and the operator would see the same sentence with no way
    past it.
    """
    from patrolbot_web_bridge.commands import precheck

    base = _navigable_base_state()
    goal = {"x": 1.0, "y": 2.0, "yaw": 0.0}

    blocked = precheck("navigate_to_pose", goal, base, localized=False,
                       base_state_age=0.1, odom_age=0.1)
    assert blocked is not None and "know where it is" in blocked

    allowed = precheck("navigate_to_pose", goal, base, localized=False,
                       base_state_age=0.1, allow_unlocalized=True,
                       odom_age=0.1, operator_authorized=True)
    assert allowed is None


def test_the_robot_side_override_waives_only_localization():
    """One waiver, not a master key."""
    from patrolbot_web_bridge.commands import precheck

    goal = {"x": 1.0, "y": 2.0, "yaw": 0.0}
    for field, value in (("motors_enabled", False),
                         ("estop_pressed", True),
                         ("fault_flags", 4),
                         ("hardware_state_valid", False)):
        base = _navigable_base_state()
        base[field] = value
        reason = precheck("navigate_to_pose", goal, base, localized=False,
                          base_state_age=0.1, allow_unlocalized=True,
                          odom_age=0.1, operator_authorized=True)
        assert reason is not None, field


def test_the_robot_side_override_defaults_off():
    """Omitting the flag must behave exactly as before."""
    from patrolbot_web_bridge.commands import precheck

    reason = precheck("navigate_to_pose", {"x": 1.0, "y": 2.0, "yaw": 0.0},
                      _navigable_base_state(), localized=False,
                      base_state_age=0.1, odom_age=0.1)
    assert reason is not None and "know where it is" in reason


def _navigable_base_state() -> dict:
    """Off the charger, motors live, telemetry fresh and valid."""
    return {
        "link_connected": True,
        "telemetry_age": 0.1,
        "hardware_state_valid": True,
        "fault_flags": 0,
        "estop_pressed": False,
        "motors_enabled": True,
        "charge_state": "discharging",
        "odom_epoch_valid": True,
        "localization_recovery_required": False,
        "localization_seed_stamp_ns": 100,
    }


def test_the_override_cannot_waive_a_dead_position_stream():
    """"Unsure where it is" and "no longer saying where it is" are different.

    Only the first is the operator's to accept. The bridge is the barrier that
    has to hold when a frame arrives without passing the dashboard server, and
    without this it would have no pose-recency check left at all once the
    override was set.
    """
    from patrolbot_web_bridge.commands import MAX_ODOM_AGE_S, precheck

    goal = {"x": 1.0, "y": 2.0, "yaw": 0.0}
    for odom_age in (None, MAX_ODOM_AGE_S + 0.1):
        reason = precheck("navigate_to_pose", goal, _navigable_base_state(),
                          localized=False, base_state_age=0.1,
                          allow_unlocalized=True, odom_age=odom_age,
                          operator_authorized=True)
        assert reason is not None, odom_age
        assert "stopped reporting its position" in reason


def test_the_override_needs_the_server_authorization_stamp():
    """A browser cannot authorize itself past a safety gate.

    operator_authorized is stamped by the dashboard server from the verified
    session role and is never settable by the browser, exactly as the guarded
    undock requires. An unstamped override is treated as not asked for.
    """
    from patrolbot_web_bridge.commands import precheck

    reason = precheck("navigate_to_pose", {"x": 1.0, "y": 2.0, "yaw": 0.0},
                      _navigable_base_state(), localized=False,
                      base_state_age=0.1, allow_unlocalized=True,
                      odom_age=0.1, operator_authorized=False)
    assert reason is not None and "know where it is" in reason


def test_authorized_override_logs_once_and_reaches_navigation():
    """Jazzy's RcutilsLogger takes one formatted message, unlike stdlib logging.

    The deployed two-argument warning call raised TypeError here and restarted
    the web bridge before the goal could reach the action client.
    """
    warnings = []
    navigations = []
    logger = SimpleNamespace(warning=lambda message: warnings.append(message))
    executor = CommandExecutor.__new__(CommandExecutor)
    executor._node = SimpleNamespace(get_logger=lambda: logger)
    executor._ws = _Ws()
    executor._navigate = (
        lambda command_id, goal, current_yaw:
        navigations.append((command_id, goal, current_yaw)))

    goal = {"x": 1.0, "y": 2.0, "yaw": 0.0}
    executor.handle(
        {
            "command_id": "override-goal",
            "command": "navigate_to_pose",
            "goal": goal,
            "allow_unlocalized": True,
            "operator_authorized": True,
        },
        _navigable_base_state(),
        current_yaw=0.25,
        localized=False,
        base_state_age=0.1,
        odom_age=0.1,
    )

    assert warnings == [
        "LOCALIZATION GATE OVERRIDDEN by operator: navigating with an "
        "unusable map-frame fix (command override-goal)"
    ]
    assert navigations == [("override-goal", goal, 0.25)]
