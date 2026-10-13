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
