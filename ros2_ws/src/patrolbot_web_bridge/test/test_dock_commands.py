"""Pure-logic tests for the dock-manager command path (no ROS graph needed).

The translation from the robot's typed result codes into what the operator
reads is the part worth pinning: a wrong mapping here tells someone the robot
is fine when it has actually stopped with a safety fault.

Codes are verbatim from patrolbot_interfaces/action/Undock.action and
patrolbot_interfaces/srv/{ChargeRelease,MotorEnable}.srv.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from patrolbot_web_bridge.commands import (
    SERVICE_CODE_TEXT,
    UNDOCK_CODE_TEXT,
    CommandExecutor,
    precheck,
    service_detail,
    undock_detail,
    undock_outcome,
    undock_stage,
)

# Undock.action result codes.
OK = 0
REJECTED = 1
PRECHECK_FAILED = 2
RELEASE_FAILED = 3
MOTOR_STATE_FAILED = 4
SENSOR_FAILED = 5
LOCALIZATION_FAILED = 6
COMMAND_PATH_FAILED = 7
MOTION_FAILED = 8
CANCELED = 9
TIMEOUT = 10
SESSION_CHANGED = 11
SAFETY_FAULT = 12


def test_charging_never_enables_automatic_dock_pose_reset_by_default():
    bridge_source = (Path(__file__).parents[1] / "patrolbot_web_bridge" /
                     "bridge_node.py").read_text(encoding="utf-8")
    assert '("auto_dock_pose_on_charge", False)' in bridge_source
    assert '("auto_dock_pose_on_charge", True)' not in bridge_source


def test_every_undock_code_has_operator_copy():
    for code in range(OK, SAFETY_FAULT + 1):
        assert code in UNDOCK_CODE_TEXT, f"undock code {code} has no copy"
        assert UNDOCK_CODE_TEXT[code].strip()


def test_every_service_code_has_operator_copy():
    for code in range(0, 8):
        assert code in SERVICE_CODE_TEXT
        assert SERVICE_CODE_TEXT[code].strip()


def test_success_maps_to_succeeded():
    assert undock_outcome(True, OK) == "succeeded"


def test_cancel_and_timeout_are_not_reported_as_failures():
    # The dashboard styles these differently and does not alarm on them.
    assert undock_outcome(False, CANCELED) == "canceled"
    assert undock_outcome(False, TIMEOUT) == "timeout"


def test_every_other_code_is_a_failure():
    for code in (REJECTED, PRECHECK_FAILED, RELEASE_FAILED, MOTOR_STATE_FAILED,
                 SENSOR_FAILED, LOCALIZATION_FAILED, COMMAND_PATH_FAILED,
                 MOTION_FAILED, SESSION_CHANGED, SAFETY_FAULT):
        assert undock_outcome(False, code) == "failed"


def test_success_flag_without_ok_code_is_still_a_failure():
    """Fail closed: a robot claiming success with a non-OK code is not trusted."""
    assert undock_outcome(True, SAFETY_FAULT) == "failed"


def test_detail_carries_the_manager_message_on_failure():
    detail = undock_detail(LOCALIZATION_FAILED, "amcl covariance 0.9")
    assert "where it is" in detail
    assert "amcl covariance 0.9" in detail


def test_detail_omits_the_message_on_success():
    # Nothing useful to add, and the success sentence should stay clean.
    assert undock_detail(OK, "state=COMPLETE") == UNDOCK_CODE_TEXT[OK]


def test_manual_recovery_is_spelled_out():
    detail = undock_detail(MOTION_FAILED, "", manual_recovery=True)
    assert "check the robot at the dock" in detail


def test_unknown_code_still_produces_a_sentence():
    assert undock_detail(999, "").strip()
    assert service_detail(999, "").strip()


def test_service_detail_reports_precondition_failures():
    detail = service_detail(3, "motors already enabled")
    assert "pre-conditions" in detail
    assert "motors already enabled" in detail


def test_undock_stages_are_plain_language():
    assert undock_stage("MOVING") == "Backing off the dock"
    assert undock_stage("releasing") == "Releasing the charger"
    assert undock_stage("TURN_AWAY_FROM_DOCK") == "Turning away from the dock"
    assert undock_stage("HARDWARE_UNDOCK", "BACKING_OUT") == "Backing clear of the dock"
    assert undock_stage("VERIFY_HANDOFF") == "Checking the robot is ready to turn"
    assert undock_stage("UNKNOWN") == "Waiting for the robot to confirm the undock outcome"
    assert undock_stage("STOPPING") == "Waiting for undocking to stop"
    # An unrecognized state passes through rather than vanishing.
    assert undock_stage("SOME_NEW_STATE") == "SOME_NEW_STATE"
    assert undock_stage("") == "Undocking"


CHARGING_BASE = dict(lidar_state="READY", lidar_scan_age=0.1,
                     link_connected=True, hardware_state_valid=True,
                     telemetry_age=0.1, charge_state="charging", fault_flags=0,
                     estop_pressed=False, bumpers_valid=True, bumpers_rear=False,
                     dock_state="DOCKED_CONFIRMED", dock_state_valid=True,
                     undock_profile_commissioned=True, undock_active=False)


def test_fresh_charging_undock_does_not_require_map_localization():
    assert precheck("undock", None, CHARGING_BASE, localized=False,
                    base_state_age=0.1, odom_age=None) is None


@pytest.mark.parametrize("changes,fragment", [
    (dict(charge_state="docked"), "not charging"),
    (dict(charge_state="not_charging"), "not charging"),
    (dict(charge_state="unknown"), "not charging"),
    (dict(charge_state="float", dock_state="CLEAR_CONFIRMED"), "not charging"),
    (dict(charge_state="float", dock_state="DEPARTING"), "not charging"),
    (dict(dock_state_valid=False), "dock observer"),
    (dict(undock_profile_commissioned=False), "profile"),
    (dict(undock_active=True), "already undocking"),
    (dict(estop_pressed=True), "emergency stop"),
    (dict(fault_flags=4), "fault"),
    (dict(bumpers_valid=False), "bumper readings"),
    (dict(bumpers_rear=True), "rear bumper"),
])
def test_undock_precheck_requires_charging_and_keeps_physical_interlocks(changes, fragment):
    assert fragment in precheck("undock", None, dict(CHARGING_BASE, **changes),
                                base_state_age=0.1)


@pytest.mark.parametrize("age", [None, 0.6, float("nan")])
def test_undock_refuses_stale_charging_telemetry(age):
    assert precheck("undock", None, CHARGING_BASE, base_state_age=age)


def pending_executor():
    executor = CommandExecutor.__new__(CommandExecutor)
    executor._ack = Mock(); executor._result = Mock(); executor._ws = Mock()
    executor._node = Mock()
    executor._active = executor._undock_active = executor._map_active = None
    executor._undock_client = Mock(server_is_ready=Mock(return_value=True))
    executor._undock_config = {}; executor._Undock = SimpleNamespace(Goal=SimpleNamespace)
    executor._motor_disable_generation = 0; executor._last_undock_progress_mono = 0.0
    return executor


def accepted_handle():
    handle = Mock(accepted=True)
    handle.cancel_goal_async.return_value = Mock()
    handle.get_result_async.return_value = Mock()
    return handle


def test_pending_undock_excludes_a_second_request_and_uses_commissioned_defaults():
    executor = pending_executor()
    executor._undock("first", True); executor._undock("second", True)
    executor._undock_client.send_goal_async.assert_called_once()
    goal = executor._undock_client.send_goal_async.call_args.args[0]
    assert goal.reverse_distance == 0.53 and goal.reverse_speed == 0.10
    assert executor._ack.call_args.args[:2] == ("second", False)


def test_undock_cannot_acquire_a_pending_navigation_request():
    executor = pending_executor(); executor._nav_pending = {"command_id": "destination"}
    executor._undock("undock", True)
    executor._undock_client.send_goal_async.assert_not_called()
    assert executor._ack.call_args.args[1] is False


def test_a_definitively_rejected_navigation_request_cannot_keep_undock_blocked():
    executor = pending_executor(); executor._nav_pending = {"command_id": "destination"}
    executor._on_goal_response("destination", SimpleNamespace(result=lambda: SimpleNamespace(accepted=False)))
    executor._undock("undock", True)
    executor._undock_client.send_goal_async.assert_called_once()


def test_an_unknown_navigation_outcome_still_excludes_undock():
    executor = pending_executor(); executor._nav_pending = {"command_id": "destination"}
    future = Mock(); future.result.side_effect = RuntimeError("connection lost")
    executor._on_goal_response("destination", future)
    executor._undock("undock", True)
    executor._undock_client.send_goal_async.assert_not_called()


def test_an_older_navigation_decline_cannot_clear_a_newer_pending_request():
    executor = pending_executor(); executor._nav_pending = {"command_id": "current-destination"}
    executor._on_goal_response("old", SimpleNamespace(result=lambda: SimpleNamespace(accepted=False)))
    assert executor._nav_pending["command_id"] == "current-destination"


def test_stop_before_undock_acceptance_cancels_the_late_goal_and_waits_for_terminal():
    executor = pending_executor(); executor._undock("undock", True)
    executor._stop("stop")
    executor._result.assert_not_called()
    handle = accepted_handle()
    executor._on_undock_response("undock", SimpleNamespace(result=lambda: handle), 0)
    handle.cancel_goal_async.assert_called_once()
    assert executor._undock_active is not None
    terminal = SimpleNamespace(status=5, result=SimpleNamespace(success=False, code=CANCELED, message=""))
    executor._on_undock_result("undock", SimpleNamespace(result=lambda: terminal))
    assert executor._undock_active is None
    assert executor._result.call_args_list[0].args[:2] == ("undock", "canceled")
    assert executor._result.call_args_list[1].args[:2] == ("stop", "succeeded")
    cancel_callback = handle.cancel_goal_async.return_value.add_done_callback.call_args.args[0]
    cancel_callback(SimpleNamespace(result=lambda: SimpleNamespace(return_code=0, goals_canceling=[object()])))
    executor._on_undock_result("undock", SimpleNamespace(result=lambda: terminal))
    assert executor._result.call_count == 2


def test_motor_disable_before_undock_acceptance_cancels_the_late_goal():
    executor = pending_executor(); executor._undock("undock", True)
    executor._motor_disable_generation = 1
    handle = accepted_handle()
    executor._on_undock_response("undock", SimpleNamespace(result=lambda: handle), 0)
    handle.cancel_goal_async.assert_called_once()
    assert executor._undock_active["goal_handle"] is handle


def test_late_old_undock_acceptance_cannot_replace_the_current_operation():
    executor = pending_executor(); executor._undock("current", True)
    handle = accepted_handle()
    executor._on_undock_response("old", SimpleNamespace(result=lambda: handle), 0)
    handle.cancel_goal_async.assert_called_once()
    assert executor._undock_active["command_id"] == "current"


def test_stale_undock_feedback_is_ignored():
    executor = pending_executor(); executor._undock("current", True)
    executor._on_undock_feedback("old", SimpleNamespace(feedback=SimpleNamespace(state="COMPLETE")))
    executor._ws.send.assert_not_called()


def test_rejected_undock_clears_the_pending_owner_and_resolves_a_waiting_stop():
    executor = pending_executor(); executor._undock("undock", True)
    executor._stop("stop")
    executor._on_undock_response("undock", SimpleNamespace(result=lambda: SimpleNamespace(accepted=False)), 0)
    assert executor._undock_active is None
    assert executor._ack.call_args.args[:2] == ("undock", False)
    assert executor._result.call_args.args[:2] == ("stop", "succeeded")


def test_cancel_before_undock_acceptance_cancels_the_late_goal():
    executor = pending_executor(); executor._undock("undock", True)
    executor._cancel_navigation("undock")
    handle = accepted_handle()
    executor._on_undock_response("undock", SimpleNamespace(result=lambda: handle), 0)
    handle.cancel_goal_async.assert_called_once()


def test_cancel_transport_error_keeps_undock_owned_and_reports_unknown_stop():
    executor = pending_executor(); executor._undock("undock", True)
    handle = accepted_handle()
    executor._on_undock_response("undock", SimpleNamespace(result=lambda: handle), 0)
    handle.cancel_goal_async.side_effect = RuntimeError("connection lost")
    executor._cancel_navigation("undock")
    assert executor._undock_active is not None
    assert "could not be asked to stop" in executor._ws.send.call_args.args[1]["detail"]
    executor._result.assert_not_called()


def test_unknown_undock_acceptance_stays_owned_and_keeps_stop_pending():
    executor = pending_executor(); executor._undock("undock", True)
    executor._stop("stop")
    future = Mock(); future.result.side_effect = RuntimeError("connection lost")
    executor._on_undock_response("undock", future, 0)
    assert executor._undock_active["command_id"] == "undock"
    assert executor._undock_active["stop_command_ids"] == ["stop"]
    executor._result.assert_not_called()
    assert executor._ws.send.call_args.args[1]["stage"] == "unknown"
    executor._undock("second", True)
    executor._undock_client.send_goal_async.assert_called_once()


def test_generic_cancel_targets_only_its_pending_undock_uuid():
    executor = pending_executor(); executor._undock("undock", True)
    executor.handle(dict(command="cancel", command_id="other"), None, None)
    assert not executor._undock_active.get("cancel_requested", False)
    executor.handle(dict(command="cancel", command_id="undock"), None, None)
    assert executor._undock_active["cancel_requested"]


def test_undock_watchdog_silence_keeps_owner_and_waits_for_the_actual_terminal():
    executor = pending_executor(); executor._node = Mock()
    executor._undock("undock", True)
    handle = accepted_handle()
    executor._on_undock_response("undock", SimpleNamespace(result=lambda: handle), 0)
    executor._fail_undock("The action server stopped answering.")
    assert executor._undock_active is not None
    handle.cancel_goal_async.assert_called_once()
    executor._result.assert_not_called()
    assert executor._ws.send.call_args.args[1]["stage"] == "unknown"
    terminal = SimpleNamespace(status=5, result=SimpleNamespace(success=False, code=CANCELED, message=""))
    executor._on_undock_result("undock", SimpleNamespace(result=lambda: terminal))
    assert executor._undock_active is None
    assert executor._result.call_args.args[:2] == ("undock", "canceled")


@pytest.mark.parametrize("reply", [None, SimpleNamespace(status=0, result=None),
                                    SimpleNamespace(status=2, result=SimpleNamespace()),
                                    SimpleNamespace(status=5, result=None)])
def test_missing_or_nonterminal_undock_result_keeps_exact_owner_and_pending_stop(reply):
    executor = pending_executor(); executor._undock("undock", True)
    handle = accepted_handle()
    executor._on_undock_response("undock", SimpleNamespace(result=lambda: handle), 0)
    executor._stop("stop")
    owner = executor._undock_active
    executor._on_undock_result("undock", SimpleNamespace(result=lambda: reply))
    assert executor._undock_active is owner
    assert owner["stop_command_ids"] == ["stop"]
    executor._result.assert_not_called()
    assert executor._ws.send.call_args.args[1]["stage"] == "unknown"


def test_get_result_exception_retains_owner_and_stops_until_a_real_terminal():
    executor = pending_executor(); executor._undock("undock", True)
    handle = accepted_handle()
    executor._on_undock_response("undock", SimpleNamespace(result=lambda: handle), 0)
    executor._stop("stop")
    owner = executor._undock_active
    broken = Mock(); broken.result.side_effect = RuntimeError("GetResult connection lost")
    executor._on_undock_result("undock", broken)
    assert executor._undock_active is owner
    assert owner["stop_command_ids"] == ["stop"]
    executor._result.assert_not_called()
    executor._undock("second", True)
    executor._undock_client.send_goal_async.assert_called_once()
    terminal = SimpleNamespace(status=5, result=SimpleNamespace(success=False, code=CANCELED, message=""))
    executor._on_undock_result("undock", SimpleNamespace(result=lambda: terminal))
    assert executor._undock_active is None
    assert [call.args[:2] for call in executor._result.call_args_list] == [
        ("undock", "canceled"), ("stop", "succeeded")]


def test_unknown_result_requests_cancellation_for_only_its_matching_owner():
    executor = pending_executor(); executor._undock("undock", True)
    handle = accepted_handle()
    executor._on_undock_response("undock", SimpleNamespace(result=lambda: handle), 0)
    broken = Mock(); broken.result.side_effect = RuntimeError("GetResult connection lost")
    executor._on_undock_result("undock", broken)
    handle.cancel_goal_async.assert_called_once()
    assert executor._undock_active["cancel_requested"]
    executor._result.assert_not_called()


def test_old_result_exception_cannot_cancel_or_release_a_newer_undock():
    executor = pending_executor(); executor._undock("current", True)
    owner = executor._undock_active
    broken = Mock(); broken.result.side_effect = RuntimeError("old connection lost")
    executor._on_undock_result("old", broken)
    assert executor._undock_active is owner
    assert not owner.get("cancel_requested", False)
    broken.result.assert_not_called()
    executor._result.assert_not_called()
    executor._ws.send.assert_not_called()


def test_none_acceptance_keeps_owner_and_cancels_its_later_accepted_goal():
    executor = pending_executor(); executor._undock("undock", True)
    executor._stop("stop")
    owner = executor._undock_active
    executor._on_undock_response("undock", SimpleNamespace(result=lambda: None), 0)
    assert executor._undock_active is owner
    assert owner["stop_command_ids"] == ["stop"]
    executor._result.assert_not_called()
    assert executor._ws.send.call_args.args[1]["stage"] == "unknown"
    handle = accepted_handle()
    executor._on_undock_response("undock", SimpleNamespace(result=lambda: handle), 0)
    handle.cancel_goal_async.assert_called_once()


def test_none_old_acceptance_does_not_mutate_the_current_owner():
    executor = pending_executor(); executor._undock("current", True)
    owner = executor._undock_active
    executor._on_undock_response("old", SimpleNamespace(result=lambda: None), 0)
    assert executor._undock_active is owner
    assert not owner.get("cancel_requested", False)
    executor._ws.send.assert_not_called()


@pytest.mark.parametrize("status,success,code", [(6, True, OK), (6, False, CANCELED),
                                                (4, False, CANCELED), (4, False, OK)])
def test_inconsistent_terminal_cannot_confirm_a_successful_undock_or_stop(status, success, code):
    executor = pending_executor(); executor._undock("undock", True)
    handle = accepted_handle()
    executor._on_undock_response("undock", SimpleNamespace(result=lambda: handle), 0)
    executor._stop("stop")
    terminal = SimpleNamespace(status=status, result=SimpleNamespace(
        success=success, code=code, message=""))
    executor._on_undock_result("undock", SimpleNamespace(result=lambda: terminal))
    assert executor._undock_active is None
    assert [call.args[:2] for call in executor._result.call_args_list] == [
        ("undock", "failed"), ("stop", "failed")]
