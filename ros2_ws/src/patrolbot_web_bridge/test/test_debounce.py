"""Charge-signal debounce — no ROS graph required.

Reproduces the 2026-07-28 flap: charge_state inverted with a hard ~3.1 s off /
~8-11 s on period for seven minutes, producing 27 "Charging started" and 26
"Charging stopped" events, a dock button that swapped meaning under the
operator's cursor, and command gates that alternately allowed and refused.
"""
from patrolbot_web_bridge.throttle import Debounce


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def advance(self, seconds):
        self.now += seconds


def make(settle_s, clock):
    return Debounce(settle_s, clock=lambda: clock.now)


def test_first_reading_is_accepted_immediately():
    clock = FakeClock()
    debounce = make(4.0, clock)
    assert debounce.update("charging") == "charging"


def test_flap_shorter_than_settle_is_suppressed():
    clock = FakeClock()
    debounce = make(4.0, clock)
    assert debounce.update("charging") == "charging"
    # The observed cycle: 3.1 s released, ~10 s latched, repeatedly.
    for _ in range(10):
        for _ in range(14):  # 3.1 s of "not_charging" at ~4.5 Hz
            clock.advance(0.22)
            assert debounce.update("not_charging") == "charging"
        for _ in range(45):  # ~10 s back on charge
            clock.advance(0.22)
            assert debounce.update("charging") == "charging"


def test_real_transition_settles_through():
    clock = FakeClock()
    debounce = make(4.0, clock)
    debounce.update("charging")
    # settle_s runs from when the new value is first seen, not from the last
    # transition — so this is 3.0 s into the 4.0 s window, not past it.
    clock.advance(3.0)
    assert debounce.update("not_charging") == "charging"
    clock.advance(3.0)
    assert debounce.update("not_charging") == "charging"  # 3.0 s of 4.0
    clock.advance(1.5)
    assert debounce.update("not_charging") == "not_charging"


def test_candidate_restarts_when_the_value_changes_again():
    clock = FakeClock()
    debounce = make(4.0, clock)
    debounce.update("charging")
    clock.advance(3.5)
    assert debounce.update("not_charging") == "charging"
    clock.advance(3.5)
    assert debounce.update("unknown") == "charging"  # new candidate, clock reset
    clock.advance(4.5)
    assert debounce.update("unknown") == "unknown"


def test_booleans_work_the_same_way():
    clock = FakeClock()
    debounce = make(4.0, clock)
    assert debounce.update(True) is True
    clock.advance(1.0)
    assert debounce.update(False) is True
    clock.advance(5.0)
    assert debounce.update(False) is False
