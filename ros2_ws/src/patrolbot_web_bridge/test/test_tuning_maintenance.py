"""Exact-job local admission/cache evidence; no robot, key or ROS graph."""
import json
from types import SimpleNamespace as NS

import pytest

from patrolbot_web_bridge.teleop import RosTeleop, TeleopSession
from patrolbot_web_bridge import teleop as teleop_module
from patrolbot_web_bridge.tuning import RosTuning, maintenance_payload


OWNER = dict(job_id='11111111-1111-4111-8111-111111111111',
             digest='sha256:' + 'a' * 64, plan_hash='sha256:' + 'b' * 64)
EXPECTED = dict(version=1, revision=2, session_id='22222222-2222-4222-8222-222222222222',
                hardware_session_generation=7,
                effective_limits=dict(max_linear_m_s=.15, max_angular_rad_s=.35))


@pytest.fixture
def rig():
    now = [10.]
    node = NS(_latest_base_state={'session_generation': 7})
    tuning = RosTuning(node, NS(), client_factory=lambda: NS(service_is_ready=lambda: True),
                       clock=lambda: now[0])
    node._tuning = tuning
    tuning._snapshot_at = now[0]
    tuning._snapshot = dict(session_id=EXPECTED['session_id'],
        active_known=True, active_revision=1, committed_active_revision=1,
        digest='sha256:' + 'c' * 64, hardware_session_generation=7,
        effective_limits=dict(max_linear_m_s=.30, max_angular_rad_s=.70),
        maintenance_policy=dict(OWNER, **EXPECTED, phase='candidate', runtime_verified=True,
                                command_hold=True, observed_age_s=0., committed_active_revision=None))
    teleop = RosTeleop.__new__(RosTeleop)
    teleop.node = node
    teleop._maintenance_owner = teleop._maintenance_released = None
    teleop._maintenance_staged = None
    teleop._maintenance_committed = False
    teleop._tuning_hold = teleop._tuning_legacy_hold = False
    teleop._last_output = (0., 0., False)
    teleop._maintenance_source_stamp = 0
    teleop._maintenance_source_gid = None
    teleop.policy = tuning.teleop_policy
    sent, published = [], []
    teleop.session = TeleopSession(publish=lambda *args: published.append(args),
        send=lambda *args: sent.append(args), clock=lambda: now[0],
        safety=lambda: 'maintenance owns input' if teleop._tuning_hold else None)
    return NS(teleop=teleop, tuning=tuning, now=now, sent=sent, published=published)


def call(rig, operation='acquire', *, payload=None, **owner_changes):
    request = NS(operation=operation, **dict(OWNER, **owner_changes),
                 payload_json='' if payload is None else json.dumps(payload))
    return rig.teleop._tuning_maintenance(request, NS())


def commit_snapshot(rig):
    rig.tuning._snapshot.update(active_revision=2, committed_active_revision=2,
        digest=OWNER['digest'], effective_limits=EXPECTED['effective_limits'])


def test_exact_acquire_inspect_release_and_no_legacy_false_override(rig):
    assert call(rig).accepted
    assert call(rig).accepted
    for operation in ('acquire', 'inspect', 'release', 'stage', 'committed'):
        assert not call(rig, operation, digest='sha256:' + 'd' * 64).accepted
        assert rig.teleop._tuning_hold
    rig.teleop._on_tuning_hold(NS(data=False))
    assert rig.teleop._tuning_hold
    data = json.loads(call(rig, 'inspect').evidence_json)
    assert data['owner'] == OWNER and data['idle'] is True
    assert data['input_released'] is True and data['session_active'] is False
    assert data['buffers'] == [0., 0., False]
    assert call(rig, 'release').accepted
    assert call(rig, 'release').accepted
    assert not call(rig).accepted
    assert not rig.teleop._tuning_hold


@pytest.mark.parametrize('output', [None, (.1, 0., True), (0., 0., True)])
def test_missing_or_unreleased_output_is_not_idle(rig, output):
    rig.teleop._last_output = output
    assert not call(rig).accepted
    assert not rig.teleop._tuning_hold


def test_live_or_preparing_session_refuses_without_cancelling_it(rig):
    session = rig.teleop.session
    assert session.start({'operator_authorized': True, 'command_id': OWNER['job_id']},
                         'fixture', received_at=rig.now[0], defer_ack=True)
    assert not call(rig).accepted
    assert session.active and not session.acknowledged
    assert not rig.teleop._tuning_hold


