"""Typed tuning transport is bounded and never replays across a connection."""
import json
from types import SimpleNamespace as NS
from uuid import uuid4
import pytest

from patrolbot_web_bridge.tuning import RosTuning, wrapper_reason
from patrolbot_web_bridge.teleop import TeleopSession
from patrolbot_web_bridge.normalizers import normalize_motion


class Future:
    def __init__(self):
        self.value = None
    def done(self):
        return self.value is not None
    def result(self):
        return self.value


def authorization(robot_id='fixture-robot'):
    """A shape-valid (never cryptographically checked here) claims block."""
    return {'version': 1, 'robot_id': robot_id, 'issued_at_ms': 1000,
            'expires_at_ms': 1000 + 30000, 'signature': 'a' * 64}


def wrapper(operation='read', role='observer', *, signed=False):
    data = {'request': {'contract': 'patrolbot-robot-tuning', 'version': 1,
        'operation': operation, 'request_id': str(uuid4()),
        'generation': {'capability': 'robot_tuning_v1', 'session_id': str(uuid4())}},
        'actor': {'id': 1, 'username': 'fixture', 'role': role}}
    if signed:
        data['authorization'] = authorization()
    return data


def save_wrapper(role='administrator', *, signed=True):
    data = wrapper('save', role, signed=signed)
    data['request'].update(base_revision=1,
                           changes=[{'id': 'max_linear_m_s', 'value': .1}])
    return data


def rig(*, ready=True, clock=None):
    calls, sent = [], []
    future = Future()
    client = NS(service_is_ready=lambda: ready,
                call_async=lambda request: (calls.append(request) or future))
    ws = NS(send=lambda kind, data: sent.append((kind, data)), connection_generation=1)
    extra = {'clock': clock} if clock is not None else {}
    adapter = RosTuning(NS(), ws, client_factory=lambda: client, **extra)
    return adapter, calls, sent, future, ws


def read_snapshot(adapter, future, snapshot):
    """Drive one read round-trip whose reply carries ``snapshot``."""
    data = wrapper()
    adapter.enqueue(data)
    adapter.tick()
    future.value = NS(result_json=json.dumps({
        'request_id': data['request']['request_id'], 'outcome': 'ok',
        'detail': '', 'snapshot': snapshot, 'job': None}))
    adapter.tick()


def test_read_roundtrip_uses_fixed_typed_service_only():
    adapter, calls, sent, future, ws = rig()
    data = wrapper()
    adapter.enqueue(data)
    adapter.tick()
    assert len(calls) == 1
    assert json.loads(calls[0].payload_json) == data
    response = {'request_id': data['request']['request_id'], 'outcome': 'ok',
                'detail': 'read only', 'snapshot': None, 'job': None}
    future.value = NS(result_json=json.dumps(response))
    adapter.tick()
    assert sent == [('tuning.response', response)]
    assert adapter.capabilities() == ['robot_tuning_read_v1']


def test_capabilities_advertise_write_from_a_fresh_save_flag():
    adapter, calls, sent, future, ws = rig()
    read_snapshot(adapter, future, {'save_supported': True})
    assert adapter.capabilities() == ['robot_tuning_read_v1', 'robot_tuning_write_v1']


def test_review_binding_is_relayed_intact_but_arbitrary_scope_arguments_are_refused():
    data = wrapper('apply', 'administrator', signed=True)
    data['request'].update(expected_revision=2, review={
        'digest': 'sha256:' + 'a' * 64, 'plan_hash': 'sha256:' + 'b' * 64})
    assert wrapper_reason(data) is None
    adapter, calls, _, _, _ = rig()
    adapter.enqueue(data)
    adapter.tick()
    assert json.loads(calls[0].payload_json) == data
    data['request']['review']['service'] = 'arbitrary'
    assert wrapper_reason(data) is not None


def test_full_mutation_capability_requires_activation_supported():
    adapter, calls, sent, future, ws = rig()
    read_snapshot(adapter, future, {'save_supported': True,
                                    'activation_supported': True})
    assert 'robot_tuning_v1' in adapter.capabilities()

    adapter2, calls2, sent2, future2, ws2 = rig()
    read_snapshot(adapter2, future2, {'save_supported': True})
    assert 'robot_tuning_v1' not in adapter2.capabilities()


def test_write_capability_is_not_advertised_from_a_stale_snapshot():
    clock = {'t': 0.0}
    adapter, calls, sent, future, ws = rig(clock=lambda: clock['t'])
    read_snapshot(adapter, future, {'save_supported': True,
                                    'activation_supported': True})
    clock['t'] += 4
    assert adapter.capabilities() == ['robot_tuning_read_v1']


def test_no_capability_is_advertised_when_the_service_is_not_ready():
    adapter, calls, sent, future, ws = rig(ready=False)
    read_snapshot(adapter, future, {'save_supported': True,
                                    'activation_supported': True})
    assert adapter.capabilities() == []


