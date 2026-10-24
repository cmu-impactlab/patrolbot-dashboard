"""Native private producer service; isolated ROS only, no hardware/remote/key."""
import json
import os
import threading
import time
from types import SimpleNamespace as NS

import pytest

rclpy = pytest.importorskip('rclpy')
from patrolbot_interfaces.srv import TuningMaintenance
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import Bool

from patrolbot_web_bridge.teleop import RosTeleop

pytestmark = pytest.mark.skipif(
    os.environ.get('PATROLBOT_RUN_INTEGRATION') != '1'
    or int(os.environ.get('ROS_DOMAIN_ID', '0')) < 90,
    reason='requires isolated integration domain >=90')


@pytest.mark.parametrize('admission', ['acquire', 'block'])
def test_native_web_private_lease_never_borrows_normal_drive_readiness(admission):
    rclpy.init()
    node = Node('patrolbot_web_bridge')
    connected = threading.Event()
    connected.set()
    ws = NS(connected=connected, send=lambda *args: None)
    producer = RosTeleop(node, ws, state=lambda: (None, None, None, None),
                         context=lambda: (1, 'native-fixture'), policy_required=False)
    client_node = Node('web_tuning_native_client')
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    executor.add_node(client_node)
    thread = threading.Thread(target=executor.spin)
    thread.start()
    client = client_node.create_client(TuningMaintenance, '/patrolbot/web_tuning_maintenance')
    hold = client_node.create_publisher(Bool, '/patrolbot/tuning_hold', 10)
    owner = dict(job_id='11111111-1111-4111-8111-111111111111',
                 digest='sha256:' + 'a' * 64, plan_hash='sha256:' + 'b' * 64)

    def call(operation, **changes):
        future = client.call_async(TuningMaintenance.Request(operation=operation, **dict(owner, **changes)))
        done = threading.Event()
        future.add_done_callback(lambda _: done.set())
        assert done.wait(3.)
        return future.result()

    try:
        assert client.wait_for_service(timeout_sec=3.)
        assert producer.safety() is not None  # No odom, hardware, motor evidence.
        observed = json.loads(call('observe').evidence_json)
        assert observed['owner'] is None and not observed['input_blocked']
        if admission == 'block':
            assert not call('block').accepted
            deadline = time.monotonic() + 3.
            while hold.get_subscription_count() != 1 and time.monotonic() < deadline:
                time.sleep(.01)
            assert hold.get_subscription_count() == 1
            hold.publish(Bool(data=True))
            deadline = time.monotonic() + 3.
            while not json.loads(call('observe').evidence_json)['input_blocked'] and time.monotonic() < deadline:
                time.sleep(.01)
            assert node._tuning_input_blocked
        assert call(admission).accepted      # Local command admission is separate.
        assert not call('release', digest='sha256:' + 'c' * 64).accepted
        inspected = call('inspect')
        assert inspected.accepted
        data = json.loads(inspected.evidence_json)
        assert data['owner'] == owner and data['input_released'] is True
        assert data['input_blocked']
        assert data['session_active'] is False and data['buffers'] == [0., 0., False]
        assert call('release').accepted
        if admission == 'block':
            assert node._tuning_input_blocked  # releasing a peer cannot drop core hold
            hold.publish(Bool(data=False))
            deadline = time.monotonic() + 3.
            while json.loads(call('observe').evidence_json)['input_blocked'] and time.monotonic() < deadline:
                time.sleep(.01)
            assert not node._tuning_input_blocked
        assert not producer.session.active
        assert producer.safety() is not None  # Maintenance never weakens driving.
        assert call('release').accepted
        assert not call('acquire').accepted
    finally:
        executor.shutdown()
        thread.join(timeout=3.)
        client_node.destroy_node()
        node.destroy_node()
        rclpy.shutdown()
