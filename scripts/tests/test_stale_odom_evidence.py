"""Offline regression using production safety/session code, without ROS imports."""
import asyncio
from copy import deepcopy
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import check_teleop_receipt as checker
import teleop_lab as lab
from test_check_teleop_receipt import ROBOT, SESSION, Socket, fault_evidence


@pytest.fixture
def production(monkeypatch):
    path = Path(__file__).resolve().parents[2] / 'ros2_ws/src/patrolbot_web_bridge/patrolbot_web_bridge/teleop.py'
    spec = importlib.util.spec_from_file_location('offline_teleop', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    clock = NS(now=9.8)
    for target in (module, lab, checker):
        monkeypatch.setattr(target, 'time', NS(monotonic=lambda: clock.now))
    return module, clock


@pytest.mark.parametrize('receipt_age,detail', [
    (.495, 'Raw odometry is invalid or stale.'),
    (.505, 'Raw odometry is missing or stale.'),
])
@pytest.mark.parametrize('dispatch', ['tick', 'frame'])
@pytest.mark.parametrize('failure', [None, 'malformed', 'nonfinite', 'future', 'other-reason',
                                     'wrong-context', 'zero-publication'])
def test_production_expiry_terminal_and_retained_evidence(production, receipt_age, detail, dispatch, failure):
    module, clock = production
    base = dict(link_connected=True, hardware_state_valid=True, telemetry_age=0.,
                lidar_state='READY', lidar_scan_age=0., odom_epoch_valid=True,
                motors_enabled=True, estop_pressed=False, fault_flags=0,
                dock_state_valid=True, dock_state='CLEAR_CONFIRMED',
                bumpers_valid=True, bumpers_front=False, bumpers_rear=False)
    stamp = 9.495 if receipt_age == .495 else 9.485
    odom = NS(header=NS(stamp=NS(sec=9, nanosec=round((stamp - 9) * 1e9))),
              pose=NS(pose=NS(position=NS(x=0., y=0.), orientation=NS(x=0., y=0., z=0., w=1.))),
              twist=NS(twist=NS(linear=NS(x=.1), angular=NS(z=0.))))
    teleop = module.RosTeleop.__new__(module.RosTeleop)
    teleop.node = NS(get_clock=lambda: NS(now=lambda: NS(nanoseconds=round(clock.now * 1e9))))
    teleop.ws = NS(connected=NS(is_set=lambda: True))
    teleop._tuning_hold, teleop.manual_at = False, None
    teleop.action_active = {}
    teleop._writers = (b'path',)
    teleop._path_identity = lambda: teleop._writers
    teleop.state = lambda: (base, clock.now - .001, odom, 10. - receipt_age)
    published, frames = [], []
    def publish(*args):
        if failure == 'zero-publication' and clock.now == 10. and args == (0., 0., False):
            raise RuntimeError('injected zero publication failure')
        published.append(args)
    teleop.session = module.TeleopSession(publish=publish,
        send=lambda kind, data: frames.append(dict(type=kind, robot_id=ROBOT, data=data)),
        safety=teleop.safety, clock=lambda: clock.now)
    state = dict(fault='healthy')
    lab.observe_odom_fault(teleop, state)
    teleop.idle_at = clock.now
    assert teleop.session.start(dict(command_id=SESSION, operator_authorized=True), 'context', received_at=clock.now)
    clock.now = 9.98
    teleop.idle_at = clock.now
    velocity = dict(command_id=SESSION, seq=1, linear=.1, angular=0., deadman=True)
    assert teleop.session.frame(velocity, 'context', received_at=clock.now)
    state.update(fault='odom_stale', fault_injected_mono=9.99)
    clock.now = 10.
    if failure == 'malformed':
        del odom.pose
    elif failure == 'nonfinite':
        odom.pose.pose.position.x = float('nan')
    elif failure == 'future':
        odom.header.stamp.sec = 11
    elif failure == 'other-reason':
        teleop._tuning_hold = True
    context = 'wrong' if failure == 'wrong-context' else 'context'
    if dispatch == 'tick':
        teleop.session.tick(context)
    else:
        teleop.session.frame(dict(velocity, seq=2), context, received_at=clock.now)
    assert not teleop.session.active
    terminal = frames[-1]
    evidence = deepcopy(state.get('odom_fault_evidence'))
    if failure is not None:
        with pytest.raises(AssertionError):
            asyncio.run(checker.expected_stale_odom_fault(Socket([terminal]), ROBOT, SESSION, evidence, 9.99, timeout=.03))
        return
    assert published[-1] == (0., 0., False)
    assert terminal['data'] == dict(command_id=SESSION, outcome='failed', detail=detail)
    assert checker.stale_odom_detail(evidence, ROBOT, SESSION, 9.99) == detail
    assert evidence['observed_mono'] - evidence['odom_at'] == pytest.approx(receipt_age)
    assert (evidence['source_after_ns'] - (evidence['stamp_sec'] * 10**9 + evidence['stamp_nanosec'])) / 1e9 == pytest.approx(10. - stamp)
    # Later receipt expiry changes readiness's reason, never the emitted terminal.
    clock.now = 10.1
    assert teleop.safety() == 'Raw odometry is missing or stale.'
    teleop.session.tick('context')
    assert state['odom_fault_evidence'] == evidence and frames[-1] == terminal
    asyncio.run(checker.expected_stale_odom_fault(Socket([terminal]), ROBOT, SESSION, evidence, 9.99, timeout=.03))
    with pytest.raises(AssertionError):
        checker.terminal_id(terminal, ROBOT, {SESSION: {'succeeded', 'canceled'}})


@pytest.mark.parametrize('field,value', [
    ('robot_id', 'other'), ('command_id', 'other'), ('fault', 'healthy'),
    ('reason', 'Raw odometry is invalid.'), ('reason', 'Other safety failure'),
    ('reason', 'Raw odometry is invalid or stale.'),  # receipt already expired
    ('odom_values', None), ('odom_values', []), ('odom_values', [0.] * 7),
    ('odom_values', [float('nan')] * 8), ('odom_values', [float('inf')] * 8),
    ('odom_values', ['0'] * 8), ('odom_values', [True] * 8),
    ('stamp_sec', None), ('stamp_sec', -1), ('stamp_sec', 2),
    ('stamp_nanosec', 10**9), ('stamp_nanosec', True), ('stamp_nanosec', float('nan')),
    ('source_before_ns', 300000000), ('source_after_ns', 300000000),
    ('source_after_ns', float('inf')), ('source_before_ns', None),
    ('injected_mono', -.1), ('injected_mono', .7),
    ('safety_started_mono', -.1), ('observed_mono', .5), ('terminal_mono', .5),
    ('terminal_mono', 2.), ('base_at', 0.), ('base_at', .7), ('base_at', None),
    ('odom_at', None), ('odom_at', .7), ('odom_at', .5), ('odom_at', float('nan')),
    ('terminal', None),
    ('terminal', dict(command_id='other', outcome='failed', detail='Raw odometry is missing or stale.')),
    *[('terminal', dict(command_id=SESSION, outcome=outcome, detail='Raw odometry is missing or stale.'))
      for outcome in ('canceled', 'succeeded')],
    ('terminal', dict(command_id=SESSION, outcome='failed', detail='Raw odometry is missing or stale. Zero publication failed; physical stop is not confirmed.')),
])
def test_malformed_or_unrelated_fault_evidence_rejected(monkeypatch, field, value):
    monkeypatch.setattr(checker, 'time', NS(monotonic=lambda: 1.))
    evidence = fault_evidence()
    evidence[field] = value
    with pytest.raises(AssertionError):
        checker.stale_odom_detail(evidence, ROBOT, SESSION, 0.)


@pytest.mark.parametrize('reason', ['Raw odometry is missing or stale.', 'Raw odometry is invalid or stale.'])
def test_fresh_odometry_cannot_attest_expiry(monkeypatch, reason):
    monkeypatch.setattr(checker, 'time', NS(monotonic=lambda: 1.))
    evidence = fault_evidence()
    evidence.update(reason=reason, odom_at=.5, stamp_nanosec=900000000,
                    terminal=dict(command_id=SESSION, outcome='failed', detail=reason))
    with pytest.raises(AssertionError):
        checker.stale_odom_detail(evidence, ROBOT, SESSION, 0.)


@pytest.mark.parametrize('reason', ['Raw odometry is missing or stale.', 'Raw odometry is invalid or stale.'])
def test_receipt_crossing_during_safety_uses_retained_exact_reason(monkeypatch, reason):
    monkeypatch.setattr(checker, 'time', NS(monotonic=lambda: 1.))
    evidence = fault_evidence()
    evidence.update(safety_started_mono=.599, observed_mono=.601, terminal_mono=.601,
                    odom_at=.1, reason=reason,
                    terminal=dict(command_id=SESSION, outcome='failed', detail=reason))
    assert checker.stale_odom_detail(evidence, ROBOT, SESSION, 0.) == reason
