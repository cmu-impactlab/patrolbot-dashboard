"""Read-only tuning and policy relay, bound to the exact robot session."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..protocol.envelope import Envelope, encode
from . import validation
from .authz import actor_from_user
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
    READ_CAPABILITY,
)

if TYPE_CHECKING:
    from ..telemetry.hub import RobotSession

log = logging.getLogger("tuning")

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
                 read_timeout_s: float = 5.0, max_pending: int = 8) -> None:
        self.hub = hub
        self.read_timeout_s = read_timeout_s
        self.max_pending = max_pending
        # Reviewed catalog: injected (tests/ops) or loaded once from the
        # installed registry. Validation needs the full catalog (``parameters``),
        # never a partially built id index.
        self._registry = registry
        self._registry_data: dict[str, Any] | None = None
        self._pending: dict[str, _Pending] = {}
        self._policies: dict[str, tuple[Any, Any, dict, float]] = {}

    # -- registry ---------------------------------------------------------------

    def _catalog(self) -> dict[str, Any]:
        # Fail closed if the reviewed catalog is missing/corrupt.
        data = self._registry if self._registry is not None else self._registry_data
        if data is None:
            data = validation.load_registry()
            self._registry_data = data
        return data

    # -- public API --------------------------------------------------------------

    async def request(self, user: Any, robot_id: str, request: Any) -> TuningResponse:
        """Forward only inspection requests, regardless of advertised capabilities."""
        if not isinstance(request, dict) or request.get("operation") not in ("read", "status"):
            raise TuningInvalid("Robot tuning is read-only.")
        payload = validation.validate_request(request, self._catalog())
        return await self._request_read(user, robot_id, payload)

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
        # Reads have no authoritative session token yet.
        return {
            "contract": CONTRACT,
            "version": CONTRACT_VERSION,
            "operation": operation,
            "request_id": str(uuid.uuid4()),
            "generation": {"capability": CAPABILITY, "session_id": str(uuid.uuid4())},
        }

    # -- internals ---------------------------------------------------------------

    async def _request_read(self, user: Any, robot_id: str, payload: dict[str, Any]) -> TuningResponse:
        request_id = payload["request_id"]
        if request_id in self._pending:
            raise TuningConflict("That tuning request is already in flight.")

        session = self.hub.robots.get(robot_id)
        if session is None or session.websocket is None or session.state.connection != "online":
            raise TuningUnavailable("The robot is not connected right now.")
        if READ_CAPABILITY not in session.state.capabilities:
            raise TuningUnavailable(
                "This robot does not advertise robot_tuning_read_v1; tuning state "
                "cannot be read yet.")

        if len(self._pending) >= self.max_pending:
            raise TuningRateLimited("Tuning is busy; too many requests are in flight.")

        generation = session.generation
        websocket = session.websocket
        actor = actor_from_user(user)
        validation.validate_actor(actor)
        wrapper = {"request": payload, "actor": actor}
        frame = encode("tuning.request", robot_id, self.hub._next_seq(), wrapper)

        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        entry = _Pending(request_id=request_id, robot_id=robot_id, session=session,
                         generation=generation, websocket=websocket, future=future)
        self._pending[request_id] = entry
        timeout = self.read_timeout_s
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
                raise TuningUnavailable(
                    "The robot connection dropped while sending the tuning read.") from exc

            if not self._bound(session, generation, websocket):
                raise TuningUnavailable("The robot connection changed while sending.")

            try:
                response = await asyncio.wait_for(future, timeout)
            except asyncio.TimeoutError:
                raise TuningUnavailable(
                    "The robot did not answer the tuning read in time.")

            if response is None:  # session lost sentinel from disconnected()
                raise TuningUnavailable("The robot connection dropped while reading tuning state.")

            if response.outcome == "ok" and not self._bound(session, generation, websocket):
                # The reply was validated against the bound session, but the
                # session has since been replaced. Fail closed rather than let
                # a stale view read as current.
                raise TuningUnavailable(
                    "The robot session was replaced while reading tuning state.")
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
            entry.future.set_result(None)

    async def shutdown(self) -> None:
        for entry in list(self._pending.values()):
            if not entry.future.done():
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
