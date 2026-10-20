"""The tuning broker: admission, binding, unknown outcomes and dedupe.

The broker is the only thing between an authenticated HTTP request and a robot
``tuning.request`` frame, so these tests exercise the fail-closed edges: missing
capabilities, non-administrator mutations, session binding/supersession,
timeouts reported as unknown (never resent), and the truthful optimistic
revision cache.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.authentication.local import User
from app.tuning import validation
from app.tuning.authorization import AuthorizationError, verify
from app.tuning.broker import TuningBroker
from app.tuning.errors import (
    TuningConflict,
    TuningForbidden,
    TuningInvalid,
    TuningNotFound,
    TuningRateLimited,
    TuningUnavailable,
)

from tuning_fixtures import (
    READ_CAPABILITY,
    apply_request,
    change,
    new_uuid,
    read_request,
    redeploy_request,
    response,
    restore_request,
    save_request,
    snapshot,
)

WRITE_CAPABILITY = "robot_tuning_write_v1"

MUTATION_CAPABILITY = "robot_tuning_v1"

ADMIN = User(id=1, username="adm", display_name="Adm", role="administrator")
OPERATOR = User(id=2, username="opr", display_name="Opr", role="operator")
OBSERVER = User(id=3, username="obs", display_name="Obs", role="observer")


class FakeWebSocket:
    def __init__(self, fail: Exception | None = None) -> None:
        self.sent: list[str] = []
        self.fail = fail
        self.closed = False

    async def send_text(self, frame: str) -> None:
        if self.fail is not None:
            raise self.fail
        self.sent.append(frame)

    async def close(self, code: int = 0, reason: str = "") -> None:
        self.closed = True


class FakeState:
    def __init__(self, connection: str = "online", capabilities=()) -> None:
        self.connection = connection
        self.capabilities = set(capabilities)


class FakeSession:
    def __init__(self, robot_id: str, websocket: FakeWebSocket, *,
                 generation: int = 1, connection: str = "online",
                 capabilities=()) -> None:
        self.robot_id = robot_id
        self.websocket = websocket
        self.generation = generation
        self.state = FakeState(connection, capabilities)


class FakeHub:
    def __init__(self) -> None:
        self.robots: dict[str, FakeSession] = {}
        self._sequence = 0

    def _next_seq(self) -> int:
        self._sequence += 1
        return self._sequence


REGISTRY = validation.load_registry()
ROBOT = "patrolbot-01"
AUTH_KEY = b'unit-test-only-key-not-for-deployment'


def make_broker(*, mutation_timeout_s: float = 30.0, read_timeout_s: float = 5.0,
                max_pending: int = 8, authorization_key: bytes | None = AUTH_KEY,
                clock=None) -> tuple[TuningBroker, FakeHub]:
    hub = FakeHub()
    broker = TuningBroker(hub, registry=REGISTRY, mutation_timeout_s=mutation_timeout_s,
                          read_timeout_s=read_timeout_s, max_pending=max_pending,
                          authorization_key=authorization_key, clock=clock)
    return broker, hub


def add_robot(hub: FakeHub, *, capabilities=(READ_CAPABILITY, MUTATION_CAPABILITY),
              generation: int = 1, connection: str = "online",
              fail: Exception | None = None) -> tuple[FakeSession, FakeWebSocket]:
    ws = FakeWebSocket(fail=fail)
    session = FakeSession(ROBOT, ws, generation=generation, connection=connection,
                          capabilities=capabilities)
    hub.robots[ROBOT] = session
    return session, ws


def envelope(robot_id: str = ROBOT) -> SimpleNamespace:
    return SimpleNamespace(robot_id=robot_id, type="tuning.response")


def sent_frame(ws: FakeWebSocket, index: int = 0) -> dict:
    return json.loads(ws.sent[index])


async def until(predicate, message: str) -> None:
    """Yield until ``predicate`` holds; scheduling order differs by Python version."""
    for _ in range(10_000):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError(message)


# -- capability gate ---------------------------------------------------------

async def test_read_requires_read_capability():
    broker, hub = make_broker()
    add_robot(hub, capabilities=(MUTATION_CAPABILITY,))  # mutation only
    with pytest.raises(TuningUnavailable):
        await broker.request(OBSERVER, ROBOT, read_request())


async def test_mutation_requires_mutation_capability():
    broker, hub = make_broker()
    add_robot(hub, capabilities=(READ_CAPABILITY,))
    with pytest.raises(TuningUnavailable):
        await broker.request(ADMIN, ROBOT, save_request([change("amcl.alpha1", 0.1)]))


async def test_save_is_admitted_with_the_write_capability_alone():
    broker, hub = make_broker()
    session, ws = add_robot(hub, capabilities=(READ_CAPABILITY, WRITE_CAPABILITY))
    task = asyncio.create_task(
        broker.request(ADMIN, ROBOT, save_request([change("amcl.alpha1", 0.1)])))
    await until(lambda: len(ws.sent) == 1, "save frame was not sent")
    assert len(ws.sent) == 1
    # Resolve explicitly instead of racing task.cancel() against the inner
    # send task: Python 3.11 may otherwise let the send complete after cancel.
    broker.disconnected(session)
    assert (await task).outcome == "unknown"


@pytest.mark.parametrize("mutation", [
    apply_request(), redeploy_request(), restore_request()])
async def test_activation_needs_the_full_capability_not_just_write(mutation):
    broker, hub = make_broker()
    _session, ws = add_robot(hub, capabilities=(READ_CAPABILITY, WRITE_CAPABILITY))
    with pytest.raises(TuningUnavailable):
        await broker.request(ADMIN, ROBOT, mutation)
    assert ws.sent == []


async def test_offline_or_missing_robot_is_unavailable():
    broker, _hub = make_broker()
    with pytest.raises(TuningUnavailable):
        await broker.request(ADMIN, ROBOT, read_request())
    _broker, hub = make_broker()
    add_robot(hub, connection="offline")
    with pytest.raises(TuningUnavailable):
        await _broker.request(ADMIN, ROBOT, read_request())


# -- authorization -----------------------------------------------------------

async def test_observer_can_read_but_not_mutate():
    broker, hub = make_broker(read_timeout_s=0.05)
    add_robot(hub)
    with pytest.raises(TuningForbidden):
        await broker.request(OBSERVER, ROBOT, save_request([change("amcl.alpha1", 0.1)]))
    # Reads are admitted for any authenticated role: it reaches the wire and
    # then times out, which is a wire-level outcome, not a refusal.
    with pytest.raises(TuningUnavailable):
        await broker.request(OBSERVER, ROBOT, read_request())
    assert hub.robots[ROBOT].websocket.sent  # a read frame was actually sent


async def test_operator_cannot_mutate():
    broker, hub = make_broker()
    add_robot(hub)
    with pytest.raises(TuningForbidden):
        await broker.request(OPERATOR, ROBOT, save_request([change("amcl.alpha1", 0.1)]))


# -- actor derivation --------------------------------------------------------

async def test_actor_is_derived_server_side_and_role_cannot_be_smuggled():
    broker, hub = make_broker()
    _session, ws = add_robot(hub)
    rid = new_uuid()
    payload = save_request([change("amcl.alpha1", 0.1)], request_id=rid)
    # A body trying to assert an actor is rejected by strict validation, never
    # forwarded. The role travels only from the verified session.
    with pytest.raises(TuningInvalid):
        await broker.request(ADMIN, ROBOT, dict(payload, actor={"role": "administrator"}))
    assert ws.sent == []

    task = asyncio.create_task(broker.request(ADMIN, ROBOT, payload))
    await until(lambda: len(ws.sent) == 1, "mutation frame was not sent")
    forwarded = sent_frame(ws)
    assert forwarded["type"] == "tuning.request"
    assert forwarded["data"]["actor"] == {"id": 1, "username": "adm",
                                          "role": "administrator"}
    assert forwarded["data"]["request"]["request_id"] == rid
    await broker.handle_response(_session, envelope(), response(rid, "ok"))
    assert (await task).outcome == "ok"


# -- request/response correlation -------------------------------------------

async def test_reply_resolves_the_awaiting_request():
    broker, hub = make_broker()
    session, ws = add_robot(hub)
    rid = new_uuid()
    task = asyncio.create_task(
        broker.request(ADMIN, ROBOT, save_request([change("amcl.alpha1", 0.1)],
                                                  request_id=rid)))
    await until(lambda: len(ws.sent) == 1, "request was not sent")
    await broker.handle_response(session, envelope(), response(rid, "refused", detail="no"))
    result = await task
    assert result.outcome == "refused" and result.detail == "no"


async def test_superseded_reply_is_dropped():
    broker, hub = make_broker(mutation_timeout_s=0.05)
    session, ws = add_robot(hub, generation=1)
    rid = new_uuid()
    task = asyncio.create_task(
        broker.request(ADMIN, ROBOT, save_request([change("amcl.alpha1", 0.1)],
                                                  request_id=rid)))
    await until(lambda: len(ws.sent) == 1, "request was not sent")
    # The session is replaced (generation bump) before the reply arrives.
    hub.robots[ROBOT] = FakeSession(ROBOT, FakeWebSocket(), generation=2,
                                    capabilities=(READ_CAPABILITY, MUTATION_CAPABILITY))
    await broker.handle_response(session, envelope(), response(rid, "ok"))
    result = await task
    assert result.outcome == "unknown"  # dropped reply -> timeout, not a stale ok


async def test_reply_from_wrong_robot_is_dropped():
    broker, hub = make_broker(mutation_timeout_s=0.05)
    session, ws = add_robot(hub)
    rid = new_uuid()
    task = asyncio.create_task(
        broker.request(ADMIN, ROBOT, save_request([change("amcl.alpha1", 0.1)],
                                                  request_id=rid)))
    await until(lambda: len(ws.sent) == 1, "request was not sent")
    await broker.handle_response(session, envelope("other-robot"), response(rid, "ok"))
    assert (await task).outcome == "unknown"


async def test_unreadable_reply_marks_unknown_not_failure():
    broker, hub = make_broker(mutation_timeout_s=0.05)
    session, ws = add_robot(hub)
    rid = new_uuid()
    task = asyncio.create_task(
        broker.request(ADMIN, ROBOT, save_request([change("amcl.alpha1", 0.1)],
                                                  request_id=rid)))
    await until(lambda: len(ws.sent) == 1, "request was not sent")
    await broker.handle_response(session, envelope(),
                                 {"request_id": rid, "outcome": "ok", "detail": "x" * 600})
    result = await task
    assert result.outcome == "unknown"


# -- unknown outcomes and no blind resend ------------------------------------

async def test_mutation_timeout_is_unknown_and_never_resent():
    broker, hub = make_broker(mutation_timeout_s=0.02)
    _session, ws = add_robot(hub)
    rid = new_uuid()
    payload = save_request([change("amcl.alpha1", 0.1)], request_id=rid)
    result = await broker.request(ADMIN, ROBOT, payload)
    assert result.outcome == "unknown"
    assert len(ws.sent) == 1  # sent once, never retried
    # The idempotency key is remembered: a replay is refused before the wire.
    with pytest.raises(TuningConflict):
        await broker.request(ADMIN, ROBOT, payload)
    assert len(ws.sent) == 1


async def test_duplicate_in_flight_request_id_conflicts():
    broker, hub = make_broker()
    session, ws = add_robot(hub)
    rid = new_uuid()
    first = asyncio.create_task(
        broker.request(ADMIN, ROBOT, save_request([change("amcl.alpha1", 0.1)],
                                                  request_id=rid)))
    await until(lambda: len(ws.sent) == 1, "first frame was not sent")
    with pytest.raises(TuningConflict):
        await broker.request(ADMIN, ROBOT, save_request([change("amcl.alpha1", 0.1)],
                                                        request_id=rid))
    assert len(ws.sent) == 1
    broker.disconnected(session)
    assert (await first).outcome == "unknown"


async def test_send_failure_of_mutation_is_unknown():
    broker, hub = make_broker()
    add_robot(hub, fail=ConnectionError("socket died"))
    result = await broker.request(
        ADMIN, ROBOT, save_request([change("amcl.alpha1", 0.1)]))
    assert result.outcome == "unknown"


async def test_send_failure_of_read_is_unavailable():
    broker, hub = make_broker()
    add_robot(hub, fail=ConnectionError("socket died"))
    with pytest.raises(TuningUnavailable):
        await broker.request(ADMIN, ROBOT, read_request())


# -- disconnect / shutdown ---------------------------------------------------

async def test_disconnect_marks_mutation_unknown_and_read_unavailable():
    broker, hub = make_broker()
    session, ws = add_robot(hub)
    mutation = asyncio.create_task(
        broker.request(ADMIN, ROBOT, save_request([change("amcl.alpha1", 0.1)])))
    await until(lambda: len(ws.sent) == 1, "mutation frame was not sent")
    broker.disconnected(session)
    assert (await mutation).outcome == "unknown"

    session2, ws2 = add_robot(hub, generation=2)
    read = asyncio.create_task(broker.request(OBSERVER, ROBOT, read_request()))
    await until(lambda: len(ws2.sent) == 1, "read frame was not sent")
    broker.disconnected(session2)
    with pytest.raises(TuningUnavailable):
        await read


async def test_shutdown_resolves_pending_as_unknown():
    broker, hub = make_broker()
    _session, ws = add_robot(hub)
    mutation = asyncio.create_task(
        broker.request(ADMIN, ROBOT, save_request([change("amcl.alpha1", 0.1)])))
    await until(lambda: len(ws.sent) == 1, "mutation frame was not sent")
    await broker.shutdown()
    assert (await mutation).outcome == "unknown"


# -- bounded concurrency -----------------------------------------------------

async def test_pending_concurrency_is_bounded():
    broker, hub = make_broker(max_pending=1)
    session, ws = add_robot(hub)
    first = asyncio.create_task(
        broker.request(ADMIN, ROBOT, save_request([change("amcl.alpha1", 0.1)])))
    await until(lambda: len(ws.sent) == 1, "first frame was not sent")
    with pytest.raises(TuningRateLimited):
        await broker.request(ADMIN, ROBOT, save_request([change("amcl.alpha2", 0.1)]))
    broker.disconnected(session)
    assert (await first).outcome == "unknown"


# -- snapshot / job helpers --------------------------------------------------

async def test_snapshot_returns_runtime_state():
    broker, hub = make_broker()
    session, ws = add_robot(hub)
    rid = new_uuid()
    task = asyncio.create_task(broker.snapshot(OBSERVER, ROBOT))
    await until(lambda: len(ws.sent) == 1, "snapshot frame was not sent")
    # Find the request_id the broker minted for the read.
    sent = sent_frame(session.websocket)["data"]["request"]
    await broker.handle_response(session, envelope(),
                                 response(sent["request_id"], "ok", snapshot_data=snapshot()))
    result = await task
    assert result["session_id"]


async def test_snapshot_unknown_is_unavailable_never_defaulted():
    broker, hub = make_broker()
    session, ws = add_robot(hub)
    task = asyncio.create_task(broker.snapshot(OBSERVER, ROBOT))
    await until(lambda: len(ws.sent) == 1, "snapshot frame was not sent")
    sent = sent_frame(session.websocket)["data"]["request"]
    await broker.handle_response(session, envelope(),
                                 response(sent["request_id"], "unknown", detail="no state"))
    with pytest.raises(TuningUnavailable):
        await task


async def test_job_unknown_and_missing():
    broker, hub = make_broker()
    session, ws = add_robot(hub)
    task = asyncio.create_task(broker.job(OBSERVER, ROBOT, new_uuid()))
    await until(lambda: len(ws.sent) == 1, "job frame was not sent")
    sent = sent_frame(session.websocket)["data"]["request"]
    await broker.handle_response(session, envelope(),
                                 response(sent["request_id"], "ok", job=None))
    with pytest.raises(TuningNotFound):
        await task


# -- truthful optimistic revision cache --------------------------------------

async def test_stale_revision_is_rejected_from_cache():
    broker, hub = make_broker()
    session, ws = add_robot(hub)
    snap = snapshot(desired_revision=3)
    read = asyncio.create_task(broker.snapshot(OBSERVER, ROBOT))
    await until(lambda: len(ws.sent) == 1, "snapshot frame was not sent")
    sent = sent_frame(session.websocket)["data"]["request"]
    await broker.handle_response(session, envelope(),
                                 response(sent["request_id"], "ok", snapshot_data=snap))
    await read

    payload = save_request([change("amcl.alpha1", 0.1)], base_revision=2,
                           session_id=snap["session_id"])
    with pytest.raises(TuningConflict):
        await broker.request(ADMIN, ROBOT, payload)


async def test_changed_tuning_session_is_rejected_from_cache():
    broker, hub = make_broker()
    session, ws = add_robot(hub)
    snap = snapshot(desired_revision=3)
    read = asyncio.create_task(broker.snapshot(OBSERVER, ROBOT))
    await until(lambda: len(ws.sent) == 1, "snapshot frame was not sent")
    sent = sent_frame(session.websocket)["data"]["request"]
    await broker.handle_response(session, envelope(),
                                 response(sent["request_id"], "ok", snapshot_data=snap))
    await read

    payload = save_request([change("amcl.alpha1", 0.1)], base_revision=3,
                           session_id=new_uuid())
    with pytest.raises(TuningConflict):
        await broker.request(ADMIN, ROBOT, payload)


# -- signed authorization -----------------------------------------------------

async def test_forwarded_mutation_is_signed_for_the_exact_robot():
    broker, hub = make_broker()
    session, ws = add_robot(hub)
    payload = save_request([change("amcl.alpha1", 0.1)])
    task = asyncio.create_task(broker.request(ADMIN, ROBOT, payload))
    await until(lambda: len(ws.sent) == 1, "mutation frame was not sent")
    wrapper = sent_frame(ws)["data"]
    assert wrapper["actor"] == {"id": 1, "username": "adm", "role": "administrator"}
    assert wrapper["request"] == payload
    assert "authorization" in wrapper
    # The core can verify the exact bytes for this robot and no other.
    assert verify(wrapper, AUTH_KEY, ROBOT) == {"request": payload,
                                                "actor": wrapper["actor"]}
    with pytest.raises(AuthorizationError):
        verify(wrapper, AUTH_KEY, "other-robot")
    # A tampered server-derived actor no longer verifies.
    tampered = json.loads(json.dumps(wrapper))
    tampered["actor"]["role"] = "observer"
    with pytest.raises(AuthorizationError):
        verify(tampered, AUTH_KEY, ROBOT)
    await broker.handle_response(session, envelope(), response(payload["request_id"], "ok"))
    assert (await task).outcome == "ok"


async def test_mutation_without_a_configured_key_fails_closed():
    hub = FakeHub()
    broker = TuningBroker(hub, registry=REGISTRY, authorization_key=None)
    _session, ws = add_robot(hub)
    with pytest.raises(TuningUnavailable):
        await broker.request(ADMIN, ROBOT, save_request([change("amcl.alpha1", 0.1)]))
    assert ws.sent == []  # nothing unsigned ever reaches the wire


# -- dedupe memory is time bounded --------------------------------------------

async def test_replayed_mutation_is_refused_within_the_dedupe_ttl():
    now = {"t": 1000.0}
    broker, hub = make_broker(clock=lambda: now["t"])
    session, ws = add_robot(hub)
    rid = new_uuid()
    payload = save_request([change("amcl.alpha1", 0.1)], request_id=rid)
    first = asyncio.create_task(broker.request(ADMIN, ROBOT, payload))
    await until(lambda: len(ws.sent) == 1, "request was not sent")
    await broker.handle_response(session, envelope(), response(rid, "ok"))
    await first
    with pytest.raises(TuningConflict):
        await broker.request(ADMIN, ROBOT, payload)
    assert len(ws.sent) == 1


async def test_dedupe_memory_prunes_only_after_the_ttl():
    now = {"t": 1000.0}
    broker, hub = make_broker(clock=lambda: now["t"])
    session, ws = add_robot(hub)
    rid = new_uuid()
    payload = save_request([change("amcl.alpha1", 0.1)], request_id=rid)
    first = asyncio.create_task(broker.request(ADMIN, ROBOT, payload))
    await until(lambda: len(ws.sent) == 1, "request was not sent")
    await broker.handle_response(session, envelope(), response(rid, "ok"))
    await first

    now["t"] += broker.dedupe_ttl_s + 1
    second = asyncio.create_task(broker.request(ADMIN, ROBOT, payload))
    await until(lambda: len(ws.sent) == 2, "replayed frame was not admitted")
    assert len(ws.sent) == 2  # admitted again; the core is the durable dedup
    await broker.handle_response(session, envelope(), response(rid, "ok"))
    await second


async def test_reply_on_a_replaced_socket_is_dropped():
    broker, hub = make_broker(mutation_timeout_s=0.05)
    session, ws = add_robot(hub)
    rid = new_uuid()
    task = asyncio.create_task(
        broker.request(ADMIN, ROBOT, save_request([change("amcl.alpha1", 0.1)],
                                                  request_id=rid)))
    await until(lambda: len(ws.sent) == 1, "request was not sent")
    # Same session object and generation, but the socket was replaced underneath.
    session.websocket = FakeWebSocket()
    await broker.handle_response(session, envelope(), response(rid, "ok"))
    assert (await task).outcome == "unknown"
