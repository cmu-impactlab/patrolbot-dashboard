"""Use the actual bridge queue boundary: discard, never defer/replay input."""

import ast
from collections import deque
from pathlib import Path
from types import MethodType, SimpleNamespace as NS

import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'patrolbot_web_bridge/bridge_node.py'


def node():
    tree = ast.parse(SOURCE.read_text())
    klass = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == 'WebBridgeNode')
    names = ('_maintenance_reject_command', '_enqueue_command')
    definitions = [item for item in klass.body if isinstance(item, ast.FunctionDef) and item.name in names]
    namespace = {'time': NS(monotonic=lambda: 10.)}
    exec(compile(ast.fix_missing_locations(ast.Module(body=definitions, type_ignores=[])), str(SOURCE), 'exec'), namespace)
    replies = []
    result = NS(_command_queue=deque(), ws=NS(connection_generation=1, send=lambda *args: replies.append(args)),
                _tuning_input_blocked=False, _tuning_input_after=0.)
    for name in names:
        setattr(result, name, MethodType(namespace[name], result))
    return result, replies


@pytest.mark.parametrize('command', ['navigate_to_pose', 'teleop_start', 'undock', 'set_initial_pose', 'motor_enable', 'software_reset'])
def test_blocked_commands_are_refused_at_ingress_not_retained_for_later(command):
    bridge, replies = node()
    bridge._tuning_input_blocked = True
    bridge._enqueue_command({'command': command, 'command_id': 'fixture'})
    assert not bridge._command_queue and replies[-1][1]['accepted'] is False
    bridge._tuning_input_blocked = False
    assert not bridge._command_queue


@pytest.mark.parametrize('command', ['stop', 'cancel', 'cancel_map', 'motor_disable', 'teleop_stop'])
def test_stop_disable_and_release_are_not_suppressed_by_maintenance(command):
    bridge, replies = node()
    bridge._tuning_input_blocked = True
    bridge._enqueue_command({'command': command, 'command_id': 'fixture'})
    assert len(bridge._command_queue) == 1 and not replies


def test_a_queued_command_cannot_cross_a_maintenance_release_boundary():
    bridge, replies = node()
    bridge._enqueue_command({'command': 'navigate_to_pose', 'command_id': 'fixture'})
    data = bridge._command_queue.popleft()
    bridge._tuning_input_after = 12.
    assert bridge._maintenance_reject_command(data, data['_input_received_at'])
    assert replies[-1][1]['accepted'] is False
    assert not bridge._maintenance_reject_command(data, 13.)
