"""Minimum-interval gates so high-rate topics don't flood the WebSocket."""
from __future__ import annotations

import time

from typing import Any, Callable


class Debounce:
    """Hold a flapping value at its last stable reading.

    A charger latching and re-latching against marginal dock contacts made
    charge_state (and the battery charging flag derived from it) invert every
    few seconds. Everything downstream treated each frame as ground truth, so
    one afternoon produced 27 "Charging started"/26 "Charging stopped" event
    pairs, a dock button that swapped between Dock and Undock while the
    operator was reaching for it, and command gates that allowed and refused
    the same command alternately.

    A new value has to hold for `settle_s` before it is published. Anything
    that flips faster than that is chatter and is suppressed — the last stable
    value stands. The first value ever seen is accepted immediately, so a
    fresh bridge reports real state without waiting out the settle window.
    """

    def __init__(self, settle_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.settle_s = settle_s
        self._clock = clock  # injectable so the tests need not sleep
        self._stable: Any = None
        self._candidate: Any = None
        self._candidate_since = 0.0
        self._seeded = False

    def update(self, value: Any) -> Any:
        """Feed the latest raw reading; returns the value to act on."""
        now = self._clock()
        if not self._seeded:
            self._seeded = True
            self._stable = value
            self._candidate = value
            self._candidate_since = now
            return self._stable
        if value == self._stable:
            # Back to the stable value before the candidate settled: the
            # excursion was chatter, so forget it.
            self._candidate = value
            self._candidate_since = now
            return self._stable
        if value != self._candidate:
            self._candidate = value
            self._candidate_since = now
            return self._stable
        if now - self._candidate_since >= self.settle_s:
            self._stable = value
        return self._stable


class Throttle:
    def __init__(self, min_interval_s: float) -> None:
        self.min_interval = min_interval_s
        self._last = 0.0

    def ready(self) -> bool:
        now = time.monotonic()
        if now - self._last >= self.min_interval:
            self._last = now
            return True
        return False
