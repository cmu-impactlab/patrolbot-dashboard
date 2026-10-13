import uuid
from types import SimpleNamespace as NS
from unittest.mock import Mock, AsyncMock
import pytest
from app.commands.broker import CommandBroker
from app.protocol.messages import CommandRequestData


@pytest.mark.asyncio
async def test_disable_passes_reset_lidar_lease_rate_and_state_barriers():
    base = NS(data=None, age=lambda: None)
    state = NS(connection="online", base_state=base, pose=base, capabilities=["motor_disable"])
    session = NS(robot_id="robot", state=state, websocket=NS(send_text=AsyncMock()))
    hub = NS(settings=NS(command_rate_per_min=1), db=None, robots={"robot":session},
        _next_seq=lambda:1, publish=Mock(), emit_event=AsyncMock())
    broker = CommandBroker(hub)
    broker.reset_pending["robot"] = "reset"; broker.lidar_pending["robot"] = "lidar"
    broker.lidar_state_after["robot"] = 100; broker.lidar_recovery_required.add("robot")
    broker.lease.acquire("robot", 2, "other", object())
    broker.rate.allow = Mock(return_value=False)
    client=NS(user=NS(id=1,username="operator",can_command=True))
    await broker.handle_browser_request(client, NS(robot_id="robot"),
        CommandRequestData(command_id=str(uuid.uuid4()), command="motor_disable"))
    session.websocket.send_text.assert_awaited_once()
    await broker.shutdown()
