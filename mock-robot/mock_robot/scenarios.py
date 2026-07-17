"""Scripted scenario timeline for the mock robot.

Cycle length is 360 s. Within each cycle (scenario `full`, the default):
- continuous waypoint patrol, battery drains; at the low threshold the robot
  drives to the dock, charges, then resumes;
- t≈240 s: WebSocket disconnect drill (socket closed for 15 s — the dashboard
  must show Stale → Offline → recovery);
- t≈300 s: map change (a wall appears, map_version bumps, map is re-sent).

`calm` disables the drills; `chaos` adds e-stop pulses and bumper hits.
"""
from __future__ import annotations

from dataclasses import dataclass

CYCLE = 360.0
DISCONNECT_AT = 240.0
DISCONNECT_FOR = 15.0
MAP_CHANGE_AT = 300.0


@dataclass
class Scenario:
    name: str = "full"

    def in_disconnect_window(self, elapsed: float) -> bool:
        if self.name == "calm":
            return False
        phase = elapsed % CYCLE
        return DISCONNECT_AT <= phase < DISCONNECT_AT + DISCONNECT_FOR

    def wants_extra_wall(self, elapsed: float) -> bool:
        """Wall present during the second half of each cycle after MAP_CHANGE_AT."""
        if self.name == "calm":
            return False
        return (elapsed % CYCLE) >= MAP_CHANGE_AT

    def estop_active(self, elapsed: float) -> bool:
        if self.name != "chaos":
            return False
        phase = elapsed % 90.0
        return 45.0 <= phase < 50.0

    def bumper_active(self, elapsed: float) -> tuple[bool, bool]:
        """(front, rear) — brief hits in full/chaos, like the POC's 24 s cycle."""
        if self.name == "calm":
            return (False, False)
        phase = elapsed % 24.0
        if 10.0 <= phase < 12.0:
            front = int(elapsed // 24.0) % 2 == 0
            return (front, not front)
        return (False, False)
