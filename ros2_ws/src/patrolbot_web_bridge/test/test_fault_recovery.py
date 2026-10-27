from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from patrolbot_web_bridge.commands import CommandExecutor


def executor():
    e = CommandExecutor.__new__(CommandExecutor)
    e._map_active = e._active = e._undock_active = None
    e._ack = Mock()
    e._dock_reset_client = Mock(service_is_ready=Mock(return_value=True))
    e._DockReset = NS(Request=lambda: NS())
    return e


def test_explicit_recovery_uses_only_zero_motion_reset_service():
    e = executor()
    e.handle(dict(command="dock_reset", command_id="recover", operator_authorized=True), {}, None)
    assert e._dock_reset_client.call_async.call_args.args[0].request_id == "recover"


@pytest.mark.parametrize("authorized,base,busy", [
    (False, {}, False), (True, {"operation_outcome_unknown": True}, False),
    (True, {"undock_active": True}, False), (True, {}, True),
])
def test_recovery_does_not_override_unknown_or_active_operations(authorized, base, busy):
    e = executor()
    if busy:
        e._active = {"command_id": "old-goal"}
    e.handle(dict(command="dock_reset", command_id="recover", operator_authorized=authorized), base, None)
    e._dock_reset_client.call_async.assert_not_called()
    assert e._ack.call_args.args[1] is False
