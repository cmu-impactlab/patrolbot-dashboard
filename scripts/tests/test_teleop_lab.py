"""Portable harness checks: no ROS imports, network, Docker or hardware."""
import io
import ast
import asyncio
import gc
import hashlib
import inspect
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import teleop_lab as lab


def executor_fixture(**kwargs):
    return NS(_tasks_lock=threading.Lock(), _pending_tasks={}, _ready_tasks=deque(),
              wake=Mock(), shutdown=Mock(return_value=True), **kwargs)


@pytest.mark.parametrize('ending', ['return', 'error', 'cancel'])
def test_completion_tracks_detached_revoke_through_stop_creation(ending):
    async def check():
        entered, release = asyncio.Event(), asyncio.Event()
        broker = NS(active={}, _admission_locks={}, teleop=NS(owners={lab.ROBOT_ID: object()}))

        async def revoke(robot_id, reason, *, issue_stop=True):
            assert robot_id == lab.ROBOT_ID and reason == 'fault' and issue_stop
            entered.set()
            await release.wait()
            if ending == 'error':
                raise ValueError('audit failure')
            broker.active['stop'] = NS(robot_id=robot_id, command_id='stop')
            return 'unchanged'

        broker.teleop.revoke = revoke
        snapshot = lab.observe_broker_completion(broker)
        task = asyncio.create_task(broker.teleop.revoke(lab.ROBOT_ID, 'fault', issue_stop=True))
        await entered.wait()
        broker.teleop.owners.clear()
        assert snapshot() == dict(active_ids=[], owner_present=False,
                                  admission_locked=False, revokes_pending=1)
        if ending == 'cancel':
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            release.set()
            if ending == 'error':
                with pytest.raises(ValueError, match='audit failure'):
                    await task
            else:
                assert await task == 'unchanged'
                assert snapshot()['active_ids'] == ['stop']
        assert snapshot()['revokes_pending'] == 0
        lock = asyncio.Lock()
        broker._admission_locks[lab.ROBOT_ID] = lock
        async with lock:
            assert snapshot()['admission_locked'] is True
        assert snapshot()['admission_locked'] is False
    asyncio.run(check())


def test_production_revoke_can_create_stop_after_terminal_detaches_owner():
    import uuid
    # Execute the production methods without importing optional server dependencies.
    path = Path(__file__).resolve().parents[2] / 'server/app/commands/teleop.py'
    controller_class = next(node for node in ast.parse(path.read_text()).body
                            if isinstance(node, ast.ClassDef) and node.name == 'TeleopController')
    controller_class.body = [node for node in controller_class.body
                             if getattr(node, 'name', None) in {'revoke', 'terminal'}]
    namespace = dict(uuid=uuid, TeleopVelocityData=NS, CommandRequestData=NS,
                     Envelope=NS(model_validate_json=lambda value: value), encode=lambda *args: args)
    exec(compile(ast.Module(body=[controller_class], type_ignores=[]), str(path), 'exec'), namespace)

    async def check():
        sending, sent, auditing, audited = (asyncio.Event() for _ in range(4))
        broker = NS(hub=NS(), active={}, _admission_locks={}, can_command=lambda _: True)
        broker.teleop = controller = namespace['TeleopController']()
        controller.broker, controller.owners, controller.revisions = broker, {}, {}
        owner = NS(phase='active', client=object(), command_id='start', seq=1,
                   stop_issued=False, start_terminal=False, stop_ids=set())
        controller.owners[lab.ROBOT_ID] = owner
        controller.bound = lambda _: True

        async def send(*_):
            sending.set()
            await sent.wait()
            return True

        async def request(client, envelope, payload):
            assert client is owner.client and payload.command == 'teleop_stop'
            auditing.set()
            await audited.wait()
            broker.active[payload.command_id] = NS(robot_id=lab.ROBOT_ID,
                                                    command_id=payload.command_id)

        controller.send = send
        broker.handle_browser_request = request
        snapshot = lab.observe_broker_completion(broker)
        task = asyncio.create_task(controller.revoke(lab.ROBOT_ID, 'fault'))
        await sending.wait()
        controller.terminal(NS(robot_id=lab.ROBOT_ID, command_id='start'))
        assert not snapshot()['owner_present'] and snapshot()['revokes_pending'] == 1
        assert snapshot()['active_ids'] == []
        sent.set()
        await auditing.wait()
        assert snapshot()['revokes_pending'] == 1 and snapshot()['active_ids'] == []
        audited.set()
        await task
        assert snapshot()['revokes_pending'] == 0 and len(snapshot()['active_ids']) == 1
    asyncio.run(check())


def lab_run_statements():
    return next(item for item in ast.parse(inspect.getsource(lab)).body
                if isinstance(item, ast.FunctionDef) and item.name == 'run_lab').body


