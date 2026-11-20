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
