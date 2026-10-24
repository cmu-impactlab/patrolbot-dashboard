"""Typed bridge adapter for robot tuning over the existing authenticated socket.

This adapter is deliberately thin: it owns a bounded, thread-safe ingress queue,
dispatches one typed ROS service call at a time on the ROS executor, and relays
the core's result back on the existing WebSocket. It publishes nothing, owns no
lifecycle, and never retries or replays a request.

* ``/patrolbot/tuning`` is called with the fixed ``RobotTuning`` service; the
  wrapper (request + server-derived actor) travels in ``payload_json`` and the
  result comes back in ``result_json``.
* Advertise ``robot_tuning_read_v1`` only while the core service is actually
  reachable (``ready``). NEVER advertise ``robot_tuning_v1`` here: mutation
  support is not proven, so the server must keep refusing writes.
* A request whose outcome cannot be established (call failure, transport change
  while in flight, unreadable/mismatched result) is reported as ``unknown``.
  A lost transport clears the queue and marks the in-flight call unknown — it is
  never re-dispatched after reconnect.
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from collections import deque
from typing import Any, Callable
from uuid import UUID
from uuid import uuid4

log = logging.getLogger("web_bridge.tuning")

CAPABILITY_READ = "robot_tuning_read_v1"
CAPABILITY_MUTATE = "robot_tuning_v1"
SERVICE_NAME = "/patrolbot/tuning"

CONTRACT = "patrolbot-robot-tuning"
CONTRACT_VERSION = 1
CAPABILITY = "robot_tuning_v1"
OPERATIONS = ("read", "save", "apply", "redeploy", "status", "restore")
# Operations that mutate robot-owned state and therefore must carry a
# server-signed authorization block. `read`/`status` never do.
MUTATION_OPERATIONS = ("save", "apply", "redeploy", "restore")
ROLES = ("administrator", "operator", "observer")

MAX_REQUEST_BYTES = 18 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
MAX_QUEUE = 8
MAX_USERNAME = 128
MAX_ROBOT_ID = 128
# Structural bounds mirroring contracts/robot-tuning-auth.json. The bridge
# checks only the *shape* of the claims; it holds no key and never verifies the
# HMAC. Expiry is decided by the core against its own clock.
MAX_AUTHORIZATION_LIFETIME_MS = 30000
MAX_SAFE_INT = 2 ** 53 - 1
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


def maintenance_context(request) -> dict:
    value = {key: request[key] if isinstance(request, dict) else getattr(request, key)
             for key in ('job_id', 'digest', 'plan_hash')}
    if canonical_uuid(value['job_id']) is None or any(
            not isinstance(value[key], str) or re.fullmatch(r'sha256:[0-9a-f]{64}', value[key]) is None
            for key in ('digest', 'plan_hash')):
        raise ValueError('invalid maintenance owner')
    return value


def maintenance_payload(raw: str) -> dict:
    """Fixed expected policy, not an authorization or a source of live values."""
    if not isinstance(raw, str) or len(raw.encode()) > 2048:
        raise ValueError('invalid maintenance payload size')
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('duplicate maintenance field')
            value[key] = item
        return value
    value = json.loads(raw, object_pairs_hook=pairs)
    if (not isinstance(value, dict) or set(value) != {'version', 'revision', 'session_id',
            'hardware_session_generation', 'effective_limits'}
            or type(value['version']) is not int or value['version'] != 1
            or type(value['revision']) is not int or not 1 <= value['revision'] <= MAX_SAFE_INT
            or canonical_uuid(value['session_id']) is None
            or type(value['hardware_session_generation']) is not int
            or not 0 <= value['hardware_session_generation'] <= MAX_SAFE_INT):
        raise ValueError('invalid expected policy binding')
    limits = value['effective_limits']
    if (not isinstance(limits, dict) or set(limits) != {'max_linear_m_s', 'max_angular_rad_s'}
            or any(type(limits[key]) not in (int, float) or not math.isfinite(limits[key])
                   or not 0 <= limits[key] <= hard for key, hard in
                   (('max_linear_m_s', .30), ('max_angular_rad_s', .70)))):
        raise ValueError('invalid expected policy limits')
    return value


def _finite_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _authorization_reason(authorization: Any) -> str | None:
    """Shape-check a server authorization block; never verify the HMAC.

    The bridge holds no key, so it must not pretend to authenticate a mutation:
    the core verifies the signature, freshness and robot binding. This rejects a
    missing, malformed or impossible claim so a mangled or unsigned envelope is
    refused locally instead of reaching the core.
    """
    fields = {"version", "robot_id", "issued_at_ms", "expires_at_ms", "signature"}
    if not isinstance(authorization, dict) or set(authorization) != fields:
        return "The tuning authorization is malformed."
    if type(authorization["version"]) is not int or authorization["version"] != 1:
        return "The tuning authorization is malformed."
    robot_id = authorization["robot_id"]
    if not isinstance(robot_id, str) or not 1 <= len(robot_id) <= MAX_ROBOT_ID:
        return "The tuning authorization robot binding is invalid."
    issued = authorization["issued_at_ms"]
    expires = authorization["expires_at_ms"]
    if (not _finite_int(issued) or not _finite_int(expires)
            or issued < 0 or expires < 0
            or issued > MAX_SAFE_INT or expires > MAX_SAFE_INT
            or not 0 < expires - issued <= MAX_AUTHORIZATION_LIFETIME_MS):
        return "The tuning authorization lifetime is invalid."
    signature = authorization["signature"]
    if not isinstance(signature, str) or re.fullmatch("[0-9a-f]{64}", signature) is None:
        return "The tuning authorization is malformed."
    return None


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
        return "The tuning operation is not supported."
    operation = request["operation"]
    # Role first: a non-administrator mutation is refused regardless of whether
    # any authorization block is attached (a forged signature is not authority).
    if operation not in ("read", "status") and actor["role"] != "administrator":
        return "Changing tuning requires an administrator."
    authorization = wrapper.get("authorization")
    if authorization is not None:
        authorization_reason = _authorization_reason(authorization)
        if authorization_reason is not None:
            return authorization_reason
    # A mutation must arrive with the server's signed claims. This is a shape
    # gate only; the core performs the real signature/expiry/robot check.
    if operation in MUTATION_OPERATIONS and authorization is None:
        return "Changing tuning requires a signed server authorization."
    common = {"contract", "version", "operation", "request_id", "generation"}
    extras = {"read": set(), "status": {"job_id"}, "save": {"base_revision", "changes"},
              "apply": {"expected_revision", "review"}, "redeploy": {"expected_revision", "review"},
              "restore": {"expected_revision", "restore_revision", "target", "review"}}[operation]
    if set(request) - common - extras or not common.issubset(request):
        return "The tuning request has unexpected fields."
    if "review" in request:
        review = request["review"]
        if (not isinstance(review, dict) or set(review) != {"digest", "plan_hash"}
                or any(not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value)
                       for value in review.values())):
            return "The reviewed activation binding is invalid."
    for key in ("base_revision", "expected_revision", "restore_revision"):
        if key in extras and (type(request.get(key)) is not int or not 0 <= request[key] <= 2**53 - 1):
            return "The tuning revision is invalid."
    if operation == "restore" and request.get("target") != "last_known_good":
        return "The restore target is invalid."
    if operation == "status" and "job_id" in request and canonical_uuid(request["job_id"]) is None:
        return "The tuning job id is invalid."
    if operation == "save":
        changes = request.get("changes")
        if not isinstance(changes, list) or not 1 <= len(changes) <= 32:
            return "The change batch is invalid."
        ids = set()
        for change in changes:
            if not isinstance(change, dict) or set(change) != {"id", "value"}:
                return "The change shape is invalid."
            pid, value = change["id"], change["value"]
            if not isinstance(pid, str) or len(pid) > 64 or not re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z0-9_]+)*", pid) or pid in ids:
                return "The change identifier is invalid."
            ids.add(pid)
            if type(value) not in (int, float, bool) or (type(value) is float and not math.isfinite(value)):
                return "The change value is invalid."
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
        self._policy_lock = threading.RLock()
        self._queue: deque[Any] = deque()
        self._inflight: dict[str, Any] | None = None
        self._srv_type = srv_type
        self._client: Any = None
        self._error: str | None = None
        self.dropped = 0
        self.unknown_outcomes = 0
        self._snapshot = None
        self._snapshot_at = None
        self._maintenance_observed = None
        self._maintenance_at = None
        self._committed_policy = None
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
        """Capability tokens this adapter may advertise.

        Read only, and only while the core service is reachable. Mutations stay
        unadvertised until safe activation support is proven elsewhere.
        """
        result = [CAPABILITY_READ] if self.ready else []
        snapshot = self._snapshot or {}
        fresh = self._snapshot_at is not None and 0 <= self._clock() - self._snapshot_at <= 3
        if self.ready and fresh and snapshot.get("save_supported") is True:
            result.append("robot_tuning_write_v1")
            if snapshot.get("activation_supported") is True:
                result.append(CAPABILITY_MUTATE)
        if self.policy_required:
            result.append("robot_tuning_policy_v1")
        return result

    @property
    def policy_required(self) -> bool:
        return self._policy_required

    def teleop_policy(self):
        with self._policy_lock:
            snapshot, snapshot_at = self._snapshot, self._snapshot_at
            committed = self._committed_policy
        if committed is not None:
            base = getattr(self.node, '_latest_base_state', None) or {}
            return dict(committed) if committed['robot_generation'] == base.get('session_generation') else None
        if not isinstance(snapshot, dict) or snapshot.get("active_known") is not True or snapshot_at is None:
            return None
        base = getattr(self.node, "_latest_base_state", None) or {}
        if snapshot.get("hardware_session_generation") != base.get("session_generation"):
            return None
        limits = snapshot.get("effective_limits")
        if not isinstance(limits, dict) or not isinstance(snapshot.get("digest"), str):
            return None
        return dict(limits, generation=f'{snapshot["session_id"]}:{snapshot["active_revision"]}:{snapshot["digest"].removeprefix("sha256:")}',
                    received_mono=snapshot_at, robot_generation=base["session_generation"])

    def maintenance_policy(self, owner: dict, expected: dict, *, committed: bool = False) -> dict:
        """Match an expected cache against independently observed core runtime.

        Core must supply ``maintenance_policy`` from its live candidate policy
        observation under the exact command hold, never compiled DESIRED. Until
        that hook is implemented staging fails closed. Committed verification
        instead uses the ordinary ACTIVE/runtime readback binding.
        """
        with self._policy_lock:
            snapshot, received = self._snapshot, self._snapshot_at
            direct, direct_at = self._maintenance_observed, self._maintenance_at
        if direct is not None and direct.get('phase') == ('committed' if committed else 'candidate'):
            observed, received = direct, direct_at
        else:
            observed = snapshot.get('maintenance_policy') if isinstance(snapshot, dict) else None
        if (not isinstance(snapshot, dict) or received is None
                or not 0 <= self._clock() - received <= 3.0 or not self.ready):
            raise ValueError('fresh core runtime policy observation required')
        base = getattr(self.node, '_latest_base_state', None) or {}
        if (type(base.get('session_generation')) is not int
                or expected['hardware_session_generation'] != base['session_generation']
                or snapshot.get('session_id') != expected['session_id']):
            raise ValueError('core/hardware session changed')
        if committed and (direct is None or direct.get('phase') != 'committed'):
            if (snapshot.get('active_known') is not True
                    or snapshot.get('active_revision') != expected['revision']
                    or snapshot.get('committed_active_revision') != expected['revision']
                    or snapshot.get('digest') != owner['digest']
                    or snapshot.get('hardware_session_generation') != expected['hardware_session_generation']
                    or snapshot.get('effective_limits') != expected['effective_limits']):
                raise ValueError('committed ACTIVE runtime policy mismatch')
        else:
            fields = set(expected) | set(owner) | {'phase', 'runtime_verified', 'command_hold',
                                                  'observed_age_s', 'committed_active_revision'}
            if (not isinstance(observed, dict) or set(observed) != fields
                    or observed.get('phase') != ('committed' if committed else 'candidate')
                    or observed.get('committed_active_revision') != (expected['revision'] if committed else None)
                    or observed.get('runtime_verified') is not True
                    or observed.get('command_hold') is not True
                    or any(observed.get(key) != value for key, value in dict(owner, **expected).items())):
                raise ValueError('exact held candidate runtime observation required')
            age = observed['observed_age_s']
            if (type(age) not in (int, float) or not math.isfinite(age)
                    or age < 0 or age + self._clock() - received > 3.0):
                raise ValueError('candidate runtime observation expired')
        return dict(expected, **owner, phase='committed' if committed else 'candidate')

    def observe_maintenance_policy(self, observed: dict | None, *, received_at: float | None = None):
        """Only the fixed, identity-checked local core topic supplies this hook.

        This side channel avoids a writer/read-service cycle during apply. It is
        not WebSocket ingress and is never populated from a browser request.
        """
        with self._policy_lock:
            self._maintenance_observed = observed
            self._maintenance_at = received_at

    def bind_committed_maintenance_policy(self, owner: dict, expected: dict):
        self.maintenance_policy(owner, expected, committed=True)
        with self._policy_lock:
            observed, received = self._maintenance_observed, self._maintenance_at
            if observed is not None and observed.get('phase') == 'committed':
                self._committed_policy = dict(expected['effective_limits'],
                    generation=f'{expected["session_id"]}:{expected["revision"]}:{owner["digest"].removeprefix("sha256:")}',
                    received_mono=received - observed['observed_age_s'],
                    robot_generation=expected['hardware_session_generation'])

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
            # Charge the complete service round trip to policy freshness.
            with self._policy_lock:
                self._snapshot, self._snapshot_at = snapshot, inflight['sent_at']
                # Never replace an explicitly bound committed policy by an old
                # delayed ACTIVE read. The normal reader takes over once it has
                # observed this same (or a later) committed runtime revision.
                if self._committed_policy is not None and snapshot.get('active_known') is True:
                    bound = self._committed_policy['generation'].split(':')
                    revision = snapshot.get('active_revision')
                    if (type(revision) is int and snapshot.get('session_id') == bound[0]
                            and (revision > int(bound[1]) or (revision == int(bound[1])
                                and snapshot.get('digest') == 'sha256:' + bound[2]))):
                        self._committed_policy = None
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
        with self._policy_lock:
            self._snapshot = None
            self._snapshot_at = None
            self._maintenance_observed = self._maintenance_at = None
            self._committed_policy = None
        if self._inflight is not None:
            self._inflight = None
            self.unknown_outcomes += 1