def test_init_preserves_main_stop_handler_until_explicit_cleanup(monkeypatch):
    statements = lab_run_statements()
    signals_import = next(item for item in statements
                          if isinstance(item, ast.ImportFrom) and item.module == 'rclpy.signals')
    init = next(item for item in statements if isinstance(item, ast.Expr)
                and isinstance(item.value, ast.Call)
                and ast.unparse(item.value.func) == 'rclpy.init')
    no_signals = object()
    monkeypatch.setitem(sys.modules, 'rclpy.signals', NS(SignalHandlerOptions=NS(NO=no_signals)))
    context = {'valid': False}
    def run(resources):
        handler = signal.getsignal(signal.SIGTERM)
        def initialize(**kwargs):
            assert kwargs['signal_handler_options'] is no_signals
            context['valid'] = True
        namespace = dict(rclpy=NS(init=initialize), ROBOT_ID=lab.ROBOT_ID)
        exec(compile(ast.Module(body=[signals_import, init], type_ignores=[]),
                     lab.__file__, 'exec'), namespace)
        assert signal.getsignal(signal.SIGTERM) is handler
        handler(signal.SIGTERM, None)
        assert resources.stopped.is_set() and context['valid']
        def destroy():
            assert context['valid'] and resources.dispatch_stopped.is_set()
        resources.nodes.append(('receiver', NS(destroy_node=destroy)))
        resources.context_shutdown = lambda: context.update(valid=False)
    monkeypatch.setattr(lab, 'run_lab', run)
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        lab.main()
        assert not context['valid']
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


@pytest.mark.parametrize('queue_source', ['portable', 'cached-jazzy'])
def test_readiness_timeout_keeps_one_uncanceled_observation(queue_source):
    executor = executor_fixture()
    def create_task(callback):
        task = Mock(callback=callback)
        task.done.return_value = task.cancelled.return_value = False
        task.cancel.side_effect = lambda: task.cancelled.configure_mock(return_value=True)
        executor._pending_tasks[task] = NS(source_node=None, source_entity=None)
        executor._ready_tasks.append(task)
        return task
    executor.create_task = create_task
    def dispatch(self):
        # Cached Jazzy 7.1.11 _wait_for_ready_callbacks queue semantics (657-667).
        with self._tasks_lock:
            for task in list(self._pending_tasks.keys()):
                if task.done() or task.cancelled():
                    del self._pending_tasks[task]
            ready_tasks_count = len(self._ready_tasks)
        for _ in range(ready_tasks_count):
            task = self._ready_tasks.popleft()
            task_data = self._pending_tasks[task]
            yield task, task_data.source_entity, task_data.source_node
    if queue_source == 'cached-jazzy':
        cached = Path('/tmp/opencode/native-ci/jazzy-executors.py')
        if not cached.is_file():
            pytest.skip('cached Jazzy source unavailable; portable queue semantics covered')
        method = next(item for item in ast.walk(ast.parse(cached.read_text()))
                      if isinstance(item, ast.FunctionDef) and item.name == '_wait_for_ready_callbacks')
        loop = next(item for item in method.body if isinstance(item, ast.While))
        index = next(i for i, item in enumerate(loop.body) if isinstance(item, ast.With))
        function = ast.parse('def dispatch(self):\n    nodes_to_use = []\n').body[0]
        function.body.extend(loop.body[index:index + 2])
        namespace = {}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])),
                     str(cached), 'exec'), namespace)
        dispatch = namespace['dispatch']
    # Control: the cached queue really does fail if its queued task is canceled.
    canceled = create_task(lambda: None)
    canceled.cancel()
    with pytest.raises(KeyError):
        list(dispatch(executor))
    executor._pending_tasks.clear()
    route = next(item for item in lab_run_statements()
                 if isinstance(item, ast.AsyncFunctionDef) and item.name == 'lab_ready')
    route.decorator_list = []
    holder = dict(bridge=object(), executor=executor, observation=None)
    snapshot = Mock(return_value={'ready': False, 'detail': 'observed'})
    async def short_wait(answer, timeout):
        return await asyncio.wait_for(answer, timeout=.01)
    namespace = dict(holder=holder, app=NS(state=NS()), ROBOT_ID=lab.ROBOT_ID,
                     asyncio=NS(get_running_loop=asyncio.get_running_loop, wait_for=short_wait,
                                TimeoutError=asyncio.TimeoutError),
                     readiness_snapshot=snapshot, node=None, state={}, lifecycle={}, metrics={})
    exec(compile(ast.Module(body=[route], type_ignores=[]), lab.__file__, 'exec'), namespace)
    async def check():
        ready = namespace['lab_ready']
        assert 'timed out' in (await ready(after=1.))['detail']
        answer = holder['observation']
        assert answer.cancelled()
        for _ in range(20):
            assert not (await ready(after=2.))['ready']
            assert holder['observation'] is answer
        assert len(executor._ready_tasks) == len(executor._pending_tasks) == 1
        task, _, _ = next(dispatch(executor))
        task.cancel.assert_not_called()
        task.callback()
        task.done.return_value = True
        await asyncio.sleep(0)
        assert holder['observation'] is None and answer.cancelled()
        assert 'timed out' in (await ready(after=3.))['detail']
        task, _, _ = next(dispatch(executor))
        task.callback()
        task.done.return_value = True
        await asyncio.sleep(0)
        assert snapshot.call_args.args[-2] == 3.
        assert not list(dispatch(executor))
        assert not executor._pending_tasks and holder['observation'] is None
        request = asyncio.create_task(ready(after=4.))
        await asyncio.sleep(0)
        task, _, _ = next(dispatch(executor))
        task.callback()
        task.done.return_value = True
        assert await request == {'ready': False, 'detail': 'observed'}
        assert holder['observation'] is None
    asyncio.run(check())


