from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from patrolbot_web_bridge.commands import CommandExecutor, precheck
from patrolbot_web_bridge.normalizers import normalize_base_state

@pytest.mark.parametrize("command", ["navigate_to_pose", "set_initial_pose", "undock", "charge_release"])
@pytest.mark.parametrize("state,age", [("OFF", 0), ("UNKNOWN", 0), ("STARTING", 0), ("FAULT", 0), ("READY", .6), ("READY", -1)])
def test_lidar_precheck_blocks_override(command, state, age):
    base = {"lidar_state": state, "lidar_scan_age": age}
    reason = precheck(command, {"x": 0, "y": 0}, base, False, .01, True, .01, True)
    assert reason and "LiDAR" in reason

def test_pose_recovers_without_localization_after_fresh_scans():
    assert precheck("set_initial_pose", {}, {"lidar_state": "READY", "lidar_scan_age": .1, "link_connected":True,"hardware_state_valid":True,"telemetry_age":.1}, False, .1) is None
    assert precheck("set_initial_pose", {}, None) is not None
    assert precheck("stop", None, None) is None

def executor():
    value = CommandExecutor.__new__(CommandExecutor)
    value._map_active = value._active = value._undock_active = None
    value._lidar_pending = None
    value._ack = Mock()
    value._result = Mock()
    value._lidar_client = Mock(service_is_ready=Mock(return_value=True))
    value._SetLidarPower = SimpleNamespace(Request=lambda: SimpleNamespace())
    return value

def test_lidar_dispatch_uses_typed_boolean_request_and_holds_spatial_commands():
    value = executor()
    base = {"lidar_state": "OFF", "lidar_power_supported": True, "hardware_state_valid": True, "link_connected": True, "telemetry_age": .1}
    value.handle({"command_id": "power", "command": "set_lidar_power", "enabled": True, "operator_authorized": True}, base, None, False, .01, .01)
    request = value._lidar_client.call_async.call_args.args[0]
    assert request.request_id == "power" and request.enabled is True
    value.handle({"command_id": "pose", "command": "set_initial_pose", "goal": {"x": 0, "y": 0, "map_id": "a", "map_revision": "r"}}, base, None, False, .01, .01)
    assert value._ack.call_args.args[1] is False
    assert "LiDAR" in value._ack.call_args.args[2]

@pytest.mark.parametrize("enabled,authorized", [(None, True), ("false", True), (1, True), (False, False)])
def test_power_requires_boolean_and_verified_authorization(enabled, authorized):
    value = executor()
    value.handle({"command_id": "power", "command": "set_lidar_power", "enabled": enabled, "operator_authorized": authorized}, {}, None, False, .01, .01)
    value._lidar_client.call_async.assert_not_called()
    assert value._ack.call_args.args[1] is False

def test_late_response_cannot_release_a_newer_request():
    value = executor()
    value._lidar_pending = "new"
    value._on_lidar_result("old", Mock())
    assert value._lidar_pending == "new"
    value._ack.assert_not_called()


def test_definitive_typed_timeout_allows_deliberate_recovery_but_blocks_spatial_commands():
    value = executor()
    value._lidar_pending = "power"
    value._on_lidar_result("power", SimpleNamespace(result=lambda: SimpleNamespace(request_id="power", code=5, accepted=True, success=False, message="Timed out")))
    assert value._lidar_pending is None
    assert value._lidar_recovery_required is True
    assert value._result.call_args.args[1] == "failed"
    value.handle({"command_id":"pose", "command":"set_initial_pose", "goal":{"map_id":"a","map_revision":"r","x":0,"y":0}}, {"lidar_state":"READY", "lidar_scan_age":.1}, None, False, .01, .01)
    assert value._ack.call_args.args[1] is False
    assert "recovery" in value._ack.call_args.args[2]
    value._lidar_pending = "recover"
    value._on_lidar_result("recover", SimpleNamespace(result=lambda: SimpleNamespace(request_id="recover", code=0, accepted=True, success=True, message="Commanded on")))
    assert value._lidar_recovery_required is False


@pytest.mark.parametrize("changes", [dict(link_connected=False),dict(hardware_state_valid=False),dict(telemetry_age=.5),dict(telemetry_age=float("nan"))])
def test_pose_and_power_refuse_disconnected_invalid_or_stale_hardware(changes):
    base = dict(lidar_state="READY",lidar_scan_age=.1,lidar_power_supported=True,link_connected=True,hardware_state_valid=True,telemetry_age=.1)
    base.update(changes)
    assert precheck("set_initial_pose", {}, base, False, .1)
    value = executor()
    value.handle(dict(command_id="power",command="set_lidar_power",enabled=False,operator_authorized=True),base,None,False,.1,.1)
    value._lidar_client.call_async.assert_not_called()


def test_terminal_power_needs_a_new_base_state_before_pose(monkeypatch):
    import patrolbot_web_bridge.commands as commands
    clock = Mock(return_value=100.0)
    monkeypatch.setattr(commands.time,"monotonic",clock)
    value = executor()
    value._set_initial_pose = Mock()
    value._lidar_pending = "power"
    value._on_lidar_result("power", SimpleNamespace(result=lambda: SimpleNamespace(request_id="power",code=0,accepted=True,success=True,message="Commanded on")))
    base = dict(lidar_state="READY",lidar_scan_age=.1,link_connected=True,hardware_state_valid=True,telemetry_age=.1)
    data = dict(command_id="pose",command="set_initial_pose",operator_authorized=True,goal=dict(map_id="a",map_revision="r",x=0,y=0))
    value.handle(data,base,None,False,.1,.1)
    value._set_initial_pose.assert_not_called()
    clock.return_value = 101.0
    value.handle(data,base,None,False,.1,.1)
    value._set_initial_pose.assert_called_once()
