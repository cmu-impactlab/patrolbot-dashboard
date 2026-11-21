"""Receipt correlation only: no ROS, network, Docker or hardware."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import check_teleop_receipt as checker


ROBOT = "isolated-web-teleop"
SESSION = "00000000-0000-4000-8000-000000000001"
STOP = "00000000-0000-4000-8000-000000000002"
OLD_STOP = "00000000-0000-4000-8000-000000000003"


def row(command_id, command):
    return dict(robot_id=ROBOT, command_id=command_id, command=command,
                user_id=1, username="local", source_ip="127.0.0.1",
                session_generation=17)


def audit():
    return [row(STOP, "teleop_stop"), row(SESSION, "teleop_start"),
            row(OLD_STOP, "teleop_stop")]


def result(command_id=SESSION, outcome="canceled", robot=ROBOT):
    return dict(type="command.result", robot_id=robot,
                data=dict(command_id=command_id, outcome=outcome))


class Socket:
    def __init__(self, frames):
        self.frames = list(frames)

    async def recv(self):
        if self.frames:
            return json.dumps(self.frames.pop(0))
        await asyncio.Event().wait()


class Client:
    def __init__(self, records=None):
        self.records = audit() if records is None else records

    async def get(self, path):
        assert path == "/api/commands?limit=500"
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: self.records)


def finish(frames, *, records=None, expect_stop=True):
    return asyncio.run(checker.finish(Socket(frames), Client(records), ROBOT,
                                      SESSION, expect_stop, timeout=.03))


def test_audit_resolves_only_stop_after_exact_start():
    assert checker.session_stop_id(audit(), ROBOT, SESSION) == STOP
    assert checker.session_stop_id(audit()[1:], ROBOT, SESSION) is None


@pytest.mark.parametrize("field,value", [
    ("command", "stop"), ("command", "teleop_start"),
    ("session_generation", 18), ("user_id", 2),
    ("username", "other"), ("source_ip", "other"),
    ("rejection_reason", "refused"), ("command_id", ""),
])
def test_audit_rejects_wrong_stop_identity(field, value):
    records = audit()
    records[0][field] = value
    with pytest.raises(AssertionError):
        checker.session_stop_id(records, ROBOT, SESSION)


@pytest.mark.parametrize("field,value", [
    ("command_id", OLD_STOP), ("robot_id", "other"), ("command", "stop"),
    ("user_id", None), ("session_generation", None),
])
def test_audit_rejects_missing_or_wrong_session_identity(field, value):
    records = audit()
    records[1][field] = value
    with pytest.raises(AssertionError):
        checker.session_stop_id(records, ROBOT, SESSION)


def test_audit_rejects_multiple_intervening_commands():
    records = [row(OLD_STOP, "teleop_stop"), *audit()]
    with pytest.raises(AssertionError, match="Ambiguous"):
        checker.session_stop_id(records, ROBOT, SESSION)


@pytest.mark.parametrize("outcome", ["succeeded", "canceled"])
@pytest.mark.parametrize("reverse", [False, True])
def test_release_and_watchdog_allow_both_terminal_orders_and_duplicates(outcome, reverse):
    # Bridge release succeeds; end()/watchdog revocation cancels the start.
    frames = [result(outcome=outcome), result(STOP, "succeeded")]
    if reverse:
        frames.reverse()
    finish([frames[0], frames[0], *frames[1:]])


@pytest.mark.parametrize("outcome", ["failed", "rejected", "timeout", None, "unknown"])
@pytest.mark.parametrize("command_id", [SESSION, STOP])
def test_unsuccessful_expected_terminals_fail(command_id, outcome):
    frames = [result(command_id, outcome), result(), result(STOP, "succeeded")]
    with pytest.raises(AssertionError):
        finish(frames)


def test_canceled_stop_is_not_successful():
    with pytest.raises(AssertionError):
        finish([result(), result(STOP, "canceled")])


def test_original_failed_session_and_unrelated_failed_command_regression():
    with pytest.raises(AssertionError):
        finish([result(outcome="failed"), result(OLD_STOP, "failed")])


@pytest.mark.parametrize("frames", [
    [], [result()], [result(STOP, "succeeded")],
    [result(), result(OLD_STOP, "succeeded")],
    [result(), result(STOP, "succeeded", robot="other")],
    [result(command_id=OLD_STOP), result(STOP, "succeeded")],
    [result(robot="other"), result(STOP, "succeeded")],
    [result(), dict(type="command.ack", robot_id=ROBOT,
                    data=dict(command_id=STOP, accepted=True))],
])
def test_missing_or_unrelated_terminals_cannot_close_session(frames):
    with pytest.raises((AssertionError, asyncio.TimeoutError)):
        finish(frames)


def test_previous_session_stop_is_not_a_candidate_even_with_successful_result():
    with pytest.raises((AssertionError, asyncio.TimeoutError)):
        finish([result(), result(OLD_STOP, "succeeded")], records=audit()[1:])


def test_stop_for_other_robot_is_not_a_candidate():
    records = audit()
    records[0]["robot_id"] = "other"
    assert checker.session_stop_id(records, ROBOT, SESSION) is None


def test_f710_self_closes_on_canceled_session_without_audit_or_stop():
    # No-stop mode must never consult the audit endpoint.
    finish([result(OLD_STOP, "failed"), result()], records=[], expect_stop=False)


@pytest.mark.parametrize("outcome", ["succeeded", "failed", "rejected", "timeout"])
def test_f710_requires_cancellation(outcome):
    with pytest.raises(AssertionError):
        finish([result(outcome=outcome)], expect_stop=False)


def test_unrelated_messages_do_not_reset_deadline(monkeypatch):
    clock = SimpleNamespace(now=0.)
    monkeypatch.setattr(checker, "time", SimpleNamespace(monotonic=lambda: clock.now))

    class NoisySocket:
        calls = 0

        async def recv(self):
            self.calls += 1
            clock.now += 1.
            return json.dumps(result(OLD_STOP, "succeeded"))

    socket = NoisySocket()
    with pytest.raises(AssertionError, match="expected terminals"):
        asyncio.run(checker.finish(socket, Client(), ROBOT, SESSION, True, timeout=3))
    assert socket.calls == 3


def test_audit_polling_and_receives_share_one_budget(monkeypatch):
    clock = SimpleNamespace(now=0.)
    monkeypatch.setattr(checker, "time", SimpleNamespace(monotonic=lambda: clock.now))

    class SlowClient(Client):
        async def get(self, path):
            clock.now += 2.
            return await super().get(path)

    class LateSocket:
        async def recv(self):
            clock.now += 2.
            return json.dumps(result())

    with pytest.raises(AssertionError, match="expected terminals"):
        asyncio.run(checker.finish(LateSocket(), SlowClient(), ROBOT, SESSION, True, timeout=3))


def test_silent_socket_is_bounded_by_remaining_budget(monkeypatch):
    timeouts = []
    wait_for = asyncio.wait_for

    async def capture(awaitable, timeout):
        timeouts.append(timeout)
        return await wait_for(awaitable, timeout)

    monkeypatch.setattr(checker.asyncio, "wait_for", capture)
    with pytest.raises(asyncio.TimeoutError):
        finish([])
    assert 0 < timeouts[-1] <= timeouts[0] <= .03


class WatchdogLab:
    """Advance virtual time on receives; audit and results can settle independently."""
    def __init__(self, monkeypatch, frames=(), stop_at=None, completed_at=0.):
        self.now = 0.
        self.frames = list(frames)
        self.stop_at = stop_at
        self.completed_at = completed_at
        self.audits = []
        monkeypatch.setattr(checker, "time", SimpleNamespace(monotonic=lambda: self.now))

    async def recv(self):
        self.now = round(self.now + .01, 8)
        if self.frames:
            return json.dumps(self.frames.pop(0))
        raise asyncio.TimeoutError

    async def get(self, path):
        assert path == "/api/commands?limit=500"
        self.audits.append(self.now)
        records = audit() if self.stop_at is not None and self.now >= self.stop_at else audit()[1:]
        if self.completed_at is not None and self.now >= self.completed_at:
            records[-2].update(outcome="canceled", completed_at="2026-11-20T00:00:00Z")
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: records)

    def finish(self, timeout=2.):
        asyncio.run(checker.finish(self, self, ROBOT, SESSION, None, timeout=timeout))


def test_robot_watchdog_first_requires_completed_audit_and_final_confirmation(monkeypatch):
    lab = WatchdogLab(monkeypatch, [result()], completed_at=.7)
    lab.finish()
    assert lab.audits[-1] >= 1.98
    assert lab.audits[-1] == lab.now < 2.


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("outcome", ["succeeded", "canceled"])
def test_server_watchdog_first_requires_exact_stop_success(monkeypatch, reverse, outcome):
    frames = [result(outcome=outcome), result(STOP, "succeeded")]
    lab = WatchdogLab(monkeypatch, frames[::-1] if reverse else frames, stop_at=0.)
    lab.finish()
    assert lab.now < .5


def test_stop_audited_after_start_terminal_during_settlement(monkeypatch):
    # Even the stop result can arrive before the audit endpoint exposes its row.
    lab = WatchdogLab(monkeypatch, [result(), result(STOP, "succeeded")], stop_at=.4)
    lab.finish()
    assert lab.now >= .4


@pytest.mark.parametrize("stop_at", [0., .4])
@pytest.mark.parametrize("outcome", ["failed", "timeout", "canceled", "rejected"])
def test_producer_loss_never_hides_failed_exact_stop(monkeypatch, stop_at, outcome):
    lab = WatchdogLab(monkeypatch,
                      [result(), result(STOP, outcome), result(OLD_STOP, "succeeded")],
                      stop_at=stop_at)
    with pytest.raises(AssertionError):
        lab.finish()


@pytest.mark.parametrize("frames", [
    [result(), result(OLD_STOP, "succeeded")],
    [result(), result(STOP, "succeeded", robot="other")],
    [result(OLD_STOP), result(STOP, "succeeded")],
    [result(), dict(type="command.ack", robot_id=ROBOT,
                    data=dict(command_id=STOP, accepted=True))],
])
def test_producer_loss_unrelated_results_cannot_replace_expected_terminal(monkeypatch, frames):
    lab = WatchdogLab(monkeypatch, frames, stop_at=.4)
    with pytest.raises((AssertionError, asyncio.TimeoutError)):
        lab.finish()
    assert lab.now <= 2.1


@pytest.mark.parametrize("outcome", ["failed", "timeout", "rejected", "succeeded"])
def test_robot_watchdog_without_stop_requires_cancellation(monkeypatch, outcome):
    lab = WatchdogLab(monkeypatch, [result(outcome=outcome)])
    with pytest.raises(AssertionError):
        lab.finish()


def test_unrelated_failed_stop_does_not_prevent_exact_no_stop_cancellation(monkeypatch):
    lab = WatchdogLab(monkeypatch, [result(OLD_STOP, "failed"), result()])
    lab.finish()


def test_unfinished_start_audit_cannot_confirm_no_stop(monkeypatch):
    lab = WatchdogLab(monkeypatch, [result()], completed_at=None)
    with pytest.raises(AssertionError, match="expected terminals"):
        lab.finish()


def test_late_audited_stop_cannot_succeed_after_budget_expires(monkeypatch):
    lab = WatchdogLab(monkeypatch, [result()], stop_at=.4)
    # An exact successful result received after the deadline must still fail.
    recv = lab.recv

    async def late_recv():
        if lab.now >= .4:
            lab.now = 2.1
            return json.dumps(result(STOP, "succeeded"))
        return await recv()

    lab.recv = late_recv
    with pytest.raises(AssertionError, match="expected terminals"):
        lab.finish()


def test_producer_loss_deadline_noise_does_not_extend_settlement(monkeypatch):
    lab = WatchdogLab(monkeypatch, [result(OLD_STOP, "succeeded")] * 500)
    with pytest.raises(AssertionError, match="expected terminals"):
        lab.finish()
    assert lab.now <= 2.1


def test_slow_final_audit_cannot_succeed_after_deadline(monkeypatch):
    lab = WatchdogLab(monkeypatch, [result()])
    get = lab.get

    async def late_get(path):
        if lab.now >= 1.98:
            lab.now = 2.1
        return await get(path)

    lab.get = late_get
    with pytest.raises(AssertionError, match="expected terminals"):
        lab.finish()


def test_stop_audited_near_deadline_still_requires_its_terminal(monkeypatch):
    lab = WatchdogLab(monkeypatch, [result(), result(OLD_STOP, "succeeded")], stop_at=1.9)
    with pytest.raises((AssertionError, asyncio.TimeoutError)):
        lab.finish()
    assert lab.now >= 1.9


def test_early_exact_stop_success_is_kept_until_near_deadline_audit(monkeypatch):
    lab = WatchdogLab(monkeypatch, [result(), result(STOP, "succeeded")], stop_at=1.9)
    lab.finish()
    assert 1.9 <= lab.now < 2.


def test_cancellation_too_late_for_settlement_cannot_pass(monkeypatch):
    lab = WatchdogLab(monkeypatch, [result()], completed_at=1.8)
    with pytest.raises(AssertionError, match="expected terminals"):
        lab.finish()


def stale_result():
    frame = result(outcome='failed')
    frame['data']['detail'] = 'Raw odometry is missing or stale.'
    return frame


def fault_evidence(command_id=SESSION, after=0.):
    return dict(robot_id=ROBOT, command_id=command_id, fault='odom_stale',
                injected_mono=after, safety_started_mono=after + .6,
                observed_mono=after + .6, terminal_mono=after + .6,
                base_at=after + .59, odom_at=after + .01,
                source_before_ns=10**9, source_after_ns=10**9,
                stamp_sec=0, stamp_nanosec=400000000, odom_values=[0.] * 7 + [1.],
                reason='Raw odometry is missing or stale.',
                terminal=dict(command_id=command_id, outcome='failed', detail='Raw odometry is missing or stale.'))


def test_deliberate_stale_fault_requires_exact_terminal():
    asyncio.run(checker.expected_stale_odom_fault(Socket([stale_result()]), ROBOT, SESSION, fault_evidence(), 0., timeout=.03))


@pytest.mark.parametrize('detail', ['Raw odometry is missing or stale.', 'Raw odometry is invalid or stale.'])
def test_healthy_finish_still_rejects_exact_stale_fault(detail):
    frame = stale_result()
    frame['data']['detail'] = detail
    with pytest.raises(AssertionError):
        finish([frame, result(STOP, 'succeeded')])


@pytest.mark.parametrize('field,value', [
    ('robot_id', 'other'), ('command_id', OLD_STOP), ('type', 'command.ack'),
    ('outcome', 'canceled'), ('outcome', 'succeeded'), ('outcome', 'timeout'),
    ('outcome', None), ('detail', None), ('detail', ''),
    ('detail', 'Raw odometry is invalid or stale.'),
    ('detail', 'Raw odometry is missing or stale. Zero publication failed; physical stop is not confirmed.'),
    ('reason', 'Raw odometry is missing or stale.'),
])
def test_deliberate_stale_fault_rejects_wrong_identity_outcome_or_reason(field, value):
    frame = stale_result()
    if field in {'robot_id', 'type'}:
        frame[field] = value
    else:
        frame['data'][field] = value
        if field == 'reason':
            del frame['data']['detail']
    with pytest.raises((AssertionError, asyncio.TimeoutError)):
        asyncio.run(checker.expected_stale_odom_fault(Socket([frame]), ROBOT, SESSION, fault_evidence(), 0., timeout=.03))


def test_deliberate_stale_fault_noise_cannot_extend_deadline(monkeypatch):
    clock = SimpleNamespace(now=0.)
    monkeypatch.setattr(checker, 'time', SimpleNamespace(monotonic=lambda: clock.now))

    class NoisySocket:
        async def recv(self):
            clock.now += 1.
            return json.dumps(result(OLD_STOP, 'failed'))

    with pytest.raises(AssertionError, match='Late stale-odometry terminal'):
        evidence = fault_evidence()
        evidence.update(injected_mono=-1., safety_started_mono=0., observed_mono=0., terminal_mono=0., base_at=-.01, odom_at=-.6)
        asyncio.run(checker.expected_stale_odom_fault(NoisySocket(), ROBOT, SESSION, evidence, -1., timeout=3))
    assert clock.now == 3.


@pytest.mark.parametrize('failure', [None, 'no_stop', 'stop_failed', 'wrong_identity',
    'missing_start_completion', 'missing_stop_completion', 'wrong_detail', 'wrong_outcome',
    'unrelated_active', 'owner', 'admission', 'revoke', 'restored', 'budget'])
def test_fault_completion_waits_for_db_and_late_detached_revoke(monkeypatch, failure):
    clock = SimpleNamespace(now=0.)
    monkeypatch.setattr(checker, 'time', SimpleNamespace(monotonic=lambda: clock.now))
    observations = []

    async def sleep(delay):
        clock.now = round(clock.now + delay, 8)
    monkeypatch.setattr(checker.asyncio, 'sleep', sleep)

    class CompletionClient:
        async def get(self, path):
            observations.append((path, clock.now))
            if failure == 'budget':
                clock.now += 2
            if path == '/lab/status':
                # Start terminal already arrived. Owner/active can be empty
                # while the detached revoke is still sending its bounded zero.
                facts = dict(active_ids=[], owner_present=False, admission_locked=False,
                             revokes_pending=int(clock.now < .8 and failure != 'no_stop'))
                if failure == 'unrelated_active':
                    facts['active_ids'] = [OLD_STOP]
                elif failure == 'owner':
                    facts['owner_present'] = True
                elif failure == 'admission':
                    facts['admission_locked'] = True
                elif failure == 'revoke':
                    facts['revokes_pending'] = 1
                data = dict(robot_id=ROBOT, fault='healthy' if failure == 'restored' else 'odom_stale',
                            completion=facts)
            else:
                assert path == '/api/commands?limit=500'
                start = dict(row(SESSION, 'teleop_start'), outcome='failed', detail='fault',
                             completed_at='done' if clock.now >= .1 else None)
                if failure == 'missing_start_completion':
                    start['completed_at'] = None
                elif failure == 'wrong_detail':
                    start['detail'] = 'other'
                elif failure == 'wrong_outcome':
                    start['outcome'] = 'canceled'
                data = [start]
                if clock.now >= .8 and failure != 'no_stop':
                    stop = dict(row(STOP, 'teleop_stop'), outcome='succeeded',
                                completed_at='done' if clock.now >= 1. else None)
                    if failure == 'stop_failed':
                        stop['outcome'] = 'failed'
                    elif failure == 'wrong_identity':
                        stop['session_generation'] += 1
                    elif failure == 'missing_stop_completion':
                        stop['completed_at'] = None
                    data.insert(0, stop)
            return SimpleNamespace(json=lambda: data, raise_for_status=lambda: None)

    async def check():
        await checker.complete_stale_odom_fault(CompletionClient(), ROBOT, SESSION, 'fault', timeout=1.5)
    if failure in (None, 'no_stop'):
        asyncio.run(check())
        assert clock.now >= (1. if failure is None else .6)
        assert observations[-1][0] == '/api/commands?limit=500'
    else:
        with pytest.raises(AssertionError):
            asyncio.run(check())
        assert clock.now <= 2.


@pytest.mark.parametrize('failure', [None, 'unobserved', 'zero', 'inactive', 'refusal', 'missing_reason', 'accepted', 'barrier'])
def test_acceptance_keeps_producer_alive_until_stale_fault_and_refuses_restart(monkeypatch, capsys, failure):
    """Exercise the checker workflow offline; receipt identity is tested above."""
    from types import ModuleType
    clock = SimpleNamespace(now=0.)
    monkeypatch.setattr(checker, 'time', SimpleNamespace(monotonic=lambda: clock.now))
    real_sleep = asyncio.sleep
    async def sleep(delay):
        clock.now += delay
        await real_sleep(0)
    monkeypatch.setattr(checker.asyncio, 'sleep', sleep)
    monkeypatch.setenv('PATROLBOT_ISOLATED_TEST', '1')
    monkeypatch.setattr(checker.Path, 'iterdir', lambda _: [Path('/sys/class/net/lo')])
    state = dict(fault='healthy', stale_velocity=0, refused=False, restored=False, probes=0)
    class Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            pass
        async def post(self, path, json):
            assert path == '/lab/fault'
            if state['fault'] == 'odom_stale' and json['fault'] == 'healthy':
                assert state['refused']
                state['restored'] = True
            state['fault'] = json['fault']
            state['injected'] = clock.now
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'accepted': True})
        async def get(self, path):
            if path == '/api/commands?limit=500':
                data = [dict(row(state['command_id'], 'teleop_start'),
                             completed_at=None if failure == 'barrier' else 'done',
                             outcome='failed', detail='Raw odometry is missing or stale.')]
            elif path.startswith('/lab/ready'):
                await real_sleep(0)
                data = dict(ready=state['fault'] != 'odom_stale', has_amcl_pose=False,
                            fault_observed=failure != 'unobserved' and state['fault'] == 'odom_stale')
                if state['fault'] == 'odom_stale':
                    data['fault_evidence'] = fault_evidence(state['command_id'], state['injected'])
                    clock.now = max(clock.now, state['injected'] + .7)
            else:
                assert path == '/lab/status'
                data = dict(physical_acceptance=False, received={
                    '/input/teleop': [dict(received_mono=10000., linear=v, angular=a)
                                      for v, a in [(0., 0.), (.18, 0.), (0., .35), (.1, 0.)]],
                    '/cmd_vel': [dict(received_mono=10000., linear=.1)],
                    '/cmd_vel_out': [dict(received_mono=10000., linear=-.12)],
                    '/patrolbot/web_teleop_active': [dict(received_mono=10000., active=False)]})
                data.update(robot_id=ROBOT, fault=state['fault'], completion=dict(
                    active_ids=[], owner_present=False, admission_locked=False, revokes_pending=0))
                if state['fault'] == 'odom_stale':
                    if failure == 'zero':
                        data['received']['/input/teleop'] = []
                    elif failure == 'inactive':
                        data['received']['/patrolbot/web_teleop_active'] = []
            return SimpleNamespace(json=lambda: data, raise_for_status=lambda: None)
    class FakeSocket(Client):
        async def send(self, message):
            frame = json.loads(message)
            if frame['type'] == 'command.request':
                accepted = state['fault'] not in {'sbc_down', 'odom_stale'}
                if state['fault'] == 'odom_stale':
                    assert clock.now >= state['injected'] + 1.2
                    state['probes'] += 1
                    assert state['probes'] == 1
                    state['refused'] = True
                    accepted = failure == 'accepted'
                else:
                    state['command_id'] = frame['data']['command_id']
                reason = 'Manual drive requires fresh base, raw odometry and LiDAR readings.'
                if failure == 'refusal':
                    reason = 'Wrong odometry reason'
                elif failure == 'missing_reason':
                    reason = None
                self.ack = dict(type='command.ack', robot_id=ROBOT,
                                data=dict(command_id=frame['data']['command_id'], accepted=accepted,
                                          reason=reason if not accepted else None))
            elif state['fault'] == 'odom_stale':
                state['stale_velocity'] += 1
        async def recv(self):
            if state['fault'] == 'odom_stale' and not state['refused']:
                frame = stale_result()
                frame['data']['command_id'] = state['command_id']
                return json.dumps(frame)
            return json.dumps(self.ack)
    async def finish(*args, **kwargs):
        assert state['fault'] != 'odom_stale'
    monkeypatch.setattr(checker, 'finish', finish)
    httpx, websockets = ModuleType('httpx'), ModuleType('websockets')
    httpx.AsyncClient = lambda **_: Client()
    websockets.connect = lambda _: FakeSocket()
    monkeypatch.setitem(sys.modules, 'httpx', httpx)
    monkeypatch.setitem(sys.modules, 'websockets', websockets)
    envelope = ModuleType('app.protocol.envelope')
    envelope.encode = lambda kind, robot, seq, data: json.dumps(
        dict(type=kind, robot_id=robot, seq=seq, data=data))
    monkeypatch.setitem(sys.modules, 'app.protocol.envelope', envelope)
    if failure is None:
        asyncio.run(checker.main())
        assert state['restored'] and state['refused']
        assert state['stale_velocity'] > 0
        assert state['probes'] == 1
        assert 'stale_odometry_failed_zero_inactive_and_refuses_fresh_start' in capsys.readouterr().out
    else:
        with pytest.raises(AssertionError):
            asyncio.run(checker.main())
        assert not state['restored']
        if failure == 'barrier':
            assert state['probes'] == 0
