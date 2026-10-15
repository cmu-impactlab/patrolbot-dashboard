from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from patrolbot_web_bridge.commands import CommandExecutor


def executor():
    e = CommandExecutor.__new__(CommandExecutor)
    e._map_active = None; e._active = {"command_id": "old-navigation", "goal_handle": Mock()}; e._undock_active = None
    e._ws = Mock()
    e._ack = Mock(); e._result = Mock(); e._node = Mock()
    e._motor_disable_client = Mock(service_is_ready=Mock(return_value=True))
    e._MotorDisable = NS(Request=lambda: NS())
    e._lidar_pending = "unknown-power"
    return e


def test_disable_bypasses_recovery_and_cancels_old_destination():
    e = executor()
    e.handle(dict(command="motor_disable", command_id="off", operator_authorized=True), None, None)
    e._active["goal_handle"].cancel_goal_async.assert_called_once()
    assert e._motor_disable_client.call_async.call_args.args[0].request_id == "off"


def test_disable_requires_verified_operator():
    e = executor()
    e.handle(dict(command="motor_disable", command_id="off"), None, None)
    e._motor_disable_client.call_async.assert_not_called()
    assert e._ack.call_args.args[1] is False


def test_disable_cannot_claim_success_while_motors_still_enabled():
    e = executor()
    e._on_dock_service_result("off", NS(result=lambda: NS(accepted=True, success=True,
        code=0, request_id="off", motors_enabled=True)), "turn the motors off")
    assert e._result.call_args.args[1] == "failed"


def test_enable_request_bypasses_map_pose_hold():
    e = executor(); e._map_active = {"command_id": "map"}
    e._motor_enable = Mock()
    e.handle(dict(command="motor_enable", command_id="on", operator_authorized=True), None, None)
    e._motor_enable.assert_called_once_with("on")


def test_old_goal_acceptance_after_disable_and_new_request_is_canceled():
    e = executor(); e._motor_disable_generation = 2
    goal = Mock(accepted=True)
    e._on_goal_response("old", NS(result=lambda: goal), generation=0)
    goal.cancel_goal_async.assert_called_once()
    assert e._ack.call_args.args[1] is False


def test_failed_map_cancellation_cannot_prevent_motor_disable():
    e = executor(); e._map_active = {"cancel": False}
    e._check_map_connection = Mock(side_effect=RuntimeError("gone"))
    e.handle(dict(command="motor_disable", command_id="off", operator_authorized=True), None, None)
    e._motor_disable_client.call_async.assert_called_once()


def test_enable_cannot_restart_an_old_goal_whose_cancel_has_not_finished():
    e = executor(); e._motor_client = Mock()
    e._motor_enable("on")
    e._motor_client.call_async.assert_not_called()
    assert e._ack.call_args.args[1] is False
