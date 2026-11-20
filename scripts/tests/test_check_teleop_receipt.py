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
