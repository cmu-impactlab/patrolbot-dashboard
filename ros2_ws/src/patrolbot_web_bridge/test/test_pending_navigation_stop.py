"""Pending/unknown navigation ownership regressions; no ROS imports or graph."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from patrolbot_web_bridge.commands import CommandExecutor


class Future:
    def __init__(self):
        self.callbacks = []
        self.resolved = False
        self.value = None
        self.error = None

    def result(self):
        assert self.resolved
        if self.error is not None:
            raise self.error
        return self.value

    def add_done_callback(self, callback):
        if self.resolved:
            callback(self)
        else:
            self.callbacks.append(callback)

    def resolve(self, value=None, error=None):
        assert not self.resolved
        self.resolved, self.value, self.error = True, value, error
        for callback in self.callbacks:
            callback(self)


class Handle:
    accepted = True
    goal_id = NS(uuid=list(range(16)))

    def __init__(self):
        self.result_future = Future()
        self.cancel_future = Future()
        self.cancel_calls = 0
        self.cancel_error = None

    def get_result_async(self):
        return self.result_future

    def cancel_goal_async(self):
        self.cancel_calls += 1
        if self.cancel_error is not None:
            raise self.cancel_error
        return self.cancel_future


@pytest.fixture
def executor():
    e = CommandExecutor.__new__(CommandExecutor)
    e.frames = []
    e._ws = NS(send=lambda kind, data: e.frames.append((kind, data)))
    e._active = e._undock_active = e._nav_pending = e._map_active = None
    e._navigation_context = ("floor", "r1", 1, 1, True, False, 1)
    e._navigation_generation = e._motor_disable_generation = 0
    e._node = NS(get_clock=lambda: NS(now=lambda: NS(to_msg=lambda: 1)))
    e._NavigateToPose = NS(Goal=lambda: NS(pose=NS(
        header=NS(), pose=NS(position=NS(), orientation=NS()))))
    e.acceptance = Future()
    e._nav_client = NS(server_is_ready=lambda: True,
                       send_goal_async=Mock(return_value=e.acceptance))
    return e


def frames(e, kind, command_id):
    return [data for frame_kind, data in e.frames
            if frame_kind == kind and data.get("command_id") == command_id]


def results(e, command_id):
    return frames(e, "command.result", command_id)


def start(e):
    e._navigate("nav", {"x": 1.0, "y": 2.0}, None)
    return e._nav_pending


def cancel(e, command_id="nav"):
    # The bridge translates wire command.cancel to this existing dispatch
    # shape. The broker must use the ORIGINAL goal's command_id.
    e.handle({"command": "cancel", "command_id": command_id}, None, None)


@pytest.mark.parametrize("status,nav_outcome,stop_outcome", [
    (4, "succeeded", "succeeded"),
    (5, "canceled", "succeeded"),
    (6, "failed", "failed"),
])
def test_pending_stop_cancels_late_handle_and_reports_actual_terminal_outcome(
        executor, status, nav_outcome, stop_outcome):
    e = executor
    owner = start(e)
    e._stop("stop")
    assert e._nav_pending is owner and owner["cancel"]
    assert results(e, "stop") == []
    handle = Handle()
    e.acceptance.resolve(handle)
    assert e._active is owner and e._nav_pending is None
    assert handle.cancel_calls == 1
    assert frames(e, "command.ack", "nav")[-1]["accepted"]
    handle.cancel_future.resolve(NS(goals_canceling=[object()], return_code=0))
    assert results(e, "stop") == results(e, "nav") == []
    handle.result_future.resolve(NS(status=status))
    assert results(e, "nav")[0]["outcome"] == nav_outcome
    assert results(e, "stop")[0]["outcome"] == stop_outcome
    assert e._active is e._nav_pending is None
    if status == 4:
        assert "reached its destination" in results(e, "stop")[0]["detail"]


def test_rejection_closes_pending_owner_and_all_stops_without_claiming_it_ran(executor):
    e = executor
    start(e)
    e._stop("stop-1")
    e._stop("stop-2")
    assert results(e, "stop-1") == results(e, "stop-2") == []
    e.acceptance.resolve(NS(accepted=False))
    assert frames(e, "command.ack", "nav") == [{
        "command_id": "nav", "accepted": False,
        "reason": "Navigation declined the destination."}]
    assert results(e, "nav") == []  # Negative ack is the wire rejection.
    for stop_id in ("stop-1", "stop-2"):
        assert len(results(e, stop_id)) == 1
        assert results(e, stop_id)[0]["outcome"] == "succeeded"
        assert "did not accept" in results(e, stop_id)[0]["detail"]
    assert e._nav_pending is e._active is None


def test_multiple_stops_join_before_and_after_acceptance_without_extra_cancels(executor):
    e = executor
    start(e)
    e._stop("stop-1")
    e._stop("stop-2")
    handle = Handle()
    e.acceptance.resolve(handle)
    e._stop("stop-3")
    assert handle.cancel_calls == 1
    # Even "already terminated" must not report an early success.
    handle.cancel_future.resolve(NS(goals_canceling=[], return_code=3))
    assert all(not results(e, f"stop-{i}") for i in range(1, 4))
    handle.result_future.resolve(NS(status=5))
    for i in range(1, 4):
        assert len(results(e, f"stop-{i}")) == 1
        assert results(e, f"stop-{i}")[0]["outcome"] == "succeeded"


@pytest.mark.parametrize("failure", [
    "refused", "unknown_goal", "malformed", "bad_code", "future_error",
    "send_error", "callback_error",
])
def test_cancel_failure_fails_every_joined_stop_but_retains_motion_owner(executor, failure):
    e = executor
    owner = start(e)
    e._stop("stop-1")
    e._stop("stop-2")
    handle = Handle()
    if failure == "send_error":
        handle.cancel_error = RuntimeError("cancel send failed")
    if failure == "callback_error":
        handle.cancel_future.add_done_callback = Mock(side_effect=RuntimeError("callback failed"))
    e.acceptance.resolve(handle)
    if failure == "future_error":
        handle.cancel_future.resolve(error=RuntimeError("cancel response lost"))
    elif failure not in {"send_error", "callback_error"}:
        reply = {
            "refused": NS(goals_canceling=[], return_code=1),
            "unknown_goal": NS(goals_canceling=[], return_code=2),
            "malformed": NS(),
            "bad_code": NS(goals_canceling=[], return_code="broken"),
        }[failure]
        handle.cancel_future.resolve(reply)
    assert e._active is owner
    assert owner["path_blocked"] and not owner["cancel_pending"]
    for stop_id in ("stop-1", "stop-2"):
        assert len(results(e, stop_id)) == 1
        assert results(e, stop_id)[0]["outcome"] == "failed"
        assert "emergency stop" in results(e, stop_id)[0]["detail"]
    assert results(e, "nav") == []
    e._navigate("replacement", {"x": 3, "y": 4}, None)
    assert not frames(e, "command.ack", "replacement")[-1]["accepted"]
    # A new explicit Stop may retry; refusal must not permanently suppress it.
    handle.cancel_error = None
    handle.cancel_future = Future()
    e._stop("stop-3")
    assert handle.cancel_calls == 2
    handle.cancel_future.resolve(NS(goals_canceling=[object()], return_code=0))
    handle.result_future.resolve(NS(status=5))
    assert results(e, "nav")[0]["outcome"] == "canceled"
    assert results(e, "stop-3")[0]["outcome"] == "succeeded"
    assert all(len(results(e, sid)) == 1 for sid in ("stop-1", "stop-2", "stop-3"))


@pytest.mark.parametrize("status", [4, 5, 6])
def test_late_failed_cancel_reply_cannot_duplicate_terminal_stop(executor, status):
    e = executor
    start(e)
    e._stop("stop-1")
    e._stop("stop-2")
    handle = Handle()
    e.acceptance.resolve(handle)
    handle.result_future.resolve(NS(status=status))
    handle.cancel_future.resolve(error=RuntimeError("late lost reply"))
    assert len(results(e, "nav")) == 1
    assert len(results(e, "stop-1")) == len(results(e, "stop-2")) == 1


@pytest.mark.parametrize("phase", ["pending", "active", "stopping"])
def test_replacement_is_rejected_without_overwriting_unresolved_owner(executor, phase):
    e = executor
    owner = start(e)
    handle = Handle()
    if phase != "pending":
        e.acceptance.resolve(handle)
    if phase == "stopping":
        e._stop("stop")
    e._navigate("replacement", {"x": 3, "y": 4}, None)
    assert e._active is owner or e._nav_pending is owner
    assert e._nav_client.send_goal_async.call_count == 1
    assert not frames(e, "command.ack", "replacement")[-1]["accepted"]
    assert results(e, "nav") == results(e, "stop") == []


@pytest.mark.parametrize("phase", ["pending", "active"])
def test_broker_cancel_with_original_id_preserves_owner_until_terminal(executor, phase):
    e = executor
    owner = start(e)
    handle = Handle()
    if phase == "active":
        e.acceptance.resolve(handle)
    cancel(e, "not-this-goal")
    assert not owner.get("cancel") and handle.cancel_calls == 0
    cancel(e)
    if phase == "pending":
        assert owner["cancel"] and handle.cancel_calls == 0
        e.acceptance.resolve(handle)
    cancel(e)  # Idempotent while a cancellation is in flight.
    assert handle.cancel_calls == 1 and e._active is owner
    assert results(e, "nav") == []
    e._stop("stop")
    assert handle.cancel_calls == 1 and results(e, "stop") == []
    handle.result_future.resolve(NS(status=5))
    assert results(e, "nav")[0]["outcome"] == "canceled"
    assert results(e, "stop")[0]["outcome"] == "succeeded"


def test_broker_cancel_can_retry_after_refusal_without_losing_original_owner(executor):
    e = executor
    owner = start(e)
    handle = Handle()
    e.acceptance.resolve(handle)
    cancel(e)
    handle.cancel_future.resolve(NS(goals_canceling=[], return_code=2))
    assert e._active is owner and results(e, "nav") == []
    handle.cancel_future = Future()
    cancel(e)
    assert handle.cancel_calls == 2
    handle.result_future.resolve(NS(status=5))
    assert len(results(e, "nav")) == 1


@pytest.mark.parametrize("reply", ["exception", "missing"])
def test_unknown_acceptance_retains_pending_owner_and_stop_waiters(executor, reply):
    e = executor
    owner = start(e)
    e._stop("stop")
    if reply == "exception":
        e.acceptance.resolve(error=RuntimeError("acceptance reply lost"))
    else:
        e.acceptance.resolve(None)
    assert e._nav_pending is owner and owner["uncertain"] and owner["cancel"]
    assert results(e, "stop") == results(e, "nav") == []
    assert frames(e, "command.ack", "nav") == []
    e._stop("another-stop")
    assert results(e, "another-stop") == []
    e._navigate("replacement", {"x": 3, "y": 4}, None)
    assert not frames(e, "command.ack", "replacement")[-1]["accepted"]
    # If acceptance evidence later becomes available, the retained cancellation
    # intent still applies; an unknown report did not retire the owner.
    late_acceptance = Future()
    handle = Handle()
    late_acceptance.resolve(handle)
    e._on_goal_response("nav", late_acceptance)
    assert e._active is owner and handle.cancel_calls == 1
    handle.result_future.resolve(NS(status=5))
    assert results(e, "nav")[0]["outcome"] == "canceled"
    assert results(e, "stop")[0]["outcome"] == "succeeded"
    assert results(e, "another-stop")[0]["outcome"] == "succeeded"


def test_send_exception_is_unknown_not_proof_of_rejection(executor):
    e = executor
    e._nav_client.send_goal_async.side_effect = RuntimeError("transport failed")
    owner = start(e)
    e._stop("stop")
    assert e._nav_pending is owner and owner["uncertain"]
    assert results(e, "stop") == results(e, "nav") == []


@pytest.mark.parametrize("failure", ["exception", "nonterminal", "missing"])
def test_unknown_result_does_not_release_owner_or_claim_stop(executor, failure):
    e = executor
    owner = start(e)
    e._stop("stop")
    handle = Handle()
    e.acceptance.resolve(handle)
    if failure == "exception":
        handle.result_future.resolve(error=RuntimeError("result lost"))
    else:
        handle.result_future.resolve(NS(status=2) if failure == "nonterminal" else None)
    assert e._active is owner and owner["uncertain"] and owner["path_blocked"]
    assert results(e, "stop") == results(e, "nav") == []
    e._navigate("replacement", {"x": 3, "y": 4}, None)
    assert not frames(e, "command.ack", "replacement")[-1]["accepted"]


@pytest.mark.parametrize("invalidation", ["motor_disable", "context", "generation"])
@pytest.mark.parametrize("with_stop", [False, True])
def test_invalidated_acceptance_still_tracks_cancel_and_terminal_result(
        executor, invalidation, with_stop):
    e = executor
    owner = start(e)
    if with_stop:
        e._stop("stop")
    if invalidation == "motor_disable":
        e._call_dock_service = Mock()
        e._motor_disable("off")
    elif invalidation == "context":
        e._navigation_context = ("other-floor", "r2", 1, 1, True, False, 1)
    else:
        e._navigation_generation += 1
    handle = Handle()
    e.acceptance.resolve(handle)
    assert e._active is owner and handle.cancel_calls == 1
    assert results(e, "nav") == results(e, "stop") == []
    handle.cancel_future.resolve(NS(goals_canceling=[], return_code=1))
    assert e._active is owner
    if with_stop:
        assert results(e, "stop")[0]["outcome"] == "failed"
    handle.result_future.resolve(NS(status=6))
    assert results(e, "nav")[0]["outcome"] == "failed"


def test_result_subscription_failure_still_cancels_and_retains_accepted_owner(executor):
    e = executor
    owner = start(e)
    e._stop("stop")
    handle = Handle()
    handle.get_result_async = Mock(side_effect=RuntimeError("result transport lost"))
    e.acceptance.resolve(handle)
    assert e._active is owner and owner["uncertain"] and handle.cancel_calls == 1
    handle.cancel_future.resolve(NS(goals_canceling=[object()], return_code=0))
    assert results(e, "nav") == results(e, "stop") == []


def test_next_destination_is_admitted_only_after_original_terminal_result(executor):
    e = executor
    start(e)
    handle = Handle()
    e.acceptance.resolve(handle)
    e._stop("stop")
    handle.result_future.resolve(NS(status=5))
    e.acceptance = Future()
    e._nav_client.send_goal_async.return_value = e.acceptance
    e._navigate("next", {"x": 3, "y": 4}, None)
    assert e._nav_pending["command_id"] == "next"
    assert e._nav_client.send_goal_async.call_count == 2


def test_pending_goal_blocks_motor_enable_map_seed_and_lidar_power(executor):
    e = executor
    owner = start(e)
    e._call_dock_service = Mock()
    e._motor_enable("on")
    e._call_dock_service.assert_not_called()
    assert not frames(e, "command.ack", "on")[-1]["accepted"]
    e._lidar_client = Mock()
    e._set_lidar_power("lidar", {"enabled": False, "operator_authorized": True}, {
        "lidar_power_supported": True, "hardware_state_valid": True,
        "link_connected": True, "telemetry_age": .1, "lidar_state": "READY",
    }, .1, .1)
    e._lidar_client.call_async.assert_not_called()
    assert not frames(e, "command.ack", "lidar")[-1]["accepted"]
    e._PoseWithCovarianceStamped = lambda: NS(header=NS(), pose=NS(
        pose=NS(position=NS(), orientation=NS()), covariance=[0.] * 36))
    e._map_client = Mock()
    e._set_initial_pose("seed", {"x": 0, "y": 0}, True)
    e._map_client.send_goal_async.assert_not_called()
    assert not frames(e, "command.ack", "seed")[-1]["accepted"]
    assert e._nav_pending is owner


@pytest.mark.parametrize("unknown", [False, True])
def test_fault_recovery_cannot_override_pending_navigation_owner(executor, unknown):
    e = executor
    owner = start(e)
    if unknown:
        e.acceptance.resolve(error=RuntimeError("acceptance lost"))
    e._dock_reset_client = Mock(service_is_ready=lambda: True)
    e.handle({"command": "dock_reset", "command_id": "recover",
              "operator_authorized": True}, {}, None)
    e._dock_reset_client.call_async.assert_not_called()
    assert not frames(e, "command.ack", "recover")[-1]["accepted"]
    assert e._nav_pending is owner


@pytest.fixture
def undock_executor(executor):
    e = executor
    e._node = Mock()
    e._Undock = NS(Goal=NS)
    e._undock_config = {}
    e._undock_client = Mock(server_is_ready=lambda: True,
                            send_goal_async=Mock(return_value=e.acceptance))
    return e


@pytest.mark.parametrize("failure", ["send", "accept_callback", "result", "result_callback"])
def test_undock_transport_exceptions_retain_uncertain_owner(undock_executor, failure):
    e = undock_executor
    handle = Handle()
    if failure == "send":
        e._undock_client.send_goal_async.side_effect = RuntimeError("send lost")
    elif failure == "accept_callback":
        e.acceptance.add_done_callback = Mock(side_effect=RuntimeError("callback lost"))
    elif failure == "result":
        handle.get_result_async = Mock(side_effect=RuntimeError("result lost"))
    else:
        handle.result_future.add_done_callback = Mock(side_effect=RuntimeError("callback lost"))
    e._undock("undock", True)
    owner = e._undock_active
    if failure in {"result", "result_callback"}:
        e.acceptance.resolve(handle)
        assert handle.cancel_calls == 1
    assert e._undock_active is owner and owner["uncertain"]
    assert not results(e, "undock")
    assert all(ack["accepted"] for ack in frames(e, "command.ack", "undock"))
    e._undock("replacement", True)
    assert e._undock_active is owner
    assert not frames(e, "command.ack", "replacement")[-1]["accepted"]


@pytest.mark.parametrize("source", ["stop", "cancel", "motor_disable"])
@pytest.mark.parametrize("failure", [None, "result", "result_callback"])
def test_deferred_undock_cancel_survives_result_subscription_failure(
        undock_executor, source, failure):
    e = undock_executor
    e._undock("undock", True)
    owner = e._undock_active
    if source == "stop":
        e._stop("stop")
    elif source == "cancel":
        cancel(e, "undock")
    else:
        e._call_dock_service = Mock()
        e._motor_disable("off")
    handle = Handle()
    if failure == "result":
        handle.get_result_async = Mock(side_effect=RuntimeError("result lost"))
    elif failure == "result_callback":
        handle.result_future.add_done_callback = Mock(side_effect=RuntimeError("callback lost"))
    e.acceptance.resolve(handle)
    assert handle.cancel_calls == 1
    assert owner["goal_handle"] is handle and e._undock_active is owner
    assert bool(owner.get("uncertain")) is (failure is not None)
    assert owner.get("stop_command_ids", []) == (["stop"] if source == "stop" else [])
    handle.cancel_future.resolve(NS(goals_canceling=[object()], return_code=0))
    assert not results(e, "undock") and not results(e, "stop")
    assert e._undock_active is owner
    e._undock("replacement", True)
    assert e._undock_active is owner
    assert not frames(e, "command.ack", "replacement")[-1]["accepted"]
    if failure is None:
        handle.result_future.resolve(NS(status=5, result=NS(success=False, code=9, message="")))
        assert e._undock_active is None
        assert results(e, "undock")[0]["outcome"] == "canceled"
        assert [r["outcome"] for r in results(e, "stop")] == (["succeeded"] if source == "stop" else [])


@pytest.mark.parametrize("failure", ["send", "callback"])
def test_deferred_undock_cancel_failure_still_subscribes_to_result(undock_executor, failure):
    e = undock_executor
    e._undock("undock", True)
    e._stop("stop")
    handle = Handle()
    if failure == "send":
        handle.cancel_error = RuntimeError("cancel send lost")
    else:
        handle.cancel_future.add_done_callback = Mock(side_effect=RuntimeError("cancel callback lost"))
    e.acceptance.resolve(handle)
    assert handle.cancel_calls == 1
    assert len(handle.result_future.callbacks) == 1
    assert e._undock_active is not None and not results(e, "undock")
    if failure == "callback":
        assert e._undock_active["uncertain"]
        assert e._undock_active["stop_command_ids"] == ["stop"]
    handle.result_future.resolve(NS(status=5, result=NS(success=False, code=9, message="")))
    assert e._undock_active is None
    assert results(e, "undock")[0]["outcome"] == "canceled"
    if failure == "callback":
        assert results(e, "stop")[0]["outcome"] == "succeeded"