@pytest.mark.parametrize('origin', ['installed', 'source-fixture', 'explicit'])
def test_smoother_config_records_actual_file(tmp_path, origin):
    class Missing(LookupError):
        pass
    installed = tmp_path / 'share'
    source, override = tmp_path / 'source.yaml', tmp_path / 'override.yaml'
    path = {'installed': installed / 'param/defaults/smoother.yaml',
            'source-fixture': source, 'explicit': override}[origin]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('actual fixture bytes\n')
    get_share = Mock(return_value=str(installed))
    if origin != 'installed':
        get_share.side_effect = Missing('no launch index')
    resolved, evidence = lab.resolve_smoother_config(
        get_share, Missing, str(override) if origin == 'explicit' else None, source)
    assert resolved == path
    assert evidence == dict(source=origin, path=str(path.resolve()),
                            sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    if origin == 'explicit':
        get_share.assert_not_called()


@pytest.mark.parametrize('origin', ['installed', 'source-fixture', 'explicit'])
def test_missing_smoother_fails_without_substituting_defaults(tmp_path, origin):
    class Missing(LookupError):
        pass
    source = tmp_path / 'source.yaml'
    # An indexed package with missing config and an invalid override must fail,
    # even when a source fixture exists.
    if origin != 'source-fixture':
        source.write_text('fixture\n')
    get_share = Mock(return_value=str(tmp_path / 'share'))
    if origin == 'source-fixture':
        get_share.side_effect = Missing()
    with pytest.raises(AssertionError, match='configuration missing'):
        lab.resolve_smoother_config(get_share, Missing,
            str(tmp_path / 'missing.yaml') if origin == 'explicit' else None, source)


def inactive_task(coroutine, closes=True):
    task = NS(_handler=coroutine, _task_lock=threading.Lock(), _executing=False,
              done=Mock(return_value=False), cancelled=Mock(return_value=False))
    task.cancel = Mock(side_effect=coroutine.close if closes else None)
    return task


@pytest.mark.parametrize('closes', [False, True])
@pytest.mark.parametrize('state', ['created', 'suspended', 'canceled'])
def test_disposal_closes_real_coroutine_before_guards(closes, state):
    events = []
    class Pause:
        def __await__(self):
            yield
    async def handler():
        try:
            await Pause()
        finally:
            events.append('finalized')
    coroutine = handler()
    if state == 'suspended':
        coroutine.send(None)
    task = inactive_task(coroutine, closes)
    task.cancelled.return_value = state == 'canceled'
    if closes:
        task.cancel.side_effect = lambda: None if task.cancelled() else coroutine.close()
    # SimpleNamespace is not hashable, unlike an rclpy task.
    wrapper = Mock(wraps=task)
    wrapper._handler, wrapper._task_lock, wrapper._executing = coroutine, task._task_lock, False
    executor = executor_fixture()
    executor._ready_tasks.append(wrapper)
    executor.shutdown.side_effect = lambda **_: events.append('shutdown') or True
    resources = lab.LabResources()
    resources.executors.append(('bridge', executor, None))
    try:
        assert resources.close()
        task.cancel.assert_called_once()
        assert inspect.getcoroutinestate(coroutine) == inspect.CORO_CLOSED
        assert events == (['finalized', 'shutdown'] if state == 'suspended' else ['shutdown'])
    finally:
        coroutine.close()


@pytest.mark.parametrize('active', ['lock', 'executing', 'unknown'])
def test_disposal_never_closes_active_or_unknown_handler(active):
    async def handler():
        pass
    coroutine = handler()
    task = inactive_task(coroutine)
    if active == 'lock':
        task._task_lock.acquire()
    elif active == 'executing':
        task._executing = True
    else:
        del task._executing
    try:
        with pytest.raises(RuntimeError, match='still active|unsupported'):
            lab.dispose_task(task)
        task.cancel.assert_not_called()
        assert inspect.getcoroutinestate(coroutine) == inspect.CORO_CREATED
    finally:
        if active == 'lock':
            task._task_lock.release()
        coroutine.close()


def test_disposal_discovers_followups_and_keeps_completed_errors_on_failure(tmp_path):
    executor = executor_fixture()
    first, followup, broken = Mock(), Mock(), Mock()
    for task in (first, followup, broken):
        task._handler, task._task_lock, task._executing = None, threading.Lock(), False
        task.done.return_value = False
    first.cancel.side_effect = lambda: executor._ready_tasks.append(followup)
    broken.cancel.side_effect = ValueError('cancel failed')
    completed = Future()
    completed.set_exception(ValueError('completed failure'))
    executor._ready_tasks.extend((first, broken, completed))
    resources = lab.LabResources()
    resources.directory = tmp_path
    resources.executors.append(('bridge', executor, None))
    destroy = Mock()
    resources.nodes.append(('bridge', NS(destroy_node=destroy)))
    assert not resources.close()
    first.cancel.assert_called_once()
    followup.cancel.assert_called_once()
    executor.shutdown.assert_not_called()
    destroy.assert_not_called()
    errors = json.loads((tmp_path / 'teardown.json').read_text())['teardown']['errors']
    assert 'cancel failed' in str(errors) and 'completed failure' in str(errors)


def test_blocked_coroutine_finalizer_retains_handles():
    release = threading.Event()
    class Pause:
        def __await__(self):
            yield
    async def handler():
        try:
            await Pause()
        finally:
            release.wait(2)
    coroutine = handler()
    coroutine.send(None)
    task = Mock()
    task._handler, task._task_lock, task._executing = coroutine, threading.Lock(), False
    task.done.return_value = False
    task.cancel.side_effect = coroutine.close
    executor = executor_fixture()
    executor._ready_tasks.append(task)
    resources = lab.LabResources()
    resources.executors.append(('bridge', executor, None))
    destroy = Mock()
    resources.nodes.append(('bridge', NS(destroy_node=destroy)))
    try:
        assert not resources.close(timeout=.04)
        executor.shutdown.assert_not_called()
        destroy.assert_not_called()
    finally:
        release.set()
        for worker in threading.enumerate():
            if worker.name == 'cleanup-bridge-tasks-disposed':
                worker.join(1)
        assert inspect.getcoroutinestate(coroutine) == inspect.CORO_CLOSED


def setup_readiness(monkeypatch):
    monkeypatch.setattr(lab.time, "monotonic", lambda: 10.)
    teleop = NS(state=Mock(return_value=({"link_connected": True}, 9.9, object(), 9.9)),
                idle_at=9.9, safety=Mock(return_value=None), session=NS(active=False),
                _path_identity=Mock(return_value=(b"mux", b"smoother")))
    bridge = NS(_teleop=teleop, _latest_amcl=None)
    node = NS(count_subscribers=Mock(return_value=3))
    server = dict(online_capable=True, reason=None, base_at=9.9, link_connected=True)
    state = dict(fault="healthy", sensor_max_gap_s=.04)
    lifecycle = dict(state=3, at=9.9)
    metrics = dict(tick_at=9.9, tick_max_gap_s=.025)
    return [bridge, node, server, state, lifecycle, metrics, 9., "healthy"]


def test_readiness_reports_runtime_ages(monkeypatch):
    args = setup_readiness(monkeypatch)
    result = lab.readiness_snapshot(*args)
    assert result["ready"] and result["physical_acceptance"] is False
    assert result["detail"]["ages_s"]["base"] == pytest.approx(.1)


@pytest.mark.parametrize("event", ["base", "odom", "idle", "tick", "lifecycle", "server_base"])
@pytest.mark.parametrize("at", [None, 8., 10.1])
def test_every_readiness_event_must_follow_this_intent(monkeypatch, event, at):
    args = setup_readiness(monkeypatch)
    teleop = args[0]._teleop
    if event in {"base", "odom"}:
        values = list(teleop.state())
        values[1 if event == "base" else 3] = at
        teleop.state.return_value = values
    elif event == "idle":
        teleop.idle_at = at
    elif event == "server_base":
        args[2]["base_at"] = at
    else:
        args[5 if event == "tick" else 4]["tick_at" if event == "tick" else "at"] = at
    assert not lab.readiness_snapshot(*args)["ready"]


@pytest.mark.parametrize("failure", ["runtime-stale", "server-stale", "subscriber", "lifecycle", "identity", "capability", "active"])
def test_publication_does_not_override_runtime_or_graph(monkeypatch, failure):
    args = setup_readiness(monkeypatch)
    teleop = args[0]._teleop
    if failure == "runtime-stale":
        teleop.safety.return_value = "Wait for fresh robot hardware readings."
    elif failure == "server-stale":
        args[2]["reason"] = "Manual drive requires fresh hardware readings."
    elif failure == "subscriber":
        args[1].count_subscribers.return_value = 1
    elif failure == "lifecycle":
        args[4]["state"] = 2
    elif failure == "identity":
        teleop._path_identity.return_value = None
    elif failure == "capability":
        args[2]["online_capable"] = False
    else:
        teleop.session.active = True
    assert not lab.readiness_snapshot(*args)["ready"]


def test_fault_readiness_requires_both_runtime_and_server_observation(monkeypatch):
    args = setup_readiness(monkeypatch)
    args[3]["fault"] = args[7] = "sbc_down"
    args[0]._teleop.safety.return_value = "Robot hardware data is invalid or stale."
    assert not lab.readiness_snapshot(*args)["ready"]
    args[0]._teleop.state.return_value[0]["link_connected"] = False
    assert not lab.readiness_snapshot(*args)["ready"]
    args[2]["link_connected"] = False
    assert lab.readiness_snapshot(*args)["ready"]


@pytest.mark.parametrize('failure', [None, 'healthy', 'missing-evidence', 'base', 'tick'])
def test_stale_fault_requires_retained_decision_and_continuing_events(monkeypatch, failure):
    args = setup_readiness(monkeypatch)
    args[3]['fault'] = args[7] = 'odom_stale'
    teleop = args[0]._teleop
    teleop.state.return_value = ({'link_connected': True}, 9.9, object(), 9.2)
    teleop.safety.return_value = 'Raw odometry is missing or stale.'
    args[3]['odom_fault_evidence'] = {'retained': 'source-expiry decision'}
    if failure == 'healthy':
        args[3]['fault'] = 'healthy'
    elif failure == 'missing-evidence':
        args[3]['odom_fault_evidence'] = None
    elif failure == 'base':
        values = list(teleop.state.return_value)
        values[1] = 8.
        teleop.state.return_value = values
    elif failure == 'tick':
        args[5]['tick_at'] = 8.
    result = lab.readiness_snapshot(*args)
    assert result['fault_observed'] is (failure is None)
    assert result['ready'] is False


@pytest.mark.parametrize('kind', ['coroutine', 'thread', 'unraisable'])
def test_main_reports_background_errors_without_suppressing_hooks(monkeypatch, tmp_path, kind):
    thread_hook, unraisable_hook = Mock(), Mock()
    monkeypatch.setattr(lab.threading, 'excepthook', thread_hook)
    monkeypatch.setattr(lab.sys, 'unraisablehook', unraisable_hook)
    monkeypatch.setattr(lab.signal, 'signal', Mock())
    def run(resources):
        resources.directory = tmp_path
        if kind == 'thread':
            def fail():
                raise ValueError('background failure')
            worker = threading.Thread(target=fail)
            worker.start()
            worker.join()
        elif kind == 'unraisable':
            class Broken:
                def __del__(self):
                    raise ValueError('unraisable failure')
            del_object = Broken()
            del del_object
        else:
            async def handler():
                pass
            coroutine = handler()
            del coroutine
        gc.collect()
    monkeypatch.setattr(lab, 'run_lab', run)
    with pytest.raises(RuntimeError, match='teardown failed'):
        lab.main()
    errors = json.loads((tmp_path / 'teardown.json').read_text())['teardown']['errors']
    assert any(error['step'] == ('background-thread' if kind == 'thread' else 'unraisable')
               for error in errors)
    (thread_hook if kind == 'thread' else unraisable_hook).assert_called_once()
    assert lab.threading.excepthook is thread_hook and lab.sys.unraisablehook is unraisable_hook


def test_cleanup_quiesces_all_executors_before_session_and_handles(capsys, tmp_path):
    resources = lab.LabResources()
    resources.directory = tmp_path
    events = []
    resources.bridge = NS(ws=NS(stop=lambda: events.append("transport")),
                          _teleop=NS(shutdown=lambda: events.append("session")))
    for name in ("receiver", "bridge", "probe"):
        executor = executor_fixture()
        executor.shutdown = lambda timeout_sec, n=name: events.append(n + "-shutdown")
        worker = NS(ident=1, join=lambda timeout, n=name: events.append(n + "-join"), is_alive=lambda: False)
        resources.executors.append((name, executor, worker))
        resources.nodes.append((name, NS(destroy_node=lambda n=name: events.append(n + "-destroy"))))
    resources.context_shutdown = lambda: events.append("context")
    assert resources.close()
    assert events == ["transport", "probe-join", "bridge-join", "receiver-join", "probe-shutdown",
                      "bridge-shutdown", "receiver-shutdown", "session", "probe-destroy",
                      "bridge-destroy", "receiver-destroy", "context"]
    assert json.loads((tmp_path / "teardown.json").read_text())["teardown"]["passed"]


@pytest.mark.parametrize("failure", ["false", "raises", "survives"])
def test_cleanup_failure_retains_handles_but_still_stops_http(failure):
    resources = lab.LabResources()
    def shutdown(timeout_sec):
        if failure == "raises":
            raise RuntimeError("broken executor")
        return failure != "false"
    worker = NS(ident=1, join=Mock(), is_alive=lambda: failure == "survives")
    executor = executor_fixture()
    executor.shutdown = shutdown
    resources.executors.append(("probe", executor, worker))
    destroy = Mock()
    resources.nodes.append(("probe", NS(destroy_node=destroy)))
    server = NS(should_exit=False)
    resources.server = (server, NS(join=Mock(), is_alive=lambda: False))
    assert not resources.close()
    destroy.assert_not_called()
    assert server.should_exit


def test_cleanup_one_budget_even_when_stop_blocks():
    resources = lab.LabResources()
    release = threading.Event()
    executor = executor_fixture()
    executor.shutdown = lambda timeout_sec: release.wait(1)
    resources.executors.append(("probe", executor, None))
    server = NS(should_exit=False)
    resources.server = (server, NS(join=Mock(), is_alive=lambda: False))
    started = time.monotonic()
    try:
        assert not resources.close(timeout=.04)
        assert time.monotonic() - started < .5
        assert server.should_exit
    finally:
        release.set()
        for worker in threading.enumerate():
            if worker.name == "cleanup-probe-shutdown":
                worker.join(1)


@pytest.mark.parametrize('failure', ['blocks', 'false', 'raises'])
def test_node_destroy_failure_retains_remaining_handles_and_context(tmp_path, failure):
    resources = lab.LabResources()
    resources.directory = tmp_path
    release, entered = threading.Event(), threading.Event()
    untouched, shutdown = Mock(), Mock()
    def destroy():
        entered.set()
        if failure == 'blocks':
            release.wait(2)
        elif failure == 'raises':
            raise RuntimeError('destroy failed')
        else:
            return False
    resources.nodes.extend([('receiver', NS(destroy_node=untouched)),
                            ('bridge', NS(destroy_node=destroy))])
    resources.context_shutdown = shutdown
    server = NS(should_exit=False)
    resources.server = (server, NS(join=Mock(), is_alive=lambda: False))
    started = time.monotonic()
    try:
        assert not resources.close(timeout=.04)
        assert entered.is_set() and time.monotonic() - started < .5
        untouched.assert_not_called()
        shutdown.assert_not_called()
        assert server.should_exit
        report = json.loads((tmp_path / 'teardown.json').read_text())['teardown']
        assert any(error['step'] == 'bridge-destroy' for error in report['errors'])
        assert 'context-shutdown' not in report['steps']
        if failure == 'blocks':
            assert any(error['step'] == 'worker' and error['name'] == 'cleanup-bridge-destroy'
                       for error in report['errors'])
    finally:
        release.set()
        for worker in threading.enumerate():
            if worker.name == 'cleanup-bridge-destroy':
                worker.join(1)
    shutdown.assert_not_called()


@pytest.mark.parametrize("early,forced", [(True, False), (False, True), (False, False)])
def test_native_exit_and_forced_kill_are_failures(monkeypatch, early, forced):
    resources = lab.LabResources()
    signals = []
    def killpg(pid, sig):
        assert pid == 45678
        if early or sig == 0:
            raise ProcessLookupError
        signals.append(sig)
    def wait(timeout):
        if forced and signal.SIGKILL not in signals:
            raise subprocess.TimeoutExpired("native", timeout)
        return 0
    resources.processes.append(NS(pid=45678, poll=lambda: 0 if early else None, wait=wait))
    monkeypatch.setattr(lab.os, "killpg", killpg)
    assert resources.close() is (not early and not forced)
    if forced:
        assert signals == [signal.SIGINT, signal.SIGTERM, signal.SIGKILL]


def test_partial_startup_runs_cleanup_and_preserves_startup_error(monkeypatch, capsys):
    destroyed = Mock()
    def fail(resources):
        resources.nodes.append(("receiver", NS(destroy_node=destroyed)))
        raise RuntimeError("startup failed")
    monkeypatch.setattr(lab, "run_lab", fail)
    monkeypatch.setattr(lab.signal, "signal", Mock())
    with pytest.raises(RuntimeError, match="startup failed"):
        lab.main()
    destroyed.assert_called_once()
    assert json.loads(capsys.readouterr().out)["teardown"]["passed"]


def test_shell_runner_propagates_supervisor_exit(tmp_path):
    # Override only setup sourcing and python; no installed ROS is consulted.
    bash_env = tmp_path / "bash-env"
    bash_env.write_text("source() { return 0; }\n")
    python = tmp_path / "python3"
    python.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\nexit 7\n')
    python.chmod(0o755)
    env = dict(os.environ, BASH_ENV=str(bash_env), PATH=str(tmp_path) + ":" + os.environ["PATH"])
    runner = Path(lab.__file__).with_name("run_teleop_lab.sh")
    result = subprocess.run(["bash", str(runner)], env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 7
    assert result.stdout.splitlines()[-1] == "--supervise"


@pytest.mark.parametrize("case,expected", [("success", 0), ("checker", 7), ("lab", 3),
    ("startup", 1), ("spawn", 1), ("forced", 1), ("teardown", 1), ("missing-teardown", 1),
    ("run-timeout", 1), ("checker-cleanup-error", 1), ("orphan-checker", 1),
    ("orphan-lab", 1), ("orphan-native", 1), ("never-awaited", 1),
    ("unraisable", 1), ("background-thread", 1)])
def test_supervisor_preserves_status_and_full_logs(monkeypatch, tmp_path, capsys, case, expected):
    monkeypatch.setattr(lab, "require_isolation", Mock())
    monkeypatch.setattr(lab.tempfile, "mkdtemp", lambda **_: str(tmp_path))
    monkeypatch.setattr(lab.signal, "signal", Mock())
    listener = NS(bind=Mock(), getsockname=lambda: ("127.0.0.1", 12345), fileno=lambda: 99, close=Mock())
    monkeypatch.setattr(lab.socket, "socket", lambda: listener)
    if case in {"run-timeout", "checker-cleanup-error"}:
        ticks = iter([0., 0., 0., 121.])
        monkeypatch.setattr(lab.time, "monotonic", lambda: next(ticks))
        monkeypatch.setattr(lab.threading, "Event", lambda: NS(is_set=lambda: False, wait=lambda _: False))
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(
        dict(robot_id=lab.ROBOT_ID, physical_acceptance=False)).encode()))
    kills = []
    orphan = {"orphan-checker": 124, "orphan-lab": 123, "orphan-native": 125}.get(case)
    if case == "orphan-native":
        (tmp_path / "native-pids.json").write_text("[125]")
    def killpg(pid, sig):
        if case == "checker-cleanup-error" and pid == 124:
            raise PermissionError("mock cleanup failure")
        if sig == 0:
            if pid != orphan or (pid, signal.SIGKILL) in kills:
                raise ProcessLookupError
            return
        kills.append((pid, sig))
    monkeypatch.setattr(lab.os, "killpg", killpg)
    def spawn(argv, **kwargs):
        if case == "spawn":
            raise OSError("startup spawn failure")
        is_lab = Path(argv[-1]).name == "teleop_lab.py"
        kwargs["stdout"].write("first line\n" + "middle\n" * 100 + "last line\n")
        warning = {'never-awaited': "RuntimeWarning: coroutine 'handler' was never awaited",
                   'unraisable': 'Exception ignored in: <coroutine object handler>',
                   'background-thread': 'Exception in thread worker:'}.get(case)
        if warning:
            kwargs['stdout'].write(warning + '\n')
        proc = NS(pid=123 if is_lab else 124, returncode=7 if case == "checker" and not is_lab else 0)
        proc.poll = lambda: ((0 if case == "startup" else None) if is_lab else
                            (None if case in {"run-timeout", "checker-cleanup-error"} else
                             7 if case == "checker" else 0))
        proc.send_signal = Mock()
        def wait(timeout):
            if case == "forced" and not kills:
                raise subprocess.TimeoutExpired("lab", timeout)
            proc.returncode = 3 if case == "lab" else 0
            if case != "missing-teardown":
                (tmp_path / "teardown.json").write_text(json.dumps({"teardown": {"passed": case != "teardown"}}))
            return proc.returncode
        proc.wait = wait
        return proc
    monkeypatch.setattr(lab.subprocess, "Popen", spawn)
    assert lab.supervise() == expected
    output = capsys.readouterr().out
    assert '"passed": ' + ("true" if expected == 0 else "false") in output
    assert '"evidence_dir":' in output
    if case != "spawn":
        assert "first line\n" + "middle\n" * 100 + "last line" in output
    if case == "forced":
        assert kills == [(123, signal.SIGKILL)]
    if orphan is not None:
        assert (orphan, signal.SIGKILL) in kills
    if case == "checker-cleanup-error":
        assert (tmp_path / "teardown.json").exists(), "lab cleanup must survive checker cleanup error"
    assert json.loads((tmp_path / "run.json").read_text())["lab_run"]["returncode"] == expected


