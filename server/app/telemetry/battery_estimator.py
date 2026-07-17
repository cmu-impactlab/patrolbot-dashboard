"""Battery runtime estimation.

Ported from the custom-dashboard-demo POC. Two modes:

- percentage mode: bounded linear regression over state-of-charge samples.
- voltage mode: same regression over pack voltage toward a cutoff voltage.
  The PatrolBot's lead-acid pack reports no reliable SOC, so voltage trend is
  the primary signal on real hardware. Results are always presented to users
  as "Estimated".
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque


@dataclass(frozen=True)
class BatteryEstimate:
    state: str  # charging | calculating | stable | estimated | threshold_reached | unavailable
    minutes_remaining: int | None
    confidence: str  # low | medium | high

    def as_dict(self) -> dict:
        return {"state": self.state, "minutes_remaining": self.minutes_remaining, "confidence": self.confidence}


class _TrendEstimator:
    """Linear regression of a declining quantity toward a lower threshold."""

    def __init__(self, lower_threshold: float, window_seconds: float = 300.0,
                 min_samples: int = 4, max_minutes: int = 24 * 60) -> None:
        self.lower_threshold = lower_threshold
        self.window_seconds = window_seconds
        self.min_samples = min_samples
        self.max_minutes = max_minutes
        self._samples: Deque[tuple[float, float]] = deque(maxlen=600)

    def add_sample(self, timestamp: float, value: float) -> None:
        if self._samples and timestamp <= self._samples[-1][0]:
            return
        self._samples.append((timestamp, value))
        cutoff = timestamp - self.window_seconds
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def reset(self) -> None:
        self._samples.clear()

    def estimate(self, *, charging: bool) -> BatteryEstimate:
        if charging:
            return BatteryEstimate("charging", None, "low")
        if len(self._samples) < self.min_samples:
            return BatteryEstimate("calculating", None, "low")
        span = self._samples[-1][0] - self._samples[0][0]
        if span < 3.0:
            return BatteryEstimate("calculating", None, "low")

        origin = self._samples[0][0]
        xs = [stamp - origin for stamp, _ in self._samples]
        ys = [level for _, level in self._samples]
        mean_x = sum(xs) / len(xs)
        mean_y = sum(ys) / len(ys)
        denominator = sum((x - mean_x) ** 2 for x in xs)
        if denominator <= 0.0:
            return BatteryEstimate("unavailable", None, "low")
        slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator
        if slope >= -1e-9:
            return BatteryEstimate("stable", None, "low")

        remaining = ys[-1] - self.lower_threshold
        if remaining <= 0.0:
            return BatteryEstimate("threshold_reached", 0, "high")
        minutes = remaining / -slope / 60.0
        if minutes <= 0.0 or minutes > self.max_minutes:
            return BatteryEstimate("unavailable", None, "low")

        if span >= 60.0 and len(xs) >= 30:
            confidence = "high"
        elif span >= 15.0 and len(xs) >= 10:
            confidence = "medium"
        else:
            confidence = "low"
        return BatteryEstimate("estimated", max(1, round(minutes)), confidence)


class BatteryRuntimeEstimator:
    """Prefers percentage trend when SOC is reported, falls back to voltage trend."""

    def __init__(self, low_percent: float = 20.0, cutoff_voltage: float = 22.0) -> None:
        self._pct = _TrendEstimator(lower_threshold=low_percent / 100.0)
        self._volt = _TrendEstimator(lower_threshold=cutoff_voltage)
        self._was_charging = False

    def add_sample(self, timestamp: float, *, voltage: float,
                   percentage: float | None, charging: bool) -> None:
        if charging and not self._was_charging:
            # Old discharge trend is meaningless after a charge session starts.
            self._pct.reset()
            self._volt.reset()
        self._was_charging = charging
        if not charging:
            if percentage is not None:
                self._pct.add_sample(timestamp, min(1.0, max(0.0, percentage / 100.0)))
            self._volt.add_sample(timestamp, voltage)

    def estimate(self, *, charging: bool, has_percentage: bool) -> BatteryEstimate:
        if charging:
            return BatteryEstimate("charging", None, "low")
        if has_percentage:
            result = self._pct.estimate(charging=False)
            if result.state == "estimated":
                return result
        return self._volt.estimate(charging=False)
