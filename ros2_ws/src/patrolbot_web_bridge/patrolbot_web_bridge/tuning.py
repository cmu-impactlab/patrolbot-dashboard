"""Typed bridge adapter for robot tuning over the existing authenticated socket.

This adapter is deliberately thin: it owns a bounded, thread-safe ingress queue,
dispatches one typed ROS service call at a time on the ROS executor, and relays
the core's result back on the existing WebSocket. It publishes nothing, owns no
lifecycle, and never retries or replays a request.

* ``/patrolbot/tuning`` is called with the fixed ``RobotTuning`` service; the
  wrapper (request + server-derived actor) travels in ``payload_json`` and the
  result comes back in ``result_json``.
* Advertise ``robot_tuning_read_v1`` only while the core service is actually
  reachable (``ready``). This adapter does not forward tuning mutations.
* A request whose outcome cannot be established (call failure, transport change
  while in flight, unreadable/mismatched result) is reported as ``unknown``.
  A lost transport clears the queue and marks the in-flight call unknown — it is
  never re-dispatched after reconnect.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from typing import Any, Callable
from uuid import UUID
from uuid import uuid4

log = logging.getLogger("web_bridge.tuning")

CAPABILITY_READ = "robot_tuning_read_v1"
SERVICE_NAME = "/patrolbot/tuning"

CONTRACT = "patrolbot-robot-tuning"
CONTRACT_VERSION = 1
CAPABILITY = "robot_tuning_v1"
OPERATIONS = ("read", "status")
ROLES = ("administrator", "operator", "observer")

MAX_REQUEST_BYTES = 18 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
MAX_QUEUE = 8
MAX_USERNAME = 128
DEFAULT_DEADLINE_S = 30.0
TICK_PERIOD_S = 0.05

_ZERO_UUID = "00000000-0000-0000-0000-000000000000"


def canonical_uuid(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) != 36:
        return None
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None
    return value if str(parsed) == value else None


def _finite_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def wrapper_reason(wrapper: Any) -> str | None:
    """Return a refusal reason for a malformed/unapproved wrapper, else None.

    This mirrors the trusted server's canonical checks as defense in depth on
    the robot side. It never accepts a raw file/node path and never trusts a
    browser-supplied role (the actor is server-derived).
    """
    if (not isinstance(wrapper, dict) or not {"request", "actor"} <= set(wrapper)
            or set(wrapper) - {"request", "actor", "authorization"}):
        return "The tuning envelope is malformed."
    actor = wrapper.get("actor")
    if (not isinstance(actor, dict) or set(actor) != {"id", "username", "role"}
            or not _finite_int(actor.get("id"))
            or not isinstance(actor.get("username"), str)
            or not 1 <= len(actor["username"]) <= MAX_USERNAME
            or actor.get("role") not in ROLES):
        return "The tuning actor block is invalid."
    request = wrapper.get("request")
    if not isinstance(request, dict):
        return "The tuning request is malformed."
    if request.get("contract") != CONTRACT or type(request.get("version")) is not int or request["version"] != CONTRACT_VERSION:
        return "The tuning contract is not supported."
    if request.get("operation") not in OPERATIONS:
        return "Robot tuning is read-only."
    operation = request["operation"]
    if wrapper.get("authorization") is not None:
        return "Read-only tuning requests do not accept authorization claims."
    common = {"contract", "version", "operation", "request_id", "generation"}
    extras = {"job_id"} if operation == "status" else set()
    if set(request) - common - extras or not common.issubset(request):
        return "The tuning request has unexpected fields."
    if operation == "status" and "job_id" in request and canonical_uuid(request["job_id"]) is None:
        return "The tuning job id is invalid."
    if canonical_uuid(request.get("request_id")) is None:
        return "The tuning request id is not canonical."
    generation = request.get("generation")
    if (not isinstance(generation, dict)
            or set(generation) != {"capability", "session_id"}
            or generation.get("capability") != CAPABILITY
            or canonical_uuid(generation.get("session_id")) is None):
        return "The tuning generation binding is invalid."
    try:
        encoded = json.dumps(wrapper, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError, UnicodeError):
        return "The tuning envelope is not serializable."
    if len(encoded.encode("utf-8")) > MAX_REQUEST_BYTES:
        return "The tuning envelope is too large."
    return None


def _unwrap(payload: Any) -> Any:
    if isinstance(payload, dict) and "request" not in payload and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload


class _SimpleRequest:
    def __init__(self) -> None:
        self.payload_json = ""


class RosTuning:
    """Bounded, single-in-flight tuning service adapter; publishes nothing."""

    def __init__(self, node: Any, ws: Any, *, service_name: str = SERVICE_NAME,
                 srv_type: Any = None, client_factory: Callable[[], Any] | None = None,
                 tick_period_s: float = TICK_PERIOD_S,
                 deadline_s: float = DEFAULT_DEADLINE_S,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.node = node
        self.ws = ws
        self.service_name = service_name
        self.deadline_s = deadline_s
        self._clock = clock
        self._lock = threading.Lock()
        self._queue: deque[Any] = deque()
        self._inflight: dict[str, Any] | None = None
        self._srv_type = srv_type
        self._client: Any = None
        self._error: str | None = None
        self.dropped = 0
        self.unknown_outcomes = 0
        self._snapshot = None
        self._snapshot_at = None
        self._last_probe = float("-inf")
        self._policy_required = False
        if client_factory is not None:
            try:
                self._client = client_factory()
            except Exception as exc:  # noqa: BLE001 - surfaced via .error
                self._error = f"Tuning client creation failed: {exc}"
        else:
            if srv_type is None:
                try:
                    from patrolbot_interfaces.srv import RobotTuning  # type: ignore
                    srv_type = RobotTuning
                except Exception as exc:  # noqa: BLE001
                    self._error = f"RobotTuning interface unavailable: {exc}"
            if srv_type is not None:
                self._srv_type = srv_type
                try:
                    self._client = node.create_client(srv_type, service_name)
                except Exception as exc:  # noqa: BLE001
                    self._error = f"Tuning service client creation failed: {exc}"
        create_timer = getattr(node, "create_timer", None)
        if callable(create_timer) and tick_period_s > 0:
            try:
                create_timer(tick_period_s, self.tick)
            except Exception:  # noqa: BLE001 - a timer failure must not break the node
                log.exception("could not create the tuning timer")

    # -- inspection ---------------------------------------------------------------

    @property
    def error(self) -> str | None:
        return self._error

    @property
    def ready(self) -> bool:
        """True only when the core service is actually reachable.

        This is existence/reachability inspection, never proof that activation
        is safe or that the capability is commissioned.
        """
        client = self._client
        if client is None:
            return False
        try:
            return bool(client.service_is_ready())
        except Exception:  # noqa: BLE001
            return False

    def capabilities(self) -> list[str]:
        """Advertise reachable inspection and any required motion policy."""
        result = [CAPABILITY_READ] if self.ready else []
        if self.policy_required:
            result.append("robot_tuning_policy_v1")
        return result

    @property
    def policy_required(self) -> bool:
        return self._policy_required

    def teleop_policy(self):
        snapshot = self._snapshot
        if not isinstance(snapshot, dict) or not snapshot.get("active_known") or self._snapshot_at is None:
            return None
        base = getattr(self.node, "_latest_base_state", None) or {}
        if snapshot.get("hardware_session_generation") != base.get("session_generation"):
            return None
        limits = snapshot.get("effective_limits")
        if not isinstance(limits, dict) or not isinstance(snapshot.get("digest"), str):
            return None
        return dict(limits, generation=f'{snapshot["session_id"]}:{snapshot["active_revision"]}:{snapshot["digest"].removeprefix("sha256:")}',
                    received_mono=self._snapshot_at, robot_generation=base["session_generation"])

    # -- ingress ------------------------------------------------------------------

    def enqueue(self, payload: Any) -> None:
        wrapper = _unwrap(payload)
        request_id = canonical_uuid(
            wrapper.get("request", {}).get("request_id")
            if isinstance(wrapper, dict) and isinstance(wrapper.get("request"), dict)
            else None) or _ZERO_UUID
        with self._lock:
            if len(self._queue) >= MAX_QUEUE:
                self.dropped += 1
                self._send_unknown(request_id,
                                   "The tuning adapter is busy; the request was not dispatched.")
                return
            self._queue.append(wrapper)

    # -- ROS executor -------------------------------------------------------------

    def tick(self) -> None:
        now = self._clock()
        inflight = self._inflight
        if inflight is not None:
            future = inflight.get("future")
            if future is not None and future.done():
                self._finish(inflight)
                return
            if now - inflight["sent_at"] > inflight["deadline_s"]:
                self._inflight = None
                self._send_unknown(inflight["request_id"],
                                   "The tuning service did not answer in time; the outcome is unknown.")
            return
        with self._lock:
            if not self._queue:
                connected = getattr(self.ws, "connected", None)
                if not (connected is not None and connected.is_set() and self.ready and now - self._last_probe >= 1.0):
                    return
                self._last_probe = now
                wrapper = {"actor": {"id": 0, "username": "web-bridge-policy-reader", "role": "observer"},
                    "request": {"contract": CONTRACT, "version": CONTRACT_VERSION, "operation": "read",
                        "request_id": str(uuid4()), "generation": {"capability": CAPABILITY, "session_id": _ZERO_UUID}}}
            else:
                wrapper = self._queue.popleft()
        self._dispatch(wrapper)

    def _dispatch(self, wrapper: Any) -> None:
        reason = wrapper_reason(wrapper)
        request = wrapper.get("request") if isinstance(wrapper, dict) else None
        raw_id = request.get("request_id") if isinstance(request, dict) else None
        request_id = canonical_uuid(raw_id) or _ZERO_UUID
        if reason is not None:
            self._send_response(request_id, "refused", reason)
            return
        if not self.ready:
            self._send_response(request_id, "refused",
                                "The robot tuning service is not reachable.")
            return
        try:
            srv_request = self._srv_type.Request() if self._srv_type is not None else _SimpleRequest()
            srv_request.payload_json = json.dumps(wrapper, separators=(",", ":"), allow_nan=False)
        except Exception as exc:  # noqa: BLE001
            self._send_response(request_id, "failed", f"The tuning request could not be encoded: {exc}")
            return
        generation = int(getattr(self.ws, "connection_generation", 0) or 0)
        try:
            future = self._client.call_async(srv_request)
        except Exception as exc:  # noqa: BLE001
            self._send_unknown(request_id, f"The tuning call could not be started: {exc}")
            return
        self._inflight = {"request_id": request_id, "future": future,
                          "generation": generation, "sent_at": self._clock(),
                          "deadline_s": self.deadline_s}

    def _finish(self, inflight: dict[str, Any]) -> None:
        self._inflight = None
        request_id = inflight["request_id"]
        current_generation = int(getattr(self.ws, "connection_generation", 0) or 0)
        if inflight["generation"] != current_generation:
            # The transport changed while the call was in flight. The server has
            # already been told the outcome is unknown (or timed out); do not
            # relay a stale result and never replay the request.
            self.unknown_outcomes += 1
            return
        try:
            response = inflight["future"].result()
        except Exception as exc:  # noqa: BLE001
            self._send_unknown(request_id, f"The tuning service call failed: {exc}")
            return
        payload_json = getattr(response, "result_json", None)
        if not isinstance(payload_json, str):
            self._send_unknown(request_id, "The tuning service returned no result.")
            return
        if len(payload_json.encode("utf-8", "ignore")) > MAX_RESPONSE_BYTES:
            self._send_unknown(request_id, "The tuning result was too large to relay.")
            return
        try:
            def pairs(items):
                result = {}
                for key, value in items:
                    if key in result:
                        raise ValueError("duplicate key")
                    result[key] = value
                return result
            def constant(name):
                raise ValueError(name)
            data = json.loads(payload_json, parse_constant=constant, object_pairs_hook=pairs)
            json.dumps(data, allow_nan=False)
        except (ValueError, RecursionError):
            self._send_unknown(request_id, "The tuning result was not valid JSON.")
            return
        if not isinstance(data, dict) or canonical_uuid(data.get("request_id")) != request_id:
            self._send_unknown(request_id, "The tuning result did not match the request.")
            return
        snapshot = data.get("snapshot")
        if data.get("outcome") == "ok" and isinstance(snapshot, dict):
            self._snapshot, self._snapshot_at = snapshot, self._clock()
            # A previously configured policy must never silently downgrade to
            # legacy merely because a later read fails or the transport drops.
            self._policy_required = self._policy_required or snapshot.get("policy_required") is True
        self._send(data)

    # -- egress / lifecycle -------------------------------------------------------

    def _send(self, data: dict[str, Any]) -> None:
        try:
            self.ws.send("tuning.response", data)
        except Exception:  # noqa: BLE001
            log.exception("failed to send a tuning response")

    def _send_response(self, request_id: str, outcome: str, detail: str) -> None:
        self._send({"request_id": request_id, "outcome": outcome, "detail": detail,
                    "snapshot": None, "job": None})

    def _send_unknown(self, request_id: str, detail: str) -> None:
        self.unknown_outcomes += 1
        self._send_response(request_id, "unknown", detail)

    def disconnected(self) -> None:
        """Transport lost: clear queued requests and mark in-flight unknown.

        Nothing is re-dispatched and no response is queued for a later
        reconnect — the server resolves the outstanding request by timeout.
        """
        with self._lock:
            self._queue.clear()
        self._snapshot = None
        self._snapshot_at = None
        if self._inflight is not None:
            self._inflight = None
            self.unknown_outcomes += 1
