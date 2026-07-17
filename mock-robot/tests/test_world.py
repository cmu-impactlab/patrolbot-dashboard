import math

from mock_robot.battery import Battery
from mock_robot.scenarios import Scenario
from mock_robot.world import DOCK, RESOLUTION, Robot, World, build_cells


def test_map_is_deterministic():
    assert build_cells() == build_cells()
    assert build_cells(extra_wall=True) != build_cells(extra_wall=False)


def test_map_border_occupied_center_free():
    world = World()
    assert world.occupied(-8.9, -8.9)  # border wall
    assert not world.occupied(4.0, -4.0)  # open floor


def test_ray_cast_hits_border():
    world = World()
    # From the center pointing +x: wall at ~8.7 m is beyond lidar range (8 m).
    assert world.ray_cast(0.0, 0.0, 0.0) is None
    # From near the east wall pointing +x: short return.
    distance = world.ray_cast(7.0, -5.0, 0.0)
    assert distance is not None and distance < 2.5


def test_scan_shape():
    world = World()
    ranges = world.scan(4.0, -4.0, math.pi / 2)
    assert len(ranges) == 181
    assert any(r is not None for r in ranges)


def test_robot_reaches_waypoint():
    robot = Robot(x=0.0, y=0.0, yaw=0.0)
    robot.set_goal((1.0, 0.0))
    arrived = False
    for _ in range(600):
        if robot.step(0.1):
            arrived = True
            break
    assert arrived
    assert abs(robot.x - 1.0) < 0.3


def test_battery_cycle_turnaround():
    battery = Battery(level=0.21, drain_per_s=0.01)
    for _ in range(30):
        battery.step(1.0)
        if battery.needs_charge:
            battery.charging = True
    assert battery.charging
    for _ in range(200):
        battery.step(1.0, discharging_allowed=False)
        if not battery.charging:
            break
    assert not battery.charging  # reached full and stopped
    assert battery.level >= 0.95


def test_scenario_windows():
    scenario = Scenario("full")
    assert scenario.in_disconnect_window(245.0)
    assert not scenario.in_disconnect_window(100.0)
    assert scenario.wants_extra_wall(310.0)
    assert not scenario.wants_extra_wall(100.0)
    calm = Scenario("calm")
    assert not calm.in_disconnect_window(245.0)
    assert not calm.wants_extra_wall(310.0)


def test_dock_is_reachable_free_space():
    world = World()
    assert not world.occupied(*DOCK)
    assert RESOLUTION > 0