def test_supervisor_checks_isolation_before_binding(monkeypatch):
    monkeypatch.setenv("ROS_DOMAIN_ID", "0")
    bind = Mock()
    monkeypatch.setattr(lab.socket, "socket", bind)
    with pytest.raises(AssertionError):
        lab.supervise()
    bind.assert_not_called()


@pytest.mark.parametrize("layout", ["jazzy", "legacy"])
@pytest.mark.parametrize("pooled", [False, True])
@pytest.mark.parametrize("failure", [False, True])
def test_real_pool_drains_before_guards_and_reports_task_errors(layout, pooled, failure, tmp_path):
    resources = lab.LabResources()
    resources.directory = tmp_path
    executor = executor_fixture()
    task = Future()
    if layout == "legacy":
        del executor._pending_tasks, executor._ready_tasks
        executor._tasks = [(task, None, None)]
    else:
        executor._pending_tasks[task] = NS(source_node=None, source_entity=None)
        executor._ready_tasks.append(task)
    finished = threading.Event()
    def complete():
        if failure:
            task.set_exception(ValueError("late callback failure"))
        else:
            task.set_result(None)
        finished.set()
    if pooled:
        executor._executor = ThreadPoolExecutor(1)
        executor._futures = [task]
        def callback():
            time.sleep(.03)
            complete()
        executor._executor.submit(callback)
    else:
        complete()
    def shutdown(timeout_sec):
        assert finished.is_set(), "guard destroyed before callback completed"
        if pooled:
            assert all(not thread.is_alive() for thread in executor._executor._threads)
        return True
    executor.shutdown = Mock(side_effect=shutdown)
    resources.executors.append(("probe", executor, None))
    resources.nodes.append(("probe", NS(destroy_node=Mock())))
    assert resources.close() is not failure
    executor.shutdown.assert_called_once()
    errors = json.loads((tmp_path / "teardown.json").read_text())["teardown"]["errors"]
    assert any("late callback failure" in str(error) for error in errors) is failure


