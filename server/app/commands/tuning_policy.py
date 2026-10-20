"""Strict dynamic teleop speed policy: fixed hard bounds plus a trusted runtime cap.

Two distinct ideas live here and must never be conflated:

* The **fixed hard envelope** is the commissioned wire/commissioned physical
  bound pinned in ``contracts/web-teleop.json`` and the final
  ``teleop_velocity_smoother``: +/-0.30 m/s and +/-0.70 rad/s. A runtime policy
  may only *reduce* what manual input may request; it can never enlarge it.

* The **dynamic operational cap** is a small, trusted runtime value that a robot
  which advertises a tuning capability publishes. It belongs to one canonical
  configuration generation and is bound to one drive-base session.

Legacy robots (``web_teleop_v1`` only, no tuning capability) keep the fixed
commissioned envelope in an explicitly named ``legacy_v1`` mode, so an already
deployed safe robot keeps working unchanged.

A robot that advertises ``robot_tuning_v1`` or ``robot_tuning_policy_v1`` has
promised a dynamic policy. For such a robot a missing, malformed, stale or
session-mismatched policy is a refusal — never a silent fall back to the old,
higher fixed default. Advertised ``robot_tuning_read_v1`` alone is a *read*
capability: it grants neither writable tuning nor a dynamic cap, so it stays in
legacy mode.

The runtime policy is supplied ONLY by the trusted core adapter or server-side
state:

* ``hub.tuning.teleop_policy(robot_id)`` (explicit provider argument), or
* the ``session.state.tuning_policy`` attribute (documented default interface).

It is never read from a browser frame, a query parameter, an environment
variable, a filename or any other ambient source.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

# --- Capability declarations -------------------------------------------------

WEB_TELEOP_CAPABILITY = "web_teleop_v1"            # legacy fixed-session manual drive
TUNING_CAPABILITY = "robot_tuning_v1"              # writable tuning (also requires a policy)
TUNING_POLICY_CAPABILITY = "robot_tuning_policy_v1"  # dynamic operational cap
TUNING_READ_CAPABILITY = "robot_tuning_read_v1"    # read-only; no writable/dynamic policy

# A robot advertising either of these has promised a dynamic, trusted policy.
POLICY_CAPABILITIES = frozenset({TUNING_CAPABILITY, TUNING_POLICY_CAPABILITY})

# --- Modes -------------------------------------------------------------------

LEGACY_MODE = "legacy_v1"
DYNAMIC_MODE = "tuning_policy_v1"

# --- Fixed physical/wire hard envelope ---------------------------------------
# These are the commissioned upper bounds, sourced from
# contracts/web-teleop.json and the final teleop_velocity_smoother. Nothing in
# a runtime policy may raise them; validation rejects any attempt.
HARD_MAX_LINEAR_M_S = 0.30
HARD_MAX_ANGULAR_RAD_S = 0.70

# --- Fixed policy freshness --------------------------------------------------
# Deliberately a constant, not a setting: no ambient knob may widen how old a
# trusted policy may be before motion is refused. The robot adapter must refresh
# a dynamic policy at least this often while manual input is open. Chosen well
# above a missed 1 Hz policy sample and far below a human timescale.
POLICY_MAX_AGE_S = 5.0
# A receipt a little in the future is a clock wobble, not an indefinitely valid
# policy; reject anything beyond this so a bogus future timestamp cannot pin a
# policy as permanently fresh.
POLICY_FUTURE_TOLERANCE_S = 0.5

# Canonical configuration generation (boot UUID / revision digest). Bounded,
# opaque, never path-like.
MAX_GENERATION_LENGTH = 128

# A canonical generation is an ASCII UUID / hex digest / dotted revision, never
# arbitrary text. `str.isalnum()` would accept Unicode letters and digits from
# unrelated scripts, so the accepted set is spelled out explicitly.
_GENERATION_ALNUM = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
)
_GENERATION_EXTRA = frozenset("-_.:")
_GENERATION_ALLOWED = _GENERATION_ALNUM | _GENERATION_EXTRA


@dataclass(frozen=True)
class TeleopPolicy:
    """One resolved manual-drive policy: a mode plus its effective caps."""

    mode: str
    max_linear_m_s: float
    max_angular_rad_s: float = HARD_MAX_ANGULAR_RAD_S
    generation: str | None = None
    received_mono: float | None = None
    robot_generation: int | None = None

    @property
    def dynamic(self) -> bool:
        return self.mode == DYNAMIC_MODE

    @property
    def limits(self) -> tuple[float, float]:
        """(max_linear_m_s, max_angular_rad_s)."""
        return (self.max_linear_m_s, self.max_angular_rad_s)

    @property
    def signature(self) -> tuple:
        """Identity used to detect a policy change between frames.

        The canonical generation is the primary key; the caps are included so a
        provider bug that mutates limits without rotating the generation still
        reads as a change instead of silently applying the old, larger bound.
        """
        return (self.mode, self.max_linear_m_s, self.max_angular_rad_s, self.generation)

    def age(self, now: float | None = None) -> float | None:
        if self.received_mono is None:
            return None
        return max(0.0, (time.monotonic() if now is None else now) - self.received_mono)

    def over_policy(self, linear: float, angular: float) -> bool:
        """True when a requested frame exceeds this policy's effective caps."""
        return (abs(linear) > self.max_linear_m_s + 1e-9
                or abs(angular) > self.max_angular_rad_s + 1e-9)


