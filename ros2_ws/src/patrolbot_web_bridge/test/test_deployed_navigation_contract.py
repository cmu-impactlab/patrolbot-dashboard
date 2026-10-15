"""Regressions for observed deployed methods; no ROS node or hardware access."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from patrolbot_web_bridge.commands import CommandExecutor


GOAL_ID = bytes(range(16))
CONTEXT = ("synthetic-floor", "synthetic-revision", 1, 1, True, False, 1)


class Future:
    def __init__(self, value):
        self.value = value

    def result(self):
        return self.value

    def add_done_callback(self, callback):
        callback(self)


@pytest.fixture
def executor():
    result = CommandExecutor.__new__(CommandExecutor)
    result._node = Mock(cfg={"path_max_points": 8})
    result._ws = Mock()
    result._undock_active = result._nav_pending = None
    result._navigation_context = CONTEXT
    result._navigation_generation = 1
    result._last_progress_mono = 0
    result._active = {"command_id": "current", "goal_id": GOAL_ID,
                      "goal_handle": Mock(), "navigation_context": CONTEXT,
                      "path_valid": False, "path_blocked": False}
    return result


@pytest.mark.parametrize("path_valid,expected", [(False, None), (True, 0.0)])
def test_zero_distance_is_known_only_after_a_current_path(executor, path_valid, expected):
    executor._active["path_valid"] = path_valid
    executor._on_feedback("current", NS(goal_id=NS(uuid=list(GOAL_ID)),
                                       feedback=NS(distance_remaining=0.0)))
    kind, payload = executor._ws.send.call_args.args
    assert kind == "command.progress"
    assert payload["distance_remaining"] == expected
    assert payload["path_valid"] is path_valid


@pytest.mark.parametrize("distance", [float("nan"), float("inf"), -1])
def test_invalid_distance_stays_unknown(executor, distance):
    executor._active["path_valid"] = True
    executor._on_feedback("current", NS(goal_id=NS(uuid=list(GOAL_ID)),
                                       feedback=NS(distance_remaining=distance)))
    payload = executor._ws.send.call_args.args[1]
    assert payload["distance_remaining"] is None
    assert payload["path_valid"] is False


def test_foreign_goal_feedback_is_ignored(executor):
    executor._on_feedback("current", NS(goal_id=NS(uuid=[99] * 16),
                                       feedback=NS(distance_remaining=0)))
    executor._ws.send.assert_not_called()


def test_cancel_clears_path_but_waits_for_terminal_result(executor):
    executor._active["path_valid"] = True
    handle = executor._active["goal_handle"]
    handle.cancel_goal_async.return_value = Future(NS(goals_canceling=[object()], return_code=0))
    executor._cancel_navigation("current")
    handle.cancel_goal_async.assert_called_once()
    assert executor._active["path_valid"] is False
    assert executor._active["path_blocked"] is True
    assert not any(c.args[0] == "command.result" for c in executor._ws.send.call_args_list)


def test_cancel_cannot_target_a_different_command(executor):
    executor._cancel_navigation("old-command")
    executor._active["goal_handle"].cancel_goal_async.assert_not_called()
    executor._ws.send.assert_not_called()


def test_context_change_withdraws_path_without_resuming_goal(executor):
    executor._active["path_valid"] = True
    executor.update_navigation_context(dict(zip(
        ("map_id", "map_revision", "map_activated_stamp_ns", "session_generation",
         "odom_epoch_valid", "localization_recovery_required", "localization_seed_stamp_ns"),
        ("synthetic-floor", "new-revision", 1, 1, True, False, 1))))
    assert executor._active["path_blocked"] is True
    assert executor._active["path_valid"] is False
    kind, payload = executor._ws.send.call_args.args
    assert kind == "telemetry.path" and payload["points"] == []
    executor._active["goal_handle"].cancel_goal_async.assert_not_called()


def test_unknown_undock_retains_owner_and_requests_one_cancel(executor):
    handle = Mock()
    owner = {"command_id": "undock", "goal_handle": handle}
    executor._undock_active = owner
    executor._fail_undock("acceptance missing")
    executor._fail_undock("still missing")
    assert executor._undock_active is owner and owner["uncertain"] is True
    handle.cancel_goal_async.assert_called_once()
    assert not any(c.args[0] == "command.result" for c in executor._ws.send.call_args_list)


def test_nonterminal_undock_reply_cannot_release_owner(executor):
    owner = {"command_id": "undock", "goal_handle": Mock()}
    executor._undock_active = owner
    executor._on_undock_result("undock", Future(NS(status=2, result=NS(success=True, code=0))))
    assert executor._undock_active is owner and owner["uncertain"] is True
    assert not any(c.args[0] == "command.result" for c in executor._ws.send.call_args_list)


def test_old_acceptance_cannot_replace_current_generation(executor):
    current = executor._active
    pending = {"command_id": "new-request", "cancel": False}
    executor._nav_pending = pending
    handle = Mock(accepted=True)
    executor._on_goal_response("old-request", Future(handle), navigation_generation=0)
    assert executor._active is current and executor._nav_pending is pending
    handle.cancel_goal_async.assert_called_once()
    assert executor._ws.send.call_args.args[1]["accepted"] is False