def test_pool_timeout_retains_guards_nodes_and_context():
    resources = lab.LabResources()
    executor = executor_fixture(_executor=ThreadPoolExecutor(1), _futures=[])
    release = threading.Event()
    executor._executor.submit(release.wait)
    resources.executors.append(("probe", executor, None))
    destroy = Mock()
    resources.nodes.append(("probe", NS(destroy_node=destroy)))
    resources.context_shutdown = Mock()
    started = time.monotonic()
    try:
        assert not resources.close(timeout=.04)
        assert time.monotonic() - started < .5
        executor.shutdown.assert_not_called()
        destroy.assert_not_called()
        resources.context_shutdown.assert_not_called()
    finally:
        release.set()
        executor._executor.shutdown(wait=True)
        for thread in threading.enumerate():
            if thread.name == "cleanup-probe-pool-drain":
                thread.join(1)


def test_unknown_task_layout_retains_handles():
    resources = lab.LabResources()
    executor = executor_fixture()
    del executor._pending_tasks
    resources.executors.append(("probe", executor, None))
    resources.context_shutdown = Mock()
    assert not resources.close()
    executor.shutdown.assert_not_called()
    resources.context_shutdown.assert_not_called()


def test_spinner_stops_dispatch_before_shutdown():
    resources = lab.LabResources()
    executor = executor_fixture()
    dispatched = threading.Event()
    executor.spin_once = lambda timeout_sec: dispatched.set() or time.sleep(.005)
    resources.executors.append(("probe", executor, None))
    resources.start_executor(0)
    assert dispatched.wait(1)
    assert resources.close()
    assert not resources.executors[0][2].is_alive()