# The explicit legacy policy. Robots without a tuning capability use exactly
# this, unchanged from the commissioned deployment.
LEGACY_POLICY = TeleopPolicy(
    mode=LEGACY_MODE,
    max_linear_m_s=HARD_MAX_LINEAR_M_S,
    max_angular_rad_s=HARD_MAX_ANGULAR_RAD_S,
)


def capability_requires_policy(capabilities: Any) -> bool:
    """Does this capability set promise a dynamic, trusted runtime policy?

    A bare string is treated as a single capability rather than iterated
    character-by-character: a mis-wired string must still fail closed toward
    requiring a policy, never silently fall back to the higher fixed default.
    """
    if isinstance(capabilities, str):
        capabilities = (capabilities,)
    try:
        return any(capability in POLICY_CAPABILITIES for capability in (capabilities or ()))
    except TypeError:
        return False


def provider_from_hub(hub: Any) -> Callable | None:
    """Return ``hub.tuning.teleop_policy`` when the parent wired it, else None."""
    adapter = getattr(hub, "tuning", None)
    function = getattr(adapter, "teleop_policy", None)
    return function if callable(function) else None


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        # A very large Python int (e.g. 10**400) is an `int` but overflows a
        # float; that must refuse, not raise into the command path.
        number = float(value)
    except (OverflowError, ValueError, TypeError):
        return None
    return number if math.isfinite(number) else None


def _valid_generation(value: Any) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_GENERATION_LENGTH:
        return False
    if "/" in value or "\\" in value or ".." in value:
        return False
    if not any(character in _GENERATION_ALNUM for character in value):
        return False
    return all(character in _GENERATION_ALLOWED for character in value)


def _field(raw: Any, name: str) -> Any:
    if isinstance(raw, Mapping):
        return raw.get(name) if name in raw else None
    return getattr(raw, name, None)


def _mapping_keys(raw: Any) -> tuple[bool, set[str]]:
    if isinstance(raw, Mapping):
        return True, {str(key) for key in raw}
    return False, set()


# Fields the runtime policy object/dict must carry. Unknown extra keys are
# rejected so a mis-wired provider cannot smuggle an unmodelled field past the
# automatic checker.
_REQUIRED_FIELDS = frozenset({
    "max_linear_m_s", "max_angular_rad_s", "generation", "received_mono", "robot_generation",
})
_OPTIONAL_FIELDS = frozenset()


def parse_policy(raw: Any, *, now: float | None = None) -> tuple[TeleopPolicy | None, str | None]:
    """Validate one trusted runtime policy value.

    Accepts a :class:`TeleopPolicy`, a mapping with exactly the documented keys,
    or an object with matching attributes. Returns ``(policy, None)`` on success
    or ``(None, refusal_reason)`` on any malformed, out-of-envelope or stale
    value. Presence here is never permission to move.
    """
    now = time.monotonic() if now is None else now

    if isinstance(raw, TeleopPolicy):
        return _validate_policy_fields(
            raw.max_linear_m_s, raw.max_angular_rad_s, raw.generation,
            raw.received_mono, raw.robot_generation, now=now)

    is_mapping, keys = _mapping_keys(raw)
    if is_mapping:
        unknown = keys - _REQUIRED_FIELDS - _OPTIONAL_FIELDS
        if unknown:
            return None, ("The dynamic speed policy is malformed: it carries unknown "
                          f"field(s) {sorted(unknown)}.")
        missing = _REQUIRED_FIELDS - keys
        if missing:
            return None, ("The dynamic speed policy is malformed: it is missing "
                          f"field(s) {sorted(missing)}.")
    elif raw is None:
        return None, "The dynamic speed policy is malformed."

    return _validate_policy_fields(
        _field(raw, "max_linear_m_s"), _field(raw, "max_angular_rad_s"),
        _field(raw, "generation"), _field(raw, "received_mono"),
        _field(raw, "robot_generation"), now=now)


