"""Bridge telemetry wiring: no ROS graph, robot connection, or command path."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

pytest.importorskip("rclpy")
from geometry_msgs.msg import PoseWithCovarianceStamped
from patrolbot_web_bridge.bridge_node import WebBridgeNode


def test_republished_pose_keeps_amcl_refresh_age_and_localized_verdict():
    amcl = PoseWithCovarianceStamped()
    amcl.pose.pose.orientation.w = 1.0
    for index in (0, 7, 35):
        amcl.pose.covariance[index] = 0.01
    clock = NS(nanoseconds=101_000_000_000)
    node = NS(_pose_throttle=NS(ready=lambda: True), _latest_amcl=amcl,
              _latest_odom=None, _latest_amcl_at=100.0,
              cfg={"covariance_warn_threshold": 0.5, "localization_max_age_s": 30},
              get_clock=lambda: NS(now=lambda: clock), _on_active_map=lambda _: True,
              _map_context=lambda: {"map_id": "test", "map_revision": "revision"},
              _localization_is_usable=lambda: False, ws=NS(send=Mock()))
    WebBridgeNode._maybe_send_pose(node)
    first = node.ws.send.call_args.args[1]
    assert first["localization_age_s"] == 1.0
    assert first["localization_max_age_s"] == 30.0
    assert first["covariance_limit"] == 0.5
    assert first["localized"] is False
    clock.nanoseconds = 132_000_000_000
    WebBridgeNode._maybe_send_pose(node)
    second = node.ws.send.call_args.args[1]
    assert second["localization_age_s"] == 32.0
    assert second["planar_covariance"] == first["planar_covariance"]
    assert second["localized"] is False


@pytest.mark.parametrize('verified,usable,age,active,expected', [
    (False, True, 31., True, False), (True, True, 31., True, True),
    (True, True, 29., True, False), (True, False, 31., True, False),
    (True, True, 31., False, False),
])
def test_age_only_candidate_requires_verified_native_and_all_non_age_guards(verified, usable, age, active, expected):
    node = NS(cfg={'navigation_stationary_refresh_verified':verified, 'localization_max_age_s':30.},
              _localization_is_usable=lambda ignore_age:usable,
              _latest_amcl_at=0., _latest_amcl=NS(header=NS(frame_id='map')),
              get_clock=lambda:NS(now=lambda:NS(nanoseconds=int(age*1e9))),
              _on_active_map=lambda _:active)
    assert bool(WebBridgeNode._age_only_refresh_eligible(node)) is expected


@pytest.mark.parametrize('covariance,stamp,base_change,expected', [
    (.01, 2_000_000_000, {}, True), (.26, 2_000_000_000, {}, False),
    (0., 2_000_000_000, {}, False), (float('nan'), 2_000_000_000, {}, False),
    (.01, 1_000_000_000, {}, False), (.01, 2_000_000_000, {'odom_epoch_valid':False}, False),
    (.01, 2_000_000_000, {'localization_recovery_required':True}, False),
    (.01, 2_000_000_000, {'map_activated_stamp_ns':3_000_000_000}, False),
])
def test_real_age_only_candidate_keeps_covariance_source_seed_map_and_epoch(covariance, stamp, base_change, expected):
    amcl = PoseWithCovarianceStamped()
    amcl.header.frame_id = 'map'; amcl.header.stamp.sec = stamp // 1_000_000_000
    amcl.pose.pose.orientation.w = 1.
    for index in (0,7,35): amcl.pose.covariance[index] = covariance
    base = dict(map_id='floor1',map_revision='revision',map_activated_stamp_ns=1_000_000_000,
                link_connected=True,telemetry_age=.1,odom_epoch_valid=True,
                localization_recovery_required=False,localization_seed_stamp_ns=1_000_000_000)
    base.update(base_change)
    node = NS(cfg={'navigation_stationary_refresh_verified':True,'localization_max_age_s':30.,'covariance_warn_threshold':.25},
              _latest_amcl=amcl,_latest_amcl_at=0.,_latest_base_state=base,_latest_base_state_at=31.,
              get_clock=lambda:NS(now=lambda:NS(nanoseconds=31_000_000_000)))
    node._localization_is_usable = lambda ignore_age=False:WebBridgeNode._localization_is_usable(node,ignore_age)
    node._on_active_map = lambda msg:WebBridgeNode._on_active_map(node,msg)
    assert node._localization_is_usable() is False
    assert bool(WebBridgeNode._age_only_refresh_eligible(node)) is expected
