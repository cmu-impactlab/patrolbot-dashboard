"""Single-operator command lease: one operator in control per robot.

A second operator may observe telemetry freely but cannot send, stop, resume,
or cancel commands while another operator holds the lease — doing so requires
an explicit takeover, which is recorded. The lease is keyed by user id, so the
same operator's second tab is not blocked; a reconnecting operator reclaims a
lease they still hold (their client reference having gone away) without a
takeover.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass
class LeaseHolder:
    user_id: int
    username: str
    client: Any  # BrowserClient; identity-compared, never introspected here
    acquired_at: float


class OperatorLease:
    def __init__(self) -> None:
        self._holders: dict[str, LeaseHolder] = {}

    def holder(self, robot_id: str) -> LeaseHolder | None:
        return self._holders.get(robot_id)

    def can_command(self, robot_id: str, user_id: int) -> bool:
        """True when the robot is uncontested or already held by this user."""
        holder = self._holders.get(robot_id)
        return holder is None or holder.user_id == user_id

    def acquire(self, robot_id: str, user_id: int, username: str, client: Any) -> None:
        self._holders[robot_id] = LeaseHolder(user_id, username, client, time.time())

    def release_client(self, client: Any) -> None:
        """Free any lease held by a client that has disconnected."""
        for robot_id, holder in list(self._holders.items()):
            if holder.client is client:
                del self._holders[robot_id]


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    """Per-key token bucket. `per_minute` tokens refill over 60s; each allowed
    event consumes one. Used to cap command frames per operator."""

    def __init__(self, per_minute: int) -> None:
        self.capacity = float(max(1, per_minute))
        self.refill_per_s = self.capacity / 60.0
        self._buckets: dict[Any, _Bucket] = {}

    def allow(self, key: Any) -> bool:
        now = time.monotonic()
        bucket = self._buckets.get(key)
        if bucket is None:
            self._buckets[key] = _Bucket(self.capacity - 1.0, now)
            return True
        bucket.tokens = min(self.capacity,
                            bucket.tokens + (now - bucket.updated) * self.refill_per_s)
        bucket.updated = now
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return True
        return False
