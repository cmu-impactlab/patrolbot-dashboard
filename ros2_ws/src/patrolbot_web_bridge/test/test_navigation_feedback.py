"""Current-action path evidence, independent of default Nav2 feedback zeros."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from patrolbot_web_bridge.commands import CommandExecutor

BASE = dict(map_id='floor1', map_revision='revision', map_activated_stamp_ns=100,
            session_generation=1, odom_epoch_valid=True,
            localization_recovery_required=False, localization_seed_stamp_ns=101)
UUID = bytes(range(1, 17))


def executor():
    e = CommandExecutor.__new__(CommandExecutor)
    e._ws = Mock()
    e._node = NS(cfg={'path_max_points': 200})
    e._active = e._undock_active = None
    e._navigation_context = None
    e._last_progress_mono = 0.
    e.update_navigation_context(BASE)
    e._active = dict(command_id='current', goal_id=UUID, path_valid=False,
                     navigation_context=e._navigation_context, path_blocked=False,
                     goal_handle=Mock())
    return e


def path(token=UUID, points=((0., 0.), (1., 0.)), frame='map', stamp=102):
    return NS(goal_id=NS(uuid=list(token)), path=NS(
        header=NS(frame_id=frame, stamp=NS(sec=0, nanosec=stamp)),
        poses=[NS(pose=NS(position=NS(x=x, y=y, z=0.),
                         orientation=NS(x=0., y=0., z=0., w=1.))) for x, y in points]))


def accept_path(e, message):
    return e.navigation_path(bytes(message.goal_id.uuid), message.path)


def progress(e, distance=0., token=UUID, command='current', recoveries=0):
    e._last_progress_mono = 0.
    e._on_feedback(command, NS(goal_id=NS(uuid=list(token)), feedback=NS(
        distance_remaining=distance, number_of_recoveries=recoveries)))
    return e._ws.send.call_args.args if e._ws.send.called else None


def test_default_zero_without_current_path_is_unknown_planning():
    kind, data = progress(executor())
    assert kind == 'command.progress'
    assert data['stage'] == 'planning'
    assert data['distance_remaining'] is None
    assert data['path_valid'] is False


@pytest.mark.parametrize('distance', [2.35, 0.])
def test_valid_current_path_preserves_distance_including_real_zero(distance):
    e = executor()
    data = accept_path(e, path())
    assert data['command_id'] == 'current' and data['path_valid'] is True
    kind, data = progress(e, distance, recoveries=7)
    assert data['stage'] == 'navigating'
    assert data['distance_remaining'] == distance
    assert data['path_valid'] is True


@pytest.mark.parametrize('distance', [float('nan'), float('inf'), -1., None])
def test_invalid_distance_never_becomes_navigation_progress(distance):
    e = executor(); accept_path(e, path())
    _, data = progress(e, distance)
    assert data['stage'] == 'planning' and data['distance_remaining'] is None


def test_same_destination_from_old_action_uuid_cannot_plan_current_command():
    e = executor()
    assert accept_path(e, path(token=bytes(range(16)))) is None
    assert progress(e)[1]['path_valid'] is False
    e._ws.reset_mock()
    assert progress(e, command='old') is None
    assert progress(e, token=bytes(range(16))) is None


@pytest.mark.parametrize('field,value', [('map_id', 'floor2'), ('map_revision', 'new'),
    ('session_generation', 2), ('map_activated_stamp_ns', 200),
    ('localization_seed_stamp_ns', 300), ('odom_epoch_valid', False),
    ('localization_recovery_required', True)])
def test_map_or_restart_invalidates_path_and_does_not_revive_old_goal(field, value):
    e = executor(); accept_path(e, path())
    e.update_navigation_context(dict(BASE, **{field: value}))
    assert e._active['path_valid'] is False
    assert e._ws.send.call_args.args[1]['points'] == []
    assert accept_path(e, path()) is None
    e.update_navigation_context(BASE)
    assert accept_path(e, path()) is None


@pytest.mark.parametrize('message', [path(points=()), path(frame='odom'), path(stamp=100),
                                     path(points=((float('nan'), 0.),))])
def test_empty_wrong_frame_old_or_malformed_path_clears_validity(message):
    e = executor(); accept_path(e, path())
    data = accept_path(e, message)
    assert data['path_valid'] is False and data['points'] == []
    assert progress(e)[1]['distance_remaining'] is None


def test_stop_invalidates_path_immediately_and_late_path_cannot_restore_it():
    e = executor(); accept_path(e, path())
    e._stop('stop')
    assert e._active['path_valid'] is False
    assert accept_path(e, path()) is None


def test_terminal_result_clears_path_and_preserves_available_planner_code():
    e = executor(); accept_path(e, path())
    e._on_result('current', NS(result=lambda: NS(status=6,
        result=NS(error_code=208, error_msg='Start is isolated by map obstacles'))))
    messages = [call.args for call in e._ws.send.call_args_list]
    assert messages[-2][0] == 'telemetry.path' and messages[-2][1]['path_valid'] is False
    result = messages[-1][1]
    assert result['outcome'] == 'failed' and result['error_code'] == 208
    assert result['error_message'] == 'Start is isolated by map obstacles'
    assert '208' in result['detail']
    assert accept_path(e, path()) is None


def test_delayed_acceptance_cannot_replace_a_newer_destination():
    e = executor(); e._navigation_generation = 2
    handle = Mock(accepted=True)
    e._on_goal_response('old', NS(result=lambda:handle), navigation_generation=1)
    handle.cancel_goal_async.assert_called_once()
    assert e._active['command_id'] == 'current'
    assert e._ws.send.call_args.args[1]['accepted'] is False


def test_path_service_response_is_bound_to_request_owner_and_latest_query():
    e = executor(); e._last_navigation_path = None
    response = NS(result=lambda:NS(active=True, path=path().path))
    e._path_future = response
    old = dict(e._active)
    e._on_navigation_path(old, response)
    assert e._active['path_valid'] is False
    e._on_navigation_path(e._active, response)
    assert e._active['path_valid'] is True
    assert e._ws.send.call_args.args[0] == 'telemetry.path'
    stopped = NS(result=lambda:NS(active=False, path=path().path))
    e._path_future = stopped
    e._on_navigation_path(e._active, stopped)
    assert e._active['path_valid'] is False
    e._on_navigation_path(e._active, response)
    assert e._active['path_valid'] is False


def test_unanswered_path_query_clears_validity_and_late_response_is_ignored(monkeypatch):
    e = executor(); accept_path(e, path())
    old_future = NS(done=lambda:False)
    e._path_future = old_future; e._path_sent_mono = 1.
    e._path_client = Mock(service_is_ready=lambda:False)
    monkeypatch.setattr('patrolbot_web_bridge.commands.time.monotonic', lambda:4.)
    e._poll_navigation_path()
    e._path_client.remove_pending_request.assert_called_once_with(old_future)
    assert e._active['path_valid'] is False
    assert e._path_future is None


def cancel(e, command='current'):
    e._map_active = None
    e.handle({'command': 'cancel', 'command_id': command}, None, None)


def test_server_cancel_is_bound_to_command_and_waits_for_terminal_result():
    e = executor(); accept_path(e, path())
    handle = e._active['goal_handle']
    cancel(e, 'previous')
    handle.cancel_goal_async.assert_not_called()
    cancel(e)
    handle.cancel_goal_async.assert_called_once()
    assert e._active['path_valid'] is False
    assert e._active['command_id'] == 'current'
    assert accept_path(e, path()) is None
    assert progress(e, 1.)[1]['stage'] == 'stopping'
    assert not any(call.args[0] == 'command.result' for call in e._ws.send.call_args_list)
    e._on_result('current', NS(result=lambda: NS(status=5)))
    assert e._ws.send.call_args.args[1]['outcome'] == 'canceled'


def test_server_cancel_before_action_ack_cancels_that_accepted_goal():
    e = executor()
    e._active = None
    e._nav_pending = dict(command_id='pending', cancel=False)
    cancel(e, 'pending')
    handle = Mock(accepted=True, goal_id=NS(uuid=list(UUID)))
    e._on_goal_response('pending', NS(result=lambda: handle))
    handle.cancel_goal_async.assert_called_once()
    assert e._active['command_id'] == 'pending'
    assert e._active['path_blocked'] is True
    assert not any(call.args[0] == 'command.result' for call in e._ws.send.call_args_list)


def test_cancel_arriving_while_goal_response_is_processing_is_not_lost():
    e = executor()
    e._active = None
    e._nav_pending = dict(command_id='pending', cancel=False)
    handle = Mock(accepted=True, goal_id=NS(uuid=list(UUID)))

    def response():
        cancel(e, 'pending')
        return handle

    e._on_goal_response('pending', NS(result=response))
    handle.cancel_goal_async.assert_called_once()
    assert e._active['command_id'] == 'pending'


def test_cancel_error_keeps_navigation_tracked_until_real_result():
    e = executor()
    e._active['goal_handle'].cancel_goal_async.side_effect = RuntimeError('no server')
    cancel(e)
    assert e._active['command_id'] == 'current'
    assert not any(call.args[0] == 'command.result' for call in e._ws.send.call_args_list)
    e._on_result('current', NS(result=lambda: NS(status=4)))
    assert e._ws.send.call_args.args[1]['outcome'] == 'succeeded'


def test_stop_joins_server_cancel_and_waits_for_navigation_terminal_result():
    e = executor()
    cancel(e)
    e._stop('stop')
    e._active['goal_handle'].cancel_goal_async.assert_called_once()
    assert not any(call.args[0] == 'command.result' for call in e._ws.send.call_args_list)
    e._on_result('current', NS(result=lambda: NS(status=5)))
    result = e._ws.send.call_args.args[1]
    assert result['command_id'] == 'stop' and result['outcome'] == 'succeeded'


def test_refused_server_cancel_can_be_retried_with_stop():
    e = executor()
    cancel(e)
    e._on_navigation_cancel_response(e._active, NS(result=lambda:
        NS(goals_canceling=[], return_code=1)))
    e._stop('stop')
    assert e._active['goal_handle'].cancel_goal_async.call_count == 2


def test_generic_cancel_preserves_matching_map_transaction_semantics():
    e = executor()
    e._map_active = dict(command_id='map')
    e._check_map_connection = Mock()
    e.handle({'command': 'cancel', 'command_id': 'old-map'}, None, None)
    e._check_map_connection.assert_not_called()
    e.handle({'command': 'cancel', 'command_id': 'map'}, None, None)
    e._check_map_connection.assert_called_once_with(cancel=True)
    e._active['goal_handle'].cancel_goal_async.assert_not_called()


def test_incoming_cancel_keeps_original_command_identity():
    import asyncio
    import json
    from patrolbot_web_bridge.ws_client import WsClient

    async def frames():
        yield json.dumps({'type': 'command.cancel', 'data': {'command_id': 'old-destination'}})

    callback = Mock()
    client = WsClient('ws://unused', 'unused', 'robot', lambda: None, callback)
    asyncio.run(client._drain_incoming(frames()))
    callback.assert_called_once_with({'command': 'cancel', 'command_id': 'old-destination'})


@pytest.mark.parametrize('stage,code,message,fragment', [
    ('refreshing', 205, 'waiting for AMCL', 'Refreshing localization'),
    ('failed', 205, 'all four exits blocked', 'planning start is blocked'),
    ('failed', 305, 'waypoint blocked', 'intermediate waypoint'),
    ('failed', 207, 'costmap stale', 'costmap did not update'),
    ('failed', 307, 'costmap stale', 'costmap did not update'),
    ('failed', 208, 'search exhausted', 'No route'),
])
def test_planner_reason_survives_feedback_and_failed_replan(stage, code, message, fragment):
    e = executor(); accept_path(e, path())
    response = NS(result=lambda:NS(active=True, path=path().path,
        planning_stage=stage, planning_error_code=code, planning_error_message=message))
    e._path_future = response
    e._on_navigation_path(e._active, response)
    assert e._active['path_valid'] is False
    assert e._last_navigation_path is None
    kind, data = progress(e, distance=2.)
    assert fragment in data['detail']
    assert data['stage'] == stage and data['distance_remaining'] is None
    assert data['path_valid'] is False
    if stage == 'failed': assert message in data['detail']
    paths = [call.args[1] for call in e._ws.send.call_args_list if call.args[0] == 'telemetry.path']
    assert paths[-1]['points'] == []


def test_successful_refresh_retry_accepts_only_new_goal_owned_path():
    e = executor(); accept_path(e, path())
    def reply(stage, code):
        future = NS(result=lambda:NS(active=True, path=path().path,
            planning_stage=stage, planning_error_code=code, planning_error_message=''))
        e._path_future = future
        e._on_navigation_path(e._active, future)
    reply('refreshing', 205)
    assert progress(e)[1]['distance_remaining'] is None
    reply('planning', 0)
    assert progress(e)[1]['stage'] == 'planning'
    reply('following', 0)
    assert progress(e, distance=1.)[1]['distance_remaining'] == 1.


def test_late_path_status_cannot_override_stop_or_context_invalidation():
    for invalidate in (lambda e:e._cancel_navigation('current'),
                       lambda e:e.update_navigation_context(dict(BASE, session_generation=2))):
        e = executor()
        future = NS(result=lambda:NS(active=True, path=path().path,
            planning_stage='failed', planning_error_code=205, planning_error_message='old'))
        e._path_future = future
        invalidate(e)
        e._on_navigation_path(e._active, future)
        assert 'planning_status' not in e._active
        assert e._active['path_valid'] is False


@pytest.mark.parametrize('code,fragment', [(205, 'planning start is blocked'),
    (305, 'intermediate waypoint'), (207, 'costmap did not update'), (307, 'costmap did not update'),
    (208, 'No route'), (308, 'No route')])
def test_terminal_failure_retains_native_code_and_specific_reason(code, fragment):
    e = executor()
    e._on_result('current', NS(result=lambda:NS(status=6,
        result=NS(error_code=code, error_msg='native planner exception detail'))))
    result = e._ws.send.call_args.args[1]
    assert result['error_code'] == code and fragment in result['detail']
    assert result['error_message'] == 'native planner exception detail'
