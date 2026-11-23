"""Reset-only commissioning against production node methods, with no ROS/network."""
import ast
import __future__
from collections import deque
import json
import os
from pathlib import Path
import socket
import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock
from uuid import uuid4

import pytest

from patrolbot_web_bridge.commands import DISABLED_REASON
from patrolbot_web_bridge.system_reset import SystemResetClient
from patrolbot_web_bridge.throttle import Debounce, Throttle
from patrolbot_web_bridge.ws_client import WsClient


class FakeNode:
    def __init__(self, name):
        pass

    def declare_parameters(self, prefix, values):
        return [NS(name=name, value=value) for name, value in values]

    def __getattr__(self, name):
        if name in {'create_publisher', 'create_client'}:
            raise AssertionError('Reset-only must not construct hardware publishers/clients')
        if name in {"create_subscription", "create_timer", "get_clock", "get_logger"}:
            return Mock()
        raise AttributeError(name)


def node(monkeypatch, commands=None, reset=None, path='/unused'):
    for key, value in [('WEB_BRIDGE_ENABLE_COMMANDS', commands),
                       ('WEB_BRIDGE_ENABLE_RESET', reset), ('PATROLBOT_RESET_SOCKET', path)]:
        monkeypatch.delenv(key, raising=False)
        if value is not None:
            monkeypatch.setenv(key, value)
    monkeypatch.setattr(WsClient, 'start', lambda _: None)
    monkeypatch.setattr(SystemResetClient, 'start', lambda _: None)
    source = Path(__file__).resolve().parents[1] / 'patrolbot_web_bridge/bridge_node.py'
    klass = next(item for item in ast.parse(source.read_text()).body
                 if isinstance(item, ast.ClassDef) and item.name == 'WebBridgeNode')
    executor, teleop = Mock(), Mock()
    namespace = dict(Node=FakeNode, os=os, deque=deque, threading=threading, time=time,
                     WsClient=WsClient, SystemResetClient=SystemResetClient,
                     CommandExecutor=executor, RosTeleop=teleop, RosTuning=None,
                     DISABLED_REASON=DISABLED_REASON, TELEOP_CAPABILITY='teleop',
                     Throttle=Throttle, Debounce=Debounce, BaseState=None)
    for name in ['QoSProfile', 'ReliabilityPolicy', 'DurabilityPolicy',
                 'PoseWithCovarianceStamped', 'Odometry', 'LaserScan', 'Path',
                 'BatteryState', 'DiagnosticArray', 'qos_profile_sensor_data']:
        namespace[name] = Mock()
    exec(compile(ast.Module(body=[klass], type_ignores=[]), str(source), 'exec',
                 flags=__future__.annotations.compiler_flag), namespace)
    result = namespace['WebBridgeNode']()
    result.ws.connected.set()
    return result, executor, teleop


def frames(node):
    return [json.loads(frame) for frame in node.ws._queue]


@pytest.mark.parametrize('commands,reset,commissioned', [
    (None, None, False), ('0', '0', False), ('0', 'true', False),
    ('0', '1', True), ('1', None, True), ('1', '0', True), ('1', '1', True),
])
def test_opt_in_matrix_does_not_construct_motion_in_reset_only(monkeypatch, commands, reset, commissioned):
    n, executor, teleop = node(monkeypatch, commands, reset)
    assert (n._system_reset is not None) == commissioned
    assert executor.called == teleop.called == (commands == '1')
    if commands != '1':
        assert n._commands is None and n._teleop is None
    if n._system_reset:
        n._system_reset.available = True
    if commands == '1':
        executor.return_value.available_capabilities.return_value = ['undock']
    n._refresh_capabilities()
    assert ('software_reset' in n._announced_capabilities) == commissioned
    if commands != '1':
        assert n._announced_capabilities == (['software_reset'] if commissioned else [])


@pytest.mark.parametrize('reset', ['0', '1'])
def test_missing_socket_never_commissions(monkeypatch, reset):
    n, _, _ = node(monkeypatch, '0', reset, '')
    assert n._system_reset is None
    n._refresh_capabilities()
    assert n._announced_capabilities == []


