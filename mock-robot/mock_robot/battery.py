"""Battery drain/charge cycle, ported from the POC simulator.

Voltage curve approximates the PatrolBot's 24 V nominal lead-acid pack
(manual: 1–3 h runtime, ~3.2 h charge time — sped up for demo purposes).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Battery:
    level: float = 0.92  # 0..1
    charging: bool = False
    drain_per_s: float = 0.0015
    charge_per_s: float = 0.012
    low_threshold: float = 0.20
    full_threshold: float = 0.96

    def step(self, dt: float, *, discharging_allowed: bool = True) -> None:
        if self.charging:
            self.level = min(self.full_threshold, self.level + self.charge_per_s * dt)
            if self.level >= self.full_threshold:
                self.charging = False
        elif discharging_allowed:
            self.level = max(0.0, self.level - self.drain_per_s * dt)

    @property
    def needs_charge(self) -> bool:
        return not self.charging and self.level <= self.low_threshold

    @property
    def voltage(self) -> float:
        return round(22.8 + 5.2 * self.level + (0.6 if self.charging else 0.0), 2)

    @property
    def percentage(self) -> float:
        return round(self.level * 100.0, 1)

    @property
    def current(self) -> float:
        return 2.4 if self.charging else -1.5

    def payload(self) -> dict:
        return {
            "voltage": self.voltage,
            "current": self.current,
            "percentage": self.percentage,
            "charging": self.charging,
        }