def test_disconnect_and_old_reply_never_replay():
    adapter, calls, sent, future, ws = rig()
    data = wrapper()
    adapter.enqueue(data)
    adapter.tick()
    adapter.disconnected()
    ws.connection_generation += 1
    future.value = NS(result_json=json.dumps({'request_id': data['request']['request_id'], 'outcome': 'ok'}))
    adapter.tick()
    assert sent == []
    assert len(calls) == 1


def test_operator_cannot_smuggle_a_mutation():
    # No role is trusted: operator is refused whether or not claims are attached.
    assert 'administrator' in wrapper_reason(save_wrapper('operator', signed=False))
    assert 'administrator' in wrapper_reason(save_wrapper('operator', signed=True))


def test_mutation_without_a_signed_authorization_is_refused():
    data = save_wrapper(signed=False)
    assert 'authorization' in wrapper_reason(data)


def test_a_shape_valid_signed_mutation_is_forwarded_to_the_typed_service():
    adapter, calls, sent, future, ws = rig()
    data = save_wrapper()
    assert wrapper_reason(data) is None
    adapter.enqueue(data)
    adapter.tick()
    assert len(calls) == 1
    assert json.loads(calls[0].payload_json) == data


@pytest.mark.parametrize('field', ['version', 'robot_id', 'issued_at_ms',
                                   'expires_at_ms', 'signature'])
def test_mutation_rejects_an_invalid_authorization_field(field):
    data = save_wrapper()
    data['authorization'][field] = {'version': True, 'robot_id': 7,
                                    'issued_at_ms': 'x', 'expires_at_ms': None,
                                    'signature': 'not-hex'}[field]
    assert wrapper_reason(data) is not None


def test_mutation_rejects_a_malformed_or_out_of_bounds_authorization():
    assert wrapper_reason(dict(save_wrapper(), authorization=None)) is not None
    assert wrapper_reason(dict(save_wrapper(), authorization={'version': 1})) is not None
    over = save_wrapper()
    over['authorization']['expires_at_ms'] = over['authorization']['issued_at_ms'] + 30001
    assert wrapper_reason(over) is not None
    empty = save_wrapper()
    empty['authorization']['expires_at_ms'] = empty['authorization']['issued_at_ms']
    assert wrapper_reason(empty) is not None
    backwards = save_wrapper()
    backwards['authorization']['issued_at_ms'] = 2000
    backwards['authorization']['expires_at_ms'] = 1000
    assert wrapper_reason(backwards) is not None
    oversized = save_wrapper()
    oversized['authorization']['robot_id'] = 'r' * 129
    assert wrapper_reason(oversized) is not None


def test_read_envelope_still_needs_no_authorization():
    assert wrapper_reason(wrapper('read')) is None


def test_none_or_unwrapped_payload_is_refused_without_raising():
    assert wrapper_reason(None) is not None
    assert wrapper_reason({'data': None}) is not None
    assert wrapper_reason({'request': None, 'actor': None}) is not None


def test_unsigned_mutation_never_reaches_the_typed_service():
    adapter, calls, sent, future, ws = rig()
    data = save_wrapper(signed=False)
    adapter.enqueue(data)
    adapter.tick()
    assert calls == []
    assert sent and sent[0][0] == 'tuning.response'
    assert sent[0][1]['outcome'] == 'refused'


@pytest.mark.parametrize('age', [float('nan'), float('inf'), -1])
def test_motion_never_fabricates_zero_on_invalid_age(age):
    twist = NS(linear=NS(x=.1), angular=NS(z=.2))
    assert normalize_motion(twist, session_generation=1, odom_epoch='epoch',
        odom_epoch_generation=1, sample_age_s=age, receipt_age_s=0) is None


def test_policy_change_ends_old_manual_input_without_rearming():
    sent, published = [], []
    policy = {'max_linear_m_s': .1, 'max_angular_rad_s': .2, 'generation': 'epoch-1',
              'received_mono': 1., 'robot_generation': 1}
    session = TeleopSession(publish=lambda *args: published.append(args),
        send=lambda *args: sent.append(args), safety=lambda: None, clock=lambda: 1.,
        policy=lambda: policy, policy_required=True)
    command = str(uuid4())
    assert session.start({'command_id': command, 'operator_authorized': True}, 'context', received_at=1.)
    policy['generation'] = 'epoch-2'
    session.tick('context')
    assert not session.active
    assert published[-1] == (0., 0., False)


def test_required_policy_does_not_gate_an_immediate_release():
    published = []
    policy = {'max_linear_m_s': .1, 'max_angular_rad_s': .2, 'generation': 'epoch-1', 'received_mono': 1.}
    holder = [policy]
    session = TeleopSession(publish=lambda *args: published.append(args), send=lambda *args: None,
        safety=lambda: None, clock=lambda: 1., policy=lambda: holder[0], policy_required=True)
    command = str(uuid4())
    assert session.start({'command_id': command, 'operator_authorized': True}, 'context', received_at=1.)
    holder[0] = None
    assert session.frame({'command_id': command, 'seq': 0, 'linear': 0., 'angular': 0., 'deadman': False}, 'context', received_at=1.)
    assert published[-1] == (0., 0., False)