def _validate_policy_fields(linear_raw, angular_raw, generation, received_mono,
                            robot_generation, *, now: float) -> tuple[TeleopPolicy | None, str | None]:
    linear = _finite_number(linear_raw)
    if linear is None:
        return None, "The dynamic speed policy linear cap is not a finite number."
    if linear < 0.0 or linear > HARD_MAX_LINEAR_M_S:
        return None, (f"The dynamic speed policy linear cap ({linear_raw!r}) is outside "
                      "the commissioned envelope.")
    angular = _finite_number(angular_raw)
    if angular is None:
        return None, "The dynamic speed policy angular cap is not a finite number."
    if angular < 0.0 or angular > HARD_MAX_ANGULAR_RAD_S:
        return None, (f"The dynamic speed policy angular cap ({angular_raw!r}) is outside "
                      "the commissioned envelope.")
    if not _valid_generation(generation):
        return None, "The dynamic speed policy configuration generation is missing or not canonical."
    received = _finite_number(received_mono)
    if received is None:
        return None, "The dynamic speed policy receipt time is missing or invalid."
    if received > now + POLICY_FUTURE_TOLERANCE_S:
        return None, "The dynamic speed policy receipt time is in the future."
    if now - received > POLICY_MAX_AGE_S:
        return None, "The dynamic speed policy is stale; manual input is refused."
    if (isinstance(robot_generation, bool) or not isinstance(robot_generation, int)
            or robot_generation < 0):
        return None, "The dynamic speed policy session binding is missing or invalid."
    return TeleopPolicy(
        mode=DYNAMIC_MODE,
        max_linear_m_s=linear,
        max_angular_rad_s=angular,
        generation=generation,
        received_mono=received,
        robot_generation=robot_generation,
    ), None


def raw_policy(*, state: Any = None, robot_id: str | None = None,
               provider: Callable | None = None) -> Any:
    """Fetch the untrusted raw policy from the trusted source, if any.

    An explicit ``provider`` (``hub.tuning.teleop_policy``) takes precedence and
    is authoritative: when it is wired, the server does not also consult the
    state attribute, so a stale state value can never shadow it. Otherwise the
    documented ``session.state.tuning_policy`` interface is used.
    """
    if provider is not None:
        return provider(robot_id)
    return getattr(state, "tuning_policy", None) if state is not None else None


def resolve_policy(
    *,
    capabilities: Any,
    state: Any = None,
    robot_id: str | None = None,
    provider: Callable | None = None,
    now: float | None = None,
    robot_generation: int | None = None,
) -> tuple[TeleopPolicy | None, str | None]:
    """Resolve the effective policy for a connection.

    Returns ``(policy, None)`` or ``(None, refusal_reason)``. A legacy robot
    resolves to :data:`LEGACY_POLICY` without consulting any policy source. A
    tuning-capable robot must supply a fresh, in-envelope, session-bound policy
    or manual input is refused.
    """
    now = time.monotonic() if now is None else now
    if not capability_requires_policy(capabilities):
        return LEGACY_POLICY, None

    # A mis-wired or failing provider is a refusal, never an exception that
    # escapes into the command path or a silent fall back to a higher default.
    try:
        raw = raw_policy(state=state, robot_id=robot_id, provider=provider)
        if raw is None:
            return None, ("The robot advertises dynamic speed tuning but no trusted policy is "
                          "available; manual input is refused.")
        policy, reason = parse_policy(raw, now=now)
    except Exception:
        return None, ("The dynamic speed policy could not be read; manual input is refused.")
    if policy is None:
        return None, (reason or "The dynamic speed policy is unavailable.")
    if policy.robot_generation != robot_generation:
        return None, ("The dynamic speed policy does not match this robot session; "
                      "manual input is refused.")
    return policy, None