def test_staging_is_not_active_policy_or_permission_and_commit_is_required(rig):
    assert not call(rig, 'stage', payload=EXPECTED).accepted
    assert call(rig).accepted
    assert call(rig, 'stage', payload=EXPECTED).accepted
    inspected = json.loads(call(rig, 'inspect').evidence_json)
    assert inspected['staged_policy'] == EXPECTED and inspected['committed'] is False
    assert inspected['policy']['max_linear_m_s'] == .30  # ACTIVE, never staged DESIRED.
    assert not call(rig, 'release').accepted
    assert not call(rig, 'committed', payload=EXPECTED).accepted
    assert rig.teleop._tuning_hold
    commit_snapshot(rig)
    assert call(rig, 'committed', payload=EXPECTED).accepted
    assert rig.teleop._tuning_hold
    assert call(rig, 'release').accepted
    assert not rig.teleop._tuning_hold
    assert not rig.teleop.session.active and rig.published == []


@pytest.mark.parametrize('fault', ['missing', 'foreign', 'unheld', 'unverified', 'old', 'generation', 'caps', 'desired'])
def test_stage_needs_fresh_exact_core_runtime_observation_not_desired(rig, fault):
    assert call(rig).accepted
    snapshot = rig.tuning._snapshot
    observed = snapshot['maintenance_policy']
    if fault == 'missing':
        del snapshot['maintenance_policy']
    elif fault == 'foreign':
        observed['plan_hash'] = 'sha256:' + 'f' * 64
    elif fault == 'unheld':
        observed['command_hold'] = False
    elif fault == 'unverified':
        observed['runtime_verified'] = False
    elif fault == 'old':
        observed['observed_age_s'] = 3.01
    elif fault == 'generation':
        rig.teleop.node._latest_base_state['session_generation'] += 1
    elif fault == 'caps':
        observed['effective_limits'] = dict(max_linear_m_s=.2, max_angular_rad_s=.35)
    else:
        snapshot['desired'] = EXPECTED['effective_limits']
        del snapshot['maintenance_policy']
    assert not call(rig, 'stage', payload=EXPECTED).accepted
    assert rig.teleop._tuning_hold
    assert rig.teleop._maintenance_staged is None


@pytest.mark.parametrize('fault', ['disconnect', 'stale', 'generation', 'active_unknown', 'digest'])
def test_commit_or_release_uncertainty_never_drops_hold(rig, fault):
    assert call(rig).accepted
    assert call(rig, 'stage', payload=EXPECTED).accepted
    commit_snapshot(rig)
    assert call(rig, 'committed', payload=EXPECTED).accepted
    if fault == 'disconnect':
        rig.tuning.disconnected()
    elif fault == 'stale':
        rig.now[0] += 3.01
    elif fault == 'generation':
        rig.teleop.node._latest_base_state['session_generation'] += 1
    elif fault == 'active_unknown':
        rig.tuning._snapshot['active_known'] = False
    else:
        rig.tuning._snapshot['digest'] = 'sha256:' + 'f' * 64
    assert not call(rig, 'release').accepted
    assert rig.teleop._maintenance_owner == OWNER and rig.teleop._tuning_hold


def test_release_needs_new_arm_not_a_queued_premaintenance_start(rig):
    old_receipt = rig.now[0]
    assert call(rig).accepted
    assert not rig.teleop.session.start({'operator_authorized': True, 'command_id': OWNER['job_id']},
                                       'fixture', received_at=old_receipt)
    assert call(rig, 'release').accepted
    assert not rig.teleop.session.start({'operator_authorized': True, 'command_id': OWNER['job_id']},
                                       'fixture', received_at=old_receipt)
    rig.now[0] += .01
    assert rig.teleop.session.start({'operator_authorized': True, 'command_id': OWNER['job_id']},
                                   'fixture', received_at=rig.now[0])


def test_release_preserves_unrelated_hold(rig):
    assert call(rig).accepted
    rig.teleop._on_tuning_hold(NS(data=True))
    assert call(rig, 'release').accepted
    assert rig.teleop._tuning_hold


def test_observe_is_read_only_and_block_requires_the_closed_core_input_gate(rig):
    observed = json.loads(call(rig, 'observe').evidence_json)
    assert observed['owner'] is None and not observed['input_blocked']
    assert not call(rig, 'block').accepted
    assert rig.teleop._maintenance_owner is None
    rig.teleop._on_tuning_hold(NS(data=True))
    assert rig.teleop.node._tuning_input_blocked
    assert call(rig, 'block').accepted
    for operation in ('block', 'release'):
        assert not call(rig, operation, plan_hash='sha256:' + 'f' * 64).accepted
    observed = json.loads(call(rig, 'observe').evidence_json)
    assert observed['input_blocked'] and observed['owner'] == OWNER
    assert call(rig, 'release').accepted
    assert rig.teleop.node._tuning_input_blocked  # core hold still owns ingress
    rig.teleop._on_tuning_hold(NS(data=False))
    assert not rig.teleop.node._tuning_input_blocked
    assert not rig.teleop.session.active