@pytest.mark.parametrize("code,passed", [(0, True), (-2, True), (130, True), (254, True),
                                       (1, False), (139, False), (255, False)])
def test_native_exit_codes_checked(monkeypatch, code, passed):
    resources = lab.LabResources()
    resources.processes.append(NS(pid=45678, poll=lambda: None, wait=lambda timeout: code))
    def killpg(pid, sig):
        if sig == 0:
            raise ProcessLookupError
    monkeypatch.setattr(lab.os, "killpg", killpg)
    assert resources.close() is passed


def test_packaging_requires_built_frontend(tmp_path):
    import shutil
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    script = root / "scripts/package_teleop_lab.py"
    shutil.copy2(Path(lab.__file__).with_name(script.name), script)
    for source in ("server", "ros2_ws/src/patrolbot_web_bridge", "contracts", "shared/schemas"):
        (root / source).mkdir(parents=True)
    output = tmp_path / "context"
    result = subprocess.run([sys.executable, str(script), str(output)], capture_output=True, text=True)
    assert result.returncode != 0 and "frontend/dist" in result.stderr
    assert not output.exists()
    (root / "frontend/dist").mkdir(parents=True)
    (root / "frontend/dist/index.html").write_text("lab build")
    (root / "infrastructure/teleop-lab").mkdir(parents=True)
    (root / "infrastructure/teleop-lab/Dockerfile").write_text("FROM lab\n")
    for name in ("teleop_lab.py", "check_teleop_receipt.py", "run_teleop_lab.sh"):
        (root / "scripts" / name).write_text("# lab\n")
    result = subprocess.run([sys.executable, str(script), str(output)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    manifest = json.loads((output / "CONTEXT.sha256.json").read_text())
    assert set(manifest) == {"Dockerfile", "frontend/dist/index.html", "scripts/teleop_lab.py",
                             "scripts/check_teleop_receipt.py", "scripts/run_teleop_lab.sh"}


def test_spinner_exception_is_retained_after_task_removed(monkeypatch, tmp_path):
    resources = lab.LabResources()
    resources.directory = tmp_path
    executor = executor_fixture()
    def spin_once(timeout_sec):
        raise ValueError("removed task failed")
    executor.spin_once = spin_once
    hook = Mock()
    monkeypatch.setattr(threading, "excepthook", hook)
    resources.executors.append(("probe", executor, None))
    resources.start_executor(0)
    resources.executors[0][2].join(1)
    assert not resources.close()
    hook.assert_called_once()
    assert "removed task failed" in (tmp_path / "teardown.json").read_text()


def test_native_group_retired_even_after_launcher_exit(monkeypatch):
    resources = lab.LabResources()
    resources.processes.append(NS(pid=45678, poll=lambda: 0, wait=lambda timeout: 0))
    signals = []
    def killpg(pid, sig):
        if sig == 0:
            raise ProcessLookupError
        signals.append(sig)
    monkeypatch.setattr(lab.os, "killpg", killpg)
    assert not resources.close(), "early launcher exit must remain a failure"
    assert signals == [signal.SIGINT]