@pytest.fixture
def supervisor(tmp_path):
    """Only a local Unix listener: no production adapter or robot operations."""
    path = str(tmp_path / 'reset.sock')
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    listener.listen()
    listener.settimeout(.05)
    state = {'ok': True, 'state': 'idle'}
    calls = []
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except socket.timeout:
                continue
            with conn:
                payload = json.loads(conn.makefile('rb').readline())
                calls.append(payload)
                if payload['op'] == 'request':
                    state.update(state='running', request_id=payload['request_id'])
                conn.sendall(json.dumps(state).encode() + b'\n')

    thread = threading.Thread(target=serve)
    thread.start()
    yield path, state, calls
    stop.set()
    thread.join(timeout=2)
    listener.close()
    assert not thread.is_alive()


@pytest.mark.parametrize('commands', ['0', '1'])
def test_reset_through_unix_supervisor_authorization_singleflight_and_reconnect(monkeypatch, supervisor, commands):
    path, state, calls = supervisor
    n, executor, _ = node(monkeypatch, commands, '1', path)
    executor.return_value.available_capabilities.return_value = []
    reset = n._system_reset
    reset.poll()
    n._refresh_capabilities()
    assert 'software_reset' in n._announced_capabilities
    def request(authorized):
        rid = str(uuid4())
        n._enqueue_command(dict(command='software_reset', command_id=rid, operator_authorized=authorized))
        n._drain_commands()
        return rid
    request(False)
    assert frames(n)[-1]['data']['accepted'] is False
    assert not any(c['op'] == 'request' for c in calls)
    rid = request(True)
    request(True)
    assert frames(n)[-1]['data']['accepted'] is False
    reset._submit(reset._requests.get_nowait())
    assert reset.busy and reset.commands_blocked
    state.update(state='uncertain', message='Outcome unknown; explicit retry required.')
    reset.poll()
    assert frames(n)[-1]['data']['outcome'] == 'timeout'
    assert reset.commands_blocked
    n.ws.stats['last_connect_mono'] = 99
    reset.poll()
    assert frames(n)[-1]['data']['outcome'] == 'timeout'
    assert len([c for c in calls if c['op'] == 'request']) == 1
    assert calls[-1] == {'op': 'current'}
    new = request(True)
    assert new != rid and reset._requests.get_nowait() == new
    executor.return_value.handle.assert_not_called()


@pytest.mark.parametrize('command', [
    'navigate_to_pose', 'set_initial_pose', 'undock', 'motor_enable', 'motor_disable',
    'charge_release', 'set_lidar_power', 'dock_reset', 'stop', 'cancel', 'cancel_map',
    'teleop_start', 'teleop_stop', 'anything_else',
])
def test_reset_only_rejects_every_other_command(monkeypatch, command):
    n, executor, teleop = node(monkeypatch, '0', '1')
    n._system_reset.busy = False
    n._enqueue_command(dict(command=command, command_id=str(uuid4()), operator_authorized=True))
    n._drain_commands()
    assert frames(n)[-1]['data']['accepted'] is False
    executor.assert_not_called()
    teleop.assert_not_called()
    assert n._system_reset._requests.empty()


def test_bad_socket_withdraws_capability_and_rejects_reset(monkeypatch, tmp_path):
    n, _, _ = node(monkeypatch, '0', '1', str(tmp_path / 'missing.sock'))
    n._system_reset.available = True
    n._system_reset.poll()
    n._refresh_capabilities()
    assert n._announced_capabilities == []
    n._enqueue_command(dict(command='software_reset', command_id=str(uuid4()), operator_authorized=True))
    n._drain_commands()
    assert frames(n)[-1]['data']['accepted'] is False
    assert n._system_reset._requests.empty()