def topic(rig, monkeypatch, *, phase='candidate', changes=None, info_changes=None):
    gid = b'fixture-core-gid'
    endpoints = [NS(node_name='patrolbot_bridge', node_namespace='/',
                    topic_type='std_msgs/msg/String', endpoint_gid=gid)]
    rig.teleop.node.get_publishers_info_by_topic = lambda _: endpoints
    rig.teleop.node.get_clock = lambda: NS(now=lambda: NS(nanoseconds=int(rig.now[0] * 1e9)))
    monkeypatch.setattr(teleop_module, 'time', NS(monotonic=lambda: rig.now[0]))
    observed = dict(OWNER, **EXPECTED, phase=phase, runtime_verified=True, command_hold=True,
                    observed_age_s=0., committed_active_revision=2 if phase == 'committed' else None)
    observed.update(changes or {})
    info = dict(source_timestamp=int(rig.now[0] * 1e9), publisher_gid=gid)
    info.update(info_changes or {})
    rig.teleop._on_maintenance_policy(NS(data=json.dumps(observed)), info)
    return endpoints


def test_private_topic_observation_avoids_apply_writer_read_cycle_and_never_activates_candidate(rig, monkeypatch):
    assert call(rig).accepted
    rig.tuning._snapshot_at -= 10.  # Ordinary read can be blocked behind apply's writer.
    topic(rig, monkeypatch)
    assert call(rig, 'stage', payload=EXPECTED).accepted
    assert rig.tuning.teleop_policy()['max_linear_m_s'] == .30
    rig.now[0] += .01
    topic(rig, monkeypatch, phase='committed')
    assert call(rig, 'committed', payload=EXPECTED).accepted
    # Only independently verified committed ACTIVE can refresh the drive cache.
    assert rig.tuning.teleop_policy()['max_linear_m_s'] == .15
    assert rig.teleop._tuning_hold and not rig.teleop.session.active
    assert call(rig, 'release').accepted
    assert not rig.teleop.session.active


@pytest.mark.parametrize('fault', ['foreign_gid', 'old', 'future', 'duplicate', 'foreign_node', 'replacement'])
def test_private_topic_identity_replay_and_freshness_are_fail_closed(rig, monkeypatch, fault):
    assert call(rig).accepted
    endpoints = topic(rig, monkeypatch)
    assert call(rig, 'stage', payload=EXPECTED).accepted
    if fault == 'foreign_gid':
        topic(rig, monkeypatch, info_changes={'publisher_gid': b'foreign'})
    elif fault == 'old':
        topic(rig, monkeypatch, info_changes={'source_timestamp': int((rig.now[0] - .501) * 1e9)})
    elif fault == 'future':
        topic(rig, monkeypatch, info_changes={'source_timestamp': int((rig.now[0] + .001) * 1e9)})
    elif fault == 'duplicate':
        topic(rig, monkeypatch)  # Same source stamp cannot refresh observation age.
    elif fault == 'foreign_node':
        endpoints[0].node_name = 'untrusted'
    else:
        endpoints[0].endpoint_gid = b'replacement-core'
    # Remove snapshot fallback; invalid topic data cannot fabricate live proof.
    rig.tuning._snapshot.pop('maintenance_policy')
    assert not call(rig, 'stage', payload=EXPECTED).accepted
    assert rig.teleop._tuning_hold


def test_zero_publication_failure_cannot_acknowledge_release(rig):
    assert call(rig).accepted
    rig.teleop._last_output = None
    assert not call(rig, 'release').accepted
    assert rig.teleop._tuning_hold


@pytest.mark.parametrize('raw', ['{"version":1,"version":1}', 'NaN', '[]', 'x' * 2049])
def test_payload_is_bounded_strict_json(raw):
    with pytest.raises(ValueError):
        maintenance_payload(raw)


@pytest.mark.parametrize('changes', [{'version': True}, {'revision': True}, {'revision': 0},
    {'session_id': '../bad'}, {'hardware_session_generation': False},
    {'effective_limits': {'max_linear_m_s': .301, 'max_angular_rad_s': .7}},
    {'effective_limits': {'max_linear_m_s': float('nan'), 'max_angular_rad_s': .7}},
    {'effective_limits': {'max_linear_m_s': True, 'max_angular_rad_s': .7}}, {'path': '/unapproved'}])
def test_payload_cannot_expand_envelope_or_select_paths(changes):
    with pytest.raises(ValueError):
        maintenance_payload(json.dumps(dict(EXPECTED, **changes)))
