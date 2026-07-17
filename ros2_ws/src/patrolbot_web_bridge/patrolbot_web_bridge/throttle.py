"""Minimum-interval gates so high-rate topics don't flood the WebSocket."""
from __future__ import annotations

import time


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
