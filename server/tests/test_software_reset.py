from types import SimpleNamespace
import pytest
from app.commands.gates import StateFacts, software_reset_reason
from app.commands.broker import CommandBroker, ActiveCommand
from app.protocol.messages import CommandResultData


def safe(**changes):
    values=dict(online=True,link_connected=True,telemetry_age=.1,base_state_age=.1,
        pose_age=.1,hardware_state_valid=True,capabilities=('software_reset',),
        dock_state_valid=True,dock_state='DOCKED_CONFIRMED',stationary=True,
        motors_enabled=False,localized=False)
    values.update(changes);return StateFacts(**values)


def test_reset_does_not_require_localization():
    assert software_reset_reason(safe()) is None


@pytest.mark.parametrize('changes',[{'pose_age':None,'stationary':False},{'pose_age':4,'stationary':False}])
def test_reset_can_be_requested_before_a_pose_exists(changes):
    assert software_reset_reason(safe(**changes)) is None


@pytest.mark.parametrize('changes',[
    {'motors_enabled':True},{'navigating':True},{'undock_active':True},
    {'stationary':False},{'base_state_age':None},{'base_state_age':4},
    {'pose_age':None},{'pose_age':4},{'hardware_state_valid':False},
    {'dock_state_valid':False},{'dock_state':'UNKNOWN'},
    {'telemetry_age':float('nan')},
])
def test_robot_telemetry_does_not_prevent_emergency_reset(changes):
    assert software_reset_reason(safe(**changes)) is None


@pytest.mark.parametrize('changes',[{'online':False},{'capabilities':()}])
def test_reset_still_requires_a_reachable_commissioned_supervisor(changes):
    assert software_reset_reason(safe(**changes)) is not None


@pytest.mark.asyncio
async def test_uncertain_robot_result_keeps_reset_barrier_and_later_success_resolves():
    sent=[]
    hub=SimpleNamespace(settings=SimpleNamespace(command_rate_per_min=20),db=None,robots={},publish=lambda *args:sent.append(args))
    broker=CommandBroker(hub)
    entry=ActiveCommand(command_id='reset-id',command='software_reset',robot_id='robot')
    broker.active[entry.command_id]=entry;broker.reset_pending['robot']=entry.command_id
    session=SimpleNamespace(robot_id='robot')
    envelope=SimpleNamespace(type='command.result')
    await broker.handle_robot_reply(session,envelope,CommandResultData(command_id='reset-id',outcome='timeout'))
    assert broker.reset_pending['robot']=='reset-id' and 'reset-id' in broker.active
    await broker.handle_robot_reply(session,envelope,CommandResultData(command_id='reset-id',outcome='succeeded'))
    assert not broker.reset_pending and not broker.active


@pytest.mark.asyncio
async def test_other_robot_cannot_release_reset_barrier():
    hub=SimpleNamespace(settings=SimpleNamespace(command_rate_per_min=20),db=None,robots={},publish=lambda *args:None)
    broker=CommandBroker(hub);entry=ActiveCommand(command_id='reset-id',command='software_reset',robot_id='robot')
    broker.active[entry.command_id]=entry;broker.reset_pending['robot']=entry.command_id
    await broker.handle_robot_reply(SimpleNamespace(robot_id='other'),SimpleNamespace(type='command.result'),
        CommandResultData(command_id='reset-id',outcome='succeeded'))
    assert broker.reset_pending['robot']=='reset-id'


@pytest.mark.asyncio
@pytest.mark.parametrize("previous,uncertain,authorized,accepted", [
    ("software_reset", False, True, False),
    ("software_reset", True, True, True),
    ("navigate_to_pose", False, True, True),
    ("navigate_to_pose", False, False, False),
])
async def test_explicit_reset_retires_old_intents_but_never_overlaps_a_running_reset(previous, uncertain, authorized, accepted):
    from unittest.mock import AsyncMock
    from uuid import uuid4
    from app.protocol.messages import CommandRequestData
    sent=[]
    slice=SimpleNamespace(data=None,age=lambda:None)
    session=SimpleNamespace(robot_id="robot",websocket=SimpleNamespace(send_text=AsyncMock()),
        state=SimpleNamespace(connection="online",base_state=slice,pose=slice,capabilities=["software_reset"]))
    hub=SimpleNamespace(settings=SimpleNamespace(command_rate_per_min=20),db=None,
        robots={"robot":session},publish=lambda *args:sent.append(args),_next_seq=lambda:1,
        emit_event=AsyncMock())
    broker=CommandBroker(hub)
    broker.rate.allow=lambda _:False
    broker.lease.can_command=lambda *_:False
    old=ActiveCommand(command_id="old",command=previous,robot_id="robot",uncertain=uncertain)
    broker.active[old.command_id]=old
    if previous=="software_reset":broker.reset_pending["robot"]="old"
    client=SimpleNamespace(user=SimpleNamespace(id=1,username="operator",can_command=authorized))
    rid=str(uuid4())
    try:
        await broker.handle_browser_request(client,SimpleNamespace(robot_id="robot"),
            CommandRequestData(command_id=rid,command="software_reset"))
        assert bool(session.websocket.send_text.call_count)==accepted
        if accepted:
            assert list(broker.active)==[rid]
            assert broker.reset_pending["robot"]==rid
        else:
            assert list(broker.active)==["old"]
    finally:
        for entry in broker.active.values():entry.cancel_timers()
