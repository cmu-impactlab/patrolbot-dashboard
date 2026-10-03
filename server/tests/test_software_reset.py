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


@pytest.mark.parametrize('changes',[
    {'motors_enabled':True},{'navigating':True},{'undock_active':True},
    {'stationary':False},{'base_state_age':None},{'base_state_age':4},
    {'pose_age':None},{'pose_age':4},{'hardware_state_valid':False},
    {'dock_state_valid':False},{'dock_state':'UNKNOWN'},{'online':False},
    {'capabilities':()},{'telemetry_age':float('nan')},
])
def test_unsafe_or_unknown_reset_is_refused(changes):
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
