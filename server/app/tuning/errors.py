"""Typed failures for the robot-tuning broker/API.

Every failure that the HTTP layer maps to a status code is one of these, so the
API never has to guess whether a message came from validation, authorization,
the broker's admission bounds or a lost robot session.
"""
from __future__ import annotations


class TuningError(Exception):
    """Base class for a tuning refusal. ``status_code`` is the HTTP mapping."""

    status_code = 503
    code = "tuning_error"

    def __init__(self, detail: str, *, code: str | None = None,
                 status_code: int | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code


class TuningInvalid(TuningError):
    """The canonical request failed strict validation (no robot traffic sent)."""

    status_code = 400
    code = "invalid_request"


class TuningForbidden(TuningError):
    """Caller is not allowed to perform this operation."""

    status_code = 403
    code = "forbidden"


class TuningConflict(TuningError):
    """A stale revision/session/duplicate idempotency key was supplied."""

    status_code = 409
    code = "conflict"


class TuningRateLimited(TuningError):
    """Bounded pending concurrency is saturated; try again shortly."""

    status_code = 429
    code = "busy"


class TuningUnavailable(TuningError):
    """No usable tuning path: robot offline, capability unadvertised, core down."""

    status_code = 503
    code = "unavailable"


class TuningNotFound(TuningError):
    """A durable job id was not found on the robot."""

    status_code = 404
    code = "not_found"


class TuningRegistryError(TuningUnavailable):
    """The reviewed catalog/registry could not be loaded; fail closed."""

    code = "registry_unavailable"