def test_reset_only_never_replays_previous_connection_input(monkeypatch):
    n, _, _ = node(monkeypatch, '0', '1')
    n._system_reset.available = True
    n._system_reset.busy = False
    n._enqueue_command(dict(command='software_reset', command_id=str(uuid4()), operator_authorized=True))
    n.ws.connection_generation += 1
    n._drain_commands()
    assert frames(n)[-1]['data']['accepted'] is False
    assert n._system_reset._requests.empty()


@pytest.mark.parametrize('reply', [
    {'ok': False, 'message': 'Not commissioned'},
    {'ok': True, 'state': 'invalid'},
])
def test_reachable_but_unusable_supervisor_has_no_capability(monkeypatch, supervisor, reply):
    path, state, calls = supervisor
    state.clear()
    state.update(reply)
    n, _, _ = node(monkeypatch, '0', '1', path)
    n._system_reset.poll()
    n._refresh_capabilities()
    assert n._announced_capabilities == []
    assert calls == [{'op': 'current'}]


@pytest.mark.parametrize('identifier', ['', 'invalid-uuid'])
def test_reset_only_keeps_uuid_validation(monkeypatch, identifier):
    n, _, _ = node(monkeypatch, '0', '1')
    n._system_reset.available = True
    n._system_reset.busy = False
    n._enqueue_command(dict(command='software_reset', command_id=identifier, operator_authorized=True))
    n._drain_commands()
    assert frames(n)[-1]['data']['accepted'] is False
    assert n._system_reset._requests.empty()


def test_reset_only_drops_raw_manual_frames_without_a_producer(monkeypatch):
    n, executor, teleop = node(monkeypatch, '0', '1')
    sample = ({'linear': .1, 'angular': .2, 'deadman': True}, time.monotonic(), n.ws.connection_generation)
    n._latest_teleop_frame = sample
    n._teleop_release_frame = sample
    n._drain_commands()
    assert n._latest_teleop_frame is None and n._teleop_release_frame is None
    executor.assert_not_called()
    teleop.assert_not_called()
    assert n._system_reset._requests.empty()


def test_native_reset_only_node_with_fake_unix_supervisor(monkeypatch, supervisor):
    """Opt in to native ROS only in a network-isolated test container."""
    if os.environ.get('PATROLBOT_TEST_NATIVE_RESET') != '1':
        pytest.skip('native reset isolation test is not enabled')

    import rclpy
    from patrolbot_interfaces.msg import BaseState
    from patrolbot_web_bridge import bridge_node

    path, state, calls = supervisor
    monkeypatch.setenv('WEB_BRIDGE_ENABLE_COMMANDS', '0')
    monkeypatch.setenv('WEB_BRIDGE_ENABLE_RESET', '1')
    monkeypatch.setenv('PATROLBOT_RESET_SOCKET', path)
    monkeypatch.setattr(WsClient, 'start', lambda _: None)
    monkeypatch.setattr(SystemResetClient, 'start', lambda _: None)
    # Independent tuning capabilities should not affect the reset proof.
    monkeypatch.setattr(bridge_node, 'RosTuning', None)

    rclpy.init(args=[])
    n = None
    try:
        n = bridge_node.WebBridgeNode()
        assert bridge_node.BaseState is BaseState
        assert n._commands is None and n._teleop is None
        assert {publisher.topic_name for publisher in n.publishers} <= {
            '/rosout', '/parameter_events'}
        assert not list(n.clients)
        n.ws.connected.set()
        n._system_reset.poll()
        n._refresh_capabilities()
        assert n._announced_capabilities == ['software_reset']
        reset_id = str(uuid4())
        n._enqueue_command(dict(command='software_reset', command_id=reset_id,
                                operator_authorized=True))
        n._drain_commands()
        n._system_reset._submit(n._system_reset._requests.get_nowait())
        assert state['request_id'] == reset_id
        assert calls == [{'op': 'current'}, {'op': 'request', 'request_id': reset_id,
                                             'operation': 'software_reset'}]
    finally:
        if n is not None:
            n.destroy_node()
        rclpy.try_shutdown()
