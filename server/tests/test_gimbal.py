"""Gimbal control authority and the server-side deadman.

Camera rate frames are a stream, not discrete commands. They must not go down
the goal-command path: that path duplicate-checks by command_id, audits every
request, and rate-limits per minute, all of which are correct for "go to this
pose" and wrong for thirty frames a second.

So this is a separate channel with its own rules, and these tests pin them:
the same lease still gates it, control transitions are audited rather than
individual frames, and a browser that disappears mid-slew stops the camera.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from app.commands.gimbal import (  # noqa: E402
    GIMBAL_EXPIRY_S,
    GimbalAuthority,
    GimbalFrame,
)
from app.commands.lease import OperatorLease  # noqa: E402


class FakeUser:
    def __init__(self, user_id: int, username: str, can_command: bool = True):
        self.id = user_id
        self.username = username
        self.can_command = can_command


class FakeClock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def authority():
    clock = FakeClock()
    return GimbalAuthority(lease=OperatorLease(), clock=clock), clock


ROBOT = "patrolbot"
ALICE = FakeUser(1, "alice")
BOB = FakeUser(2, "bob")
OBSERVER = FakeUser(3, "observer", can_command=False)


def _frame(pan=0.0, tilt=0.0, roll=0.0, valid=True):
    return GimbalFrame(pan=pan, tilt=tilt, roll=roll, valid=valid)


# ------------------------------------------------------------- authority --

def test_observer_cannot_move_the_camera(authority):
    auth, _ = authority
    decision = auth.submit(ROBOT, OBSERVER, object(), _frame(pan=10.0))
    assert not decision.accepted
    assert "read-only" in decision.reason.lower()


def test_unauthenticated_client_cannot_move_the_camera(authority):
    auth, _ = authority
    decision = auth.submit(ROBOT, None, object(), _frame(pan=10.0))
    assert not decision.accepted


def test_operator_with_the_lease_is_accepted(authority):
    auth, _ = authority
    assert auth.submit(ROBOT, ALICE, object(), _frame(pan=10.0)).accepted


def test_second_operator_is_refused_while_the_first_holds(authority):
    auth, _ = authority
    auth.submit(ROBOT, ALICE, object(), _frame(pan=10.0))
    decision = auth.submit(ROBOT, BOB, object(), _frame(pan=10.0))
    assert not decision.accepted
    assert "alice" in decision.reason.lower()


def test_explicit_takeover_transfers_control(authority):
    auth, _ = authority
    auth.submit(ROBOT, ALICE, object(), _frame(pan=10.0))
    decision = auth.submit(ROBOT, BOB, object(), _frame(pan=10.0), takeover=True)
    assert decision.accepted


def test_the_same_operator_in_a_second_tab_is_not_locked_out(authority):
    """The lease is keyed by user, so one person is never their own blocker."""
    auth, _ = authority
    auth.submit(ROBOT, ALICE, object(), _frame(pan=10.0))
    assert auth.submit(ROBOT, ALICE, object(), _frame(pan=5.0)).accepted


def test_gimbal_control_shares_the_lease_with_driving(authority):
    """Camera authority is the same authority as driving, by design."""
    auth, _ = authority
    lease = auth.lease
    lease.acquire(ROBOT, BOB.id, BOB.username, object())
    assert not auth.submit(ROBOT, ALICE, object(), _frame(pan=10.0)).accepted


# ------------------------------------------------------- server deadman --

def test_a_fresh_stream_is_not_expired(authority):
    auth, _ = authority
    auth.submit(ROBOT, ALICE, object(), _frame(pan=10.0))
    assert not auth.expired(ROBOT)


def test_the_stream_expires_when_frames_stop(authority):
    """A browser that disconnects mid-slew must not leave the camera moving."""
    auth, clock = authority
    auth.submit(ROBOT, ALICE, object(), _frame(pan=10.0))
    clock.advance(GIMBAL_EXPIRY_S + 0.01)
    assert auth.expired(ROBOT)


def test_expiry_emits_exactly_one_hold(authority):
    auth, clock = authority
    auth.submit(ROBOT, ALICE, object(), _frame(pan=10.0))
    clock.advance(GIMBAL_EXPIRY_S + 0.01)

    holds = auth.collect_holds()
    assert [robot for robot, _ in holds] == [ROBOT]
    assert not holds[0][1].valid
    # Already held; do not keep emitting.
    assert auth.collect_holds() == []


def test_refreshing_the_stream_prevents_expiry(authority):
    auth, clock = authority
    auth.submit(ROBOT, ALICE, object(), _frame(pan=10.0))
    clock.advance(GIMBAL_EXPIRY_S * 0.5)
    auth.submit(ROBOT, ALICE, object(), _frame(pan=10.0))
    clock.advance(GIMBAL_EXPIRY_S * 0.5)
    assert not auth.expired(ROBOT)


def test_a_client_disconnecting_releases_the_stream(authority):
    auth, _ = authority
    client = object()
    auth.submit(ROBOT, ALICE, client, _frame(pan=10.0))
    holds = auth.release_client(client)
    assert [robot for robot, _ in holds] == [ROBOT]
    assert not holds[0][1].valid


def test_releasing_an_unrelated_client_emits_nothing(authority):
    auth, _ = authority
    auth.submit(ROBOT, ALICE, object(), _frame(pan=10.0))
    assert auth.release_client(object()) == []


def test_an_explicit_invalid_frame_stops_without_waiting_for_expiry(authority):
    auth, _ = authority
    client = object()
    auth.submit(ROBOT, ALICE, client, _frame(pan=10.0))
    decision = auth.submit(ROBOT, ALICE, client, _frame(valid=False))
    assert decision.accepted
    assert auth.expired(ROBOT)


# ---------------------------------------------------------- flood guard --

def test_a_flood_of_frames_is_capped(authority):
    """A stuck client must not be able to saturate the robot link."""
    auth, _ = authority
    client = object()
    accepted = sum(
        1 for _ in range(500)
        if auth.submit(ROBOT, ALICE, client, _frame(pan=1.0)).accepted)
    assert accepted < 500


def test_the_cap_allows_a_normal_thirty_hertz_stream(authority):
    """30 Hz for a second is ordinary use and must never be throttled."""
    auth, clock = authority
    client = object()
    for _ in range(30):
        decision = auth.submit(ROBOT, ALICE, client, _frame(pan=1.0))
        assert decision.accepted
        clock.advance(1.0 / 30.0)


# ------------------------------------------------------------- auditing --

def test_taking_control_is_audited_once_not_per_frame(authority):
    """Auditing every frame would write thousands of rows a minute."""
    auth, clock = authority
    client = object()
    for _ in range(10):
        auth.submit(ROBOT, ALICE, client, _frame(pan=1.0))
        clock.advance(0.03)
    events = auth.drain_audit()
    assert len(events) == 1
    assert events[0].action == "gimbal_control_acquired"
    assert events[0].username == "alice"


def test_a_takeover_is_audited(authority):
    auth, _ = authority
    auth.submit(ROBOT, ALICE, object(), _frame(pan=1.0))
    auth.drain_audit()
    auth.submit(ROBOT, BOB, object(), _frame(pan=1.0), takeover=True)
    events = auth.drain_audit()
    assert any(e.action == "gimbal_control_taken_over" for e in events)


def test_a_refusal_is_audited(authority):
    auth, _ = authority
    auth.submit(ROBOT, ALICE, object(), _frame(pan=1.0))
    auth.drain_audit()
    auth.submit(ROBOT, BOB, object(), _frame(pan=1.0))
    events = auth.drain_audit()
    assert any(e.action == "gimbal_control_refused" for e in events)


def test_draining_the_audit_clears_it(authority):
    auth, _ = authority
    auth.submit(ROBOT, ALICE, object(), _frame(pan=1.0))
    auth.drain_audit()
    assert auth.drain_audit() == []


# -------------------------------------------------------------- sanity --

def test_non_finite_values_are_refused(authority):
    """NaN would propagate all the way to the serial encoder."""
    auth, _ = authority
    assert not auth.submit(
        ROBOT, ALICE, object(), _frame(pan=float("nan"))).accepted
    assert not auth.submit(
        ROBOT, ALICE, object(), _frame(pan=float("inf"))).accepted


def test_frames_for_separate_robots_are_tracked_separately(authority):
    auth, clock = authority
    auth.submit("robot-a", ALICE, object(), _frame(pan=1.0))
    clock.advance(GIMBAL_EXPIRY_S + 0.01)
    auth.submit("robot-b", ALICE, object(), _frame(pan=1.0))
    assert auth.expired("robot-a")
    assert not auth.expired("robot-b")
