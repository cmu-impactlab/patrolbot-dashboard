"""Tuning request broker: browser HTTP -> robot WebSocket -> core adapter.

Posture
-------
* The actor is derived from the verified session, never the request body.
* Reads require any authenticated role; every mutation re-checks a *current*
  administrator role at admission (the API layer also depends on
  ``require_administrator`` semantics; the broker re-checks defensively).
* Every request is bound to the exact robot session/generation/websocket it was
  admitted against. A reply from a superseded connection is dropped, and a
  timeout of a mutation is reported as ``unknown`` — it is never resent or
  retried blindly.
* Nothing is persisted in the dashboard database. Durability belongs to the
  robot; a dashboard cache may only be used for a truthful optimistic
  stale-revision check, never to claim a save succeeded.
* The capability gate fails closed: reads need ``robot_tuning_read_v1`` and
  mutations need ``robot_tuning_v1``. Until the core proves both, no tuning
  request reaches the robot.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from ..protocol.envelope import Envelope, encode
from . import validation
from .authorization import AuthorizationError, load_key, sign
from .authz import actor_from_user, require_administrator
from .errors import (
    TuningConflict,
    TuningInvalid,
    TuningNotFound,
    TuningRateLimited,
    TuningUnavailable,
)
from .validation import (
    CAPABILITY,
    CONTRACT,
    CONTRACT_VERSION,
    MUTATION_CAPABILITY,
    MUTATION_OPERATIONS,
    READ_CAPABILITY,
)

if TYPE_CHECKING:
    from ..telemetry.hub import RobotSession

log = logging.getLogger("tuning")

# The robot bridge advertises this narrower token when it can persist a `save`
# but has not proven safe activation. Full `robot_tuning_v1` is required before
# apply/redeploy/restore may run.
WRITE_CAPABILITY = "robot_tuning_write_v1"


@dataclass
class TuningResponse:
    request_id: str
    outcome: str
    detail: str = ""
    snapshot: dict[str, Any] | None = None
    job: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"request_id": self.request_id, "outcome": self.outcome,
                "detail": self.detail, "snapshot": self.snapshot, "job": self.job}


@dataclass
class _Pending:
    request_id: str
    robot_id: str
    session: "RobotSession"
    generation: int
    websocket: Any
    future: "asyncio.Future[Any]"
    operation: str
    mutation: bool


def _as_mapping(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        return payload
    dump = getattr(payload, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json")
        except TypeError:
            return dump()
    return None


class TuningBroker:
    def __init__(self, hub: Any, *, registry: dict[str, Any] | None = None,
                 read_timeout_s: float = 5.0, mutation_timeout_s: float = 30.0,
                 max_pending: int = 8, dedupe_limit: int = 512,
                 dedupe_ttl_s: float = 60.0,
                 authorization_key: bytes | None = None,
                 clock: Callable[[], float] | None = None) -> None:
        self.hub = hub
        self.read_timeout_s = read_timeout_s
        self.mutation_timeout_s = mutation_timeout_s
        self.max_pending = max_pending
        self.dedupe_limit = dedupe_limit
        # A replayed idempotency key is remembered for at least the signed
        # authorization window (30s) plus margin, so a resent mutation can never
        # slip past the in-memory guard within its lifetime. Older ids are
        # pruned; the core keeps the durable dedup ledger.
        self.dedupe_ttl_s = dedupe_ttl_s
        self._clock = clock if clock is not None else time.monotonic
        self._authorization_key = authorization_key
        # Reviewed catalog: injected (tests/ops) or loaded once from the
        # installed registry. Validation needs the full catalog (``parameters``),
        # never a partially built id index.
        self._registry = registry
        self._registry_data: dict[str, Any] | None = None
        self._pending: dict[str, _Pending] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        # Bounded, time-bounded memory of mutation request ids: a replayed
        # idempotency key must not re-run (or blindly resend) a durable
        # mutation while its authorization could still be current.
        self._seen: OrderedDict[str, float] = OrderedDict()
        # Truthful optimistic check only: (generation, desired_revision,
        # session_id) learned from a real robot snapshot. Never a success claim.
        self._desired: dict[str, tuple[int, int, str]] = {}
        self._policies: dict[str, tuple[Any, Any, dict, float]] = {}

    # -- registry ---------------------------------------------------------------

    def _catalog(self) -> dict[str, Any]:
        # Fail closed if the reviewed catalog is missing/corrupt.
        data = self._registry if self._registry is not None else self._registry_data
        if data is None:
            data = validation.load_registry()
            self._registry_data = data
        return data

    def _lock(self, robot_id: str) -> asyncio.Lock:
        return self._locks.setdefault(robot_id, asyncio.Lock())

    # -- public API --------------------------------------------------------------

    async def request(self, user: Any, robot_id: str, request: Any) -> TuningResponse:
        """Admit, forward and await one canonical tuning request.

        Raises a :class:`TuningError` subclass for anything refused before a
        frame was sent. A mutation whose outcome cannot be known returns a
        response with ``outcome="unknown"`` instead of pretending failure.
        """
        catalog = self._catalog()  # fail closed if the reviewed catalog is missing
        payload = validation.validate_request(request, catalog)
        operation = payload["operation"]
        require_administrator(user, operation)
        mutation = operation in MUTATION_OPERATIONS
        # Admission checks that must fail fast and never queue behind the
        # per-robot mutation lock: a replayed idempotency key, an id already in
        # flight, or a saturated pending bound. All of these run on the event
        # loop, so reading them before the lock is safe (and the in-lock checks
        # below stay as defence in depth).
        request_id = payload["request_id"]
        if request_id in self._pending:
            raise TuningConflict("That tuning request is already in flight.")
        if mutation and self._seen_recently(request_id):
            raise TuningConflict("That tuning request was already handled.")
        if len(self._pending) >= self.max_pending:
            raise TuningRateLimited("Tuning is busy; too many requests are in flight.")
        if mutation:
            # Serialize mutations per robot so two admins cannot interleave a
            # save/apply. Refuse rather than queue: a queued change would be
            # unbounded work the caller never learned about, and a second admin
            # should be told the robot is already busy. Reads never take it.
            lock = self._lock(robot_id)
            if lock.locked():
                raise TuningRateLimited(
                    "A tuning change is already in flight for this robot; "
                    "wait for it to finish before sending another.")
            async with lock:
                return await self._request_locked(user, robot_id, payload, operation, mutation)
        return await self._request_locked(user, robot_id, payload, operation, mutation)

    async def snapshot(self, user: Any, robot_id: str) -> dict[str, Any]:
        """Perform the read RPC and return the robot's runtime snapshot.

        A missing/unavailable runtime is a 503, never a packaged default.
        """
        response = await self.request(user, robot_id, self._read_request())
        if response.outcome != "ok" or response.snapshot is None:
            raise TuningUnavailable(
                response.detail or "The robot has not reported tuning state yet.")
        return response.snapshot

    async def job(self, user: Any, robot_id: str, job_id: str) -> dict[str, Any]:
        """Query one durable job by id on the robot."""
        request = self._read_request(operation="status")
        request["job_id"] = job_id
        response = await self.request(user, robot_id, request)
        if response.outcome != "ok":
            raise TuningUnavailable(
                response.detail or "The robot could not report that tuning job.")
        if response.job is None:
            raise TuningNotFound("That tuning job is not known to the robot.")
        return response.job

    def _read_request(self, *, operation: str = "read") -> dict[str, Any]:
        # A read/bootstrap has no authoritative session token yet; the core may
        # ignore the placeholder for a session fetch. It is minted fresh per
        # call and never reused as a compare-and-swap token.
        return {
            "contract": CONTRACT,
            "version": CONTRACT_VERSION,
            "operation": operation,
            "request_id": str(uuid.uuid4()),
            "generation": {"capability": CAPABILITY, "session_id": str(uuid.uuid4())},
        }

    # -- internals ---------------------------------------------------------------

    async def _request_locked(self, user: Any, robot_id: str, payload: dict[str, Any],
                              operation: str, mutation: bool) -> TuningResponse:
        request_id = payload["request_id"]
        if request_id in self._pending:
            raise TuningConflict("That tuning request is already in flight.")
        if mutation and self._seen_recently(request_id):
            raise TuningConflict("That tuning request was already handled.")

        session = self.hub.robots.get(robot_id)
        capability = MUTATION_CAPABILITY if mutation else READ_CAPABILITY
        if session is None or session.websocket is None or session.state.connection != "online":
            raise TuningUnavailable("The robot is not connected right now.")
        save_capability = operation == "save" and WRITE_CAPABILITY in session.state.capabilities
        if capability not in session.state.capabilities and not save_capability:
            if mutation:
                raise TuningUnavailable(
                    "This robot does not advertise robot_tuning_v1; tuning changes "
                    "are disabled until the robot proves safe activation support.")
            raise TuningUnavailable(
                "This robot does not advertise robot_tuning_read_v1; tuning state "
                "cannot be read yet.")

        if mutation:
            self._optimistic_check(robot_id, session, payload, operation)

        if len(self._pending) >= self.max_pending:
            raise TuningRateLimited("Tuning is busy; too many requests are in flight.")

        generation = session.generation
        websocket = session.websocket
        actor = actor_from_user(user)
        validation.validate_actor(actor)
        wrapper = {"request": payload, "actor": actor}
        if mutation:
            try:
                key = self._authorization_key
                if key is None:
                    settings = getattr(self.hub, "settings", None)
                    key = load_key(getattr(settings, "tuning_auth_key_file", ""))
                wrapper = sign(wrapper, key, robot_id)
            except (AuthorizationError, OSError) as exc:
                raise TuningUnavailable("Signed tuning authorization is not configured.") from exc
        frame = encode("tuning.request", robot_id, self.hub._next_seq(), wrapper)

        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        entry = _Pending(request_id=request_id, robot_id=robot_id, session=session,
                         generation=generation, websocket=websocket, future=future,
                         operation=operation, mutation=mutation)
        self._pending[request_id] = entry
        if mutation:
            # Remember once a frame is about to be sent, so a completed-but-
            # unresolved mutation cannot be blindly resent with the same key.
            self._remember(request_id)
        timeout = self.mutation_timeout_s if mutation else self.read_timeout_s
        try:
            try:
                if not self._bound(session, generation, websocket):
                    raise TuningUnavailable(
                        "The robot connection changed before the request was sent.")
                await asyncio.wait_for(websocket.send_text(frame), timeout=min(5.0, timeout))
            except TuningUnavailable:
                raise
            except Exception as exc:  # socket died between the check and the send
                log.warning("failed to send tuning %s to %s: %s", request_id, robot_id, exc)
                if mutation:
                    return TuningResponse(request_id, "unknown",
                        "The request could not be sent; the outcome is unknown. "
                        "Do not resend it blindly.")
                raise TuningUnavailable(
                    "The robot connection dropped while sending the tuning read.") from exc

            if not self._bound(session, generation, websocket):
                if mutation:
                    return TuningResponse(request_id, "unknown",
                        "The robot connection changed while sending; the outcome is "
                        "unknown. Query the job instead of resending.")
                raise TuningUnavailable("The robot connection changed while sending.")

            try:
                response = await asyncio.wait_for(future, timeout)
            except asyncio.TimeoutError:
                if mutation:
                    return TuningResponse(request_id, "unknown",
                        "The robot did not answer in time; the outcome is unknown. "
                        "Query the durable job — do not resend the change.")
                raise TuningUnavailable(
                    "The robot did not answer the tuning read in time.")

            if response is None:  # session lost sentinel from disconnected()
                if mutation:
                    return TuningResponse(request_id, "unknown",
                        "The robot connection dropped; the outcome is unknown.")
                raise TuningUnavailable("The robot connection dropped while reading tuning state.")

            if response.outcome == "ok" and not self._bound(session, generation, websocket):
                # The reply was validated against the bound session, but the
                # session has since been replaced. Fail closed rather than let
                # a stale view read as current.
                if mutation:
                    return TuningResponse(request_id, "unknown",
                        "The robot session was replaced; the outcome is unknown.")
                raise TuningUnavailable(
                    "The robot session was replaced while reading tuning state.")
            self._update_desired(robot_id, session, response.snapshot)
            return response
        finally:
            self._pending.pop(request_id, None)

    def _bound(self, session: "RobotSession", generation: int, websocket: Any) -> bool:
        return (self.hub.robots.get(session.robot_id) is session
                and session.websocket is websocket
                and session.generation == generation)

    def _bound_entry(self, entry: _Pending, session: "RobotSession") -> bool:
        # The reply must arrive on the exact session generation *and* socket the
        # request was sent on; a replaced socket is a superseded connection even
        # if the session object was not rebuilt.
        return (entry.session is session and entry.generation == session.generation
                and entry.websocket is session.websocket)

    def _seen_recently(self, request_id: str) -> bool:
        self._prune_seen()
        return request_id in self._seen

    def _prune_seen(self) -> None:
        if self.dedupe_ttl_s <= 0:
            return
        cutoff = self._clock() - self.dedupe_ttl_s
        # Insertion order is clock order: the oldest ids are at the front.
        while self._seen:
            oldest = next(iter(self._seen))
            if self._seen[oldest] < cutoff:
                del self._seen[oldest]
            else:
                break

    def _remember(self, request_id: str) -> None:
        self._seen[request_id] = self._clock()
        self._seen.move_to_end(request_id)
        self._prune_seen()
        while len(self._seen) > self.dedupe_limit:
            self._seen.popitem(last=False)

    def _optimistic_check(self, robot_id: str, session: "RobotSession",
                          payload: dict[str, Any], operation: str) -> None:
        """Reject a definitively stale revision/session from a truthful cache."""
        cached = self._desired.get(robot_id)
        if cached is None:
            return
        generation, revision, session_id = cached
        if generation != session.generation:
            return
        if payload.get("generation", {}).get("session_id") != session_id:
            raise TuningConflict(
                "The tuning session changed; refresh the catalog before changing settings.")
        supplied = payload.get("base_revision") if operation == "save" else payload.get("expected_revision")
        if isinstance(supplied, int) and supplied != revision:
            if operation == "save":
                raise TuningConflict("A newer tuning revision exists; refresh before saving.")
            raise TuningConflict("A newer tuning revision exists; refresh before applying.")

    def _update_desired(self, robot_id: str, session: "RobotSession",
                        snapshot: dict[str, Any] | None) -> None:
        if snapshot is None:
            return
        desired = snapshot.get("desired_revision")
        session_id = snapshot.get("session_id")
        if isinstance(desired, int) and isinstance(session_id, str):
            self._desired[robot_id] = (session.generation, desired, session_id)

    # -- robot side --------------------------------------------------------------

    async def handle_response(self, session: "RobotSession", envelope: Envelope,
                               payload: Any) -> None:
        """Route one robot ``tuning.response`` to the request awaiting it."""
        if self.hub.robots.get(session.robot_id) is not session or session.websocket is None:
            return
        data = _as_mapping(payload)
        if data is None:
            log.warning("dropping tuning reply with an unreadable payload")
            return
        try:
            normalized = validation.validate_response(data, self._catalog())
        except TuningInvalid as exc:
            log.warning("invalid tuning reply: %s", exc.detail)
            rid = validation.canonical_uuid(data.get("request_id"))
            entry = self._pending.get(rid) if rid else None
            if entry is not None and self._bound_entry(entry, session) and not entry.future.done():
                entry.future.set_result(TuningResponse(
                    entry.request_id, "unknown",
                    "The robot's tuning reply did not validate; the outcome is unknown."))
            return

        if envelope is not None and getattr(envelope, "robot_id", session.robot_id) != session.robot_id:
            return
        if normalized["outcome"] == "ok" and normalized["snapshot"] is not None:
            snapshot = normalized["snapshot"]
            self._policies[session.robot_id] = (session, session.websocket, snapshot, time.monotonic())
        entry = self._pending.get(normalized["request_id"])
        if entry is None:
            return  # unknown/closed request: never resurrect it
        if not self._bound_entry(entry, session):
            log.warning("dropping superseded tuning reply for %s", entry.request_id)
            return
        if envelope is not None and getattr(envelope, "robot_id", entry.robot_id) != entry.robot_id:
            return
        if entry.mutation:
            self._remember(entry.request_id)
        if not entry.future.done():
            entry.future.set_result(TuningResponse(
                request_id=normalized["request_id"], outcome=normalized["outcome"],
                detail=normalized["detail"], snapshot=normalized["snapshot"],
                job=normalized["job"]))

    def disconnected(self, session: "RobotSession") -> None:
        """A robot connection was lost: pending outcomes are unknown."""
        cached = self._policies.get(session.robot_id)
        if cached is not None and cached[0] is session:
            self._policies.pop(session.robot_id, None)
        for entry in list(self._pending.values()):
            if entry.session is not session or entry.future.done():
                continue
            if entry.mutation:
                entry.future.set_result(TuningResponse(
                    entry.request_id, "unknown",
                    "The robot connection dropped; the outcome is unknown."))
            else:
                entry.future.set_result(None)

    async def shutdown(self) -> None:
        for entry in list(self._pending.values()):
            if not entry.future.done():
                if entry.mutation:
                    entry.future.set_result(TuningResponse(
                        entry.request_id, "unknown",
                        "The server is shutting down; the outcome is unknown."))
                else:
                    entry.future.set_result(None)
        self._pending.clear()
        self._policies.clear()

    def teleop_policy(self, robot_id: str) -> dict | None:
        cached = self._policies.get(robot_id)
        if cached is None:
            return None
        session, websocket, snapshot, received = cached
        if not self._bound(session, session.generation, websocket) or not snapshot.get("active_known"):
            return None
        limits = snapshot.get("effective_limits")
        base = session.state.base_state.data
        digest = snapshot.get("digest")
        if not isinstance(limits, dict) or base is None or not isinstance(digest, str):
            return None
        if snapshot.get("hardware_session_generation") != base.session_generation:
            return None
        return dict(limits, generation=f'{snapshot["session_id"]}:{snapshot["active_revision"]}:{digest.removeprefix("sha256:")}',
                    received_mono=received, robot_generation=base.session_generation)
