"""Isolated software lab: real ROS receiver/mux/smoother, never a physical base.

Run only inside the dedicated network-none test container. Synthetic hardware
exists here, never in application startup or production readiness code.
"""
from __future__ import annotations

import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import gc
import hashlib
import inspect
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import warnings


ROBOT_ID = "isolated-web-teleop"
PORT = 8027


def resolve_smoother_config(get_share, package_missing, override=None,
        source=Path('/opt/patrolbot/ros2_ws/src/patrolbot-launch/param/defaults/smoother.yaml')):
    """LAB ONLY: use the actual installed config or the packaged source fixture."""
    if override is not None:
        path, origin = Path(override), 'explicit'
    else:
        try:
            path = Path(get_share('patrolbot-launch')) / 'param/defaults/smoother.yaml'
            origin = 'installed'
        except package_missing:
            path, origin = Path(source), 'source-fixture'
    assert path.is_file(), f'Native lab smoother configuration missing ({origin}): {path}'
    return path, {'source': origin, 'path': str(path.resolve()),
                  'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def dispose_task(task):
    """LAB ONLY: match the core NativeFixture adapter after all workers drain."""
    # concurrent.futures represent submitted pool calls, not ROS handlers.
    from concurrent.futures import Future
    if isinstance(task, Future):
        if task.done() and not task.cancelled():
            return task.exception()
        if not task.cancelled():
            raise RuntimeError('lab pool future still pending after worker drain')
        return
    handler = getattr(task, '_handler', None)
    lock = getattr(task, '_task_lock', None)
    if (not hasattr(task, '_handler') or not isinstance(lock, type(threading.Lock()))
            or not isinstance(getattr(task, '_executing', None), bool)
            or not (handler is None or callable(handler) or inspect.iscoroutine(handler))):
        raise RuntimeError('unsupported lab executor task internals; update LAB ONLY adapter')
    if not lock.acquire(blocking=False):
        raise RuntimeError('lab executor task still active after worker drain')
    try:
        if task._executing or (inspect.iscoroutine(handler) and handler.cr_running):
            raise RuntimeError('lab executor task still active after worker drain')
        if task.done():
            return task.exception()
        task.cancel()
        # Older rclpy versions only change state; cached Jazzy also closes here.
        if inspect.iscoroutine(handler):
            handler.close()
    finally:
        lock.release()


def executor_tasks(executor):
    """LAB ONLY: support legacy triples and cached Jazzy 7.1.11 task storage."""
    if not hasattr(executor, "_tasks_lock"):
        raise RuntimeError("unsupported lab executor task lock; update LAB ONLY adapter")
    with executor._tasks_lock:
        if isinstance(getattr(executor, "_tasks", None), list):
            tasks = [task for task, _entity, _node in executor._tasks]
        elif (isinstance(getattr(executor, "_pending_tasks", None), dict)
              and isinstance(getattr(executor, "_ready_tasks", None), deque)):
            tasks = list(executor._pending_tasks) + list(executor._ready_tasks)
        else:
            raise RuntimeError("unsupported lab executor task storage; update LAB ONLY adapter")
    if hasattr(executor, "_executor"):
        if (not isinstance(executor._executor, ThreadPoolExecutor)
                or not isinstance(getattr(executor, "_futures", None), list)):
            raise RuntimeError("unsupported lab executor worker pool; update LAB ONLY adapter")
        tasks += executor._futures
    elif hasattr(executor, "_futures"):
        raise RuntimeError("unsupported lab executor without worker pool")
    return tasks


def require_isolation():
    assert os.environ.get("PATROLBOT_ISOLATED_TEST") == "1"
    assert os.environ.get("ROS_DOMAIN_ID") == "188"
    assert {path.name for path in Path("/sys/class/net").iterdir()} == {"lo"}


def observe_odom_fault(teleop, state):
    """LAB ONLY: retain the safety decision that actually closes the session."""
    safety, send = teleop.session.safety, teleop.session.send
    decision = None

    def observe_safety():
        nonlocal decision
        decision = None
        if state['fault'] != 'odom_stale' or not teleop.session.active:
            return safety()
        started = time.monotonic()
        source_before = teleop.node.get_clock().now().nanoseconds
        _, base_at, odom, odom_at = teleop.state()
        reason = safety()
        source_after = teleop.node.get_clock().now().nanoseconds
        ended = time.monotonic()
        if state['fault'] == 'odom_stale' and teleop.session.active:
            evidence = dict(robot_id=ROBOT_ID, command_id=teleop.session.command_id,
                fault='odom_stale', injected_mono=state['fault_injected_mono'],
                safety_started_mono=started, observed_mono=ended, reason=reason,
                base_at=base_at, odom_at=odom_at,
                source_before_ns=source_before, source_after_ns=source_after)
            try:
                p, q, v = odom.pose.pose.position, odom.pose.pose.orientation, odom.twist.twist
                evidence['odom_values'] = [p.x, p.y, q.x, q.y, q.z, q.w, v.linear.x, v.angular.z]
                evidence['stamp_sec'] = odom.header.stamp.sec
                evidence['stamp_nanosec'] = odom.header.stamp.nanosec
            except (AttributeError, TypeError):
                pass  # Missing/malformed evidence must fail the checker.
            decision = evidence
        return reason

    def observe_send(kind, data):
        if kind == 'command.result' and decision is not None:
            state['odom_fault_evidence'] = dict(decision, terminal=dict(data),
                                               terminal_mono=time.monotonic())
        return send(kind, data)

    teleop.session.safety, teleop.session.send = observe_safety, observe_send


def readiness_snapshot(bridge, node, server, state, lifecycle, metrics, after, fault):
    teleop = bridge._teleop
    now = time.monotonic()
    _, base_at, _, odom_at = teleop.state()
    observed = {"base": base_at, "odom": odom_at, "idle": teleop.idle_at, "server_base": server["base_at"],
                "tick": metrics["tick_at"], "lifecycle": lifecycle["at"]}
    subscribers = {topic: node.count_subscribers(topic) for topic in (
        "/patrolbot/base_state", "/odom", "/patrolbot/manual_active",
        "/input/joy", "/input/teleop", "/cmd_vel_out", "/cmd_vel")}
    reason = teleop.safety()
    graph_ready = all(subscribers[topic] >= minimum for topic, minimum in (
        ("/patrolbot/base_state", 1), ("/odom", 1), ("/patrolbot/manual_active", 1),
        ("/input/joy", 1), ("/input/teleop", 2), ("/cmd_vel_out", 3), ("/cmd_vel", 2)))
    server_ready = server["online_capable"] and (server["reason"] is None if fault == "healthy"
                                                else server["link_connected"] is False)
    fault_observed = (reason is None if fault == "healthy" else
        fault == "sbc_down" and teleop.state()[0].get("link_connected") is False
        and server["link_connected"] is False)
    return {"ready": bool(fault_observed and server_ready and not teleop.session.active
                and state["fault"] == fault and graph_ready
                and lifecycle["state"] == 3 and teleop._path_identity() is not None
                and all(at is not None and after < at <= now for at in observed.values())),
            "fault_observed": bool(fault == 'odom_stale' and state['fault'] == fault
                and state.get('odom_fault_evidence') is not None
                and all(at is not None and after < at <= now
                        for at in (base_at, metrics['tick_at']))),
            "fault_evidence": state.get('odom_fault_evidence'),
            "physical_acceptance": False, "observed_mono": now,
            "has_amcl_pose": bridge._latest_amcl is not None,
            "detail": {"runtime_safety_reason": reason, "subscribers": subscribers,
                       "server_ready": server_ready, "expected_fault": fault,
                       "server_safety_reason": server["reason"],
                       "lifecycle": dict(lifecycle),
                       "ages_s": {key: None if at is None else now - at for key, at in observed.items()},
                       "sensor_max_gap_s": state["sensor_max_gap_s"],
                       "tick_max_gap_s": metrics["tick_max_gap_s"]}}


class LabResources:
    """Own partial startup too; all cleanup shares one monotonic deadline."""

    def __init__(self):
        self.initial_workers = set(threading.enumerate())
        self.executors = []
        self.nodes = []
        self.processes = []
        self.server = None
        self.listener = None
        self.bridge = None
        self.context_shutdown = None
        self.log = None
        self.stopped = threading.Event()
        self.dispatch_stopped = threading.Event()
        self.spin_errors = []
        self.directory = None
        self.background_errors = []

    def start_executor(self, index):
        name, executor, _ = self.executors[index]
        def spin():
            try:
                while not self.dispatch_stopped.is_set():
                    executor.spin_once(timeout_sec=.05)
            except BaseException as error:
                self.spin_errors.append({"step": name + "-spin", "error": repr(error)})
                raise
        worker = threading.Thread(target=spin, name="lab-" + name, daemon=True)
        self.executors[index] = (name, executor, worker)
        worker.start()

    def close(self, timeout=20):
        deadline = time.monotonic() + timeout
        errors = []
        steps = []

        def remaining():
            return max(0, deadline - time.monotonic())

        def attempt(name, callback):
            # Third-party stop methods may block even when given a timeout.
            failure = []
            def run():
                try:
                    if callback() is False:
                        failure.append("returned false")
                except BaseException as error:
                    failure.append(repr(error))
            task = threading.Thread(target=run, name="cleanup-" + name, daemon=True)
            task.start()
            task.join(min(3, remaining()))
            if task.is_alive() or failure:
                errors.append({"step": name, "error": failure or "worker survived deadline"})
                return False
            steps.append(name)
            return True

        # Stop transport intent, then callbacks, then cancel the session before
        # destroying any publisher/client handles used by shutdown().
        quiesced = True
        if self.bridge is not None:
            quiesced = attempt("transport-stop", self.bridge.ws.stop)
            ws_worker = getattr(self.bridge.ws, "_thread", None)
            if ws_worker is not None and ws_worker.is_alive():
                errors.append({"step": "transport-stop", "error": "websocket worker survived"})
                quiesced = False
        self.dispatch_stopped.set()
        for name, executor, _ in self.executors:
            quiesced = attempt(name + "-wake", lambda e=executor: e.wake()) and quiesced
        joined = []
        for name, executor, worker in reversed(self.executors):
            ok = True
            if worker is not None and worker.ident is not None:
                worker.join(min(1, remaining()))
                if worker.is_alive():
                    errors.append({"step": name, "error": "executor worker survived"})
                    ok = False
            joined.append((name, executor, ok))
        for name, executor, ok in joined:
            tasks = []
            if ok:
                ok = attempt(name + "-tasks", lambda e=executor: tasks.extend(executor_tasks(e)))
            if ok and hasattr(executor, "_executor"):
                # Jazzy shutdown can return True with submitted callbacks still running.
                ok = attempt(name + "-pool-drain", lambda e=executor:
                             e._executor.shutdown(wait=True, cancel_futures=True))
            if ok:
                def dispose(e=executor, tasks=tasks, task_name=name):
                    seen = set()
                    failed = False
                    while True:
                        tasks.extend(executor_tasks(e))
                        pending = dict.fromkeys(t for t in tasks if t not in seen)
                        if not pending:
                            if failed:
                                raise RuntimeError('lab task disposal failed; handles retained')
                            return
                        for task in pending:
                            seen.add(task)
                            try:
                                error = dispose_task(task)
                            except BaseException as error:
                                errors.append({'step': task_name + '-task-dispose', 'error': repr(error)})
                                failed = True
                                continue
                            if error is not None:
                                errors.append({'step': task_name + '-task-exception', 'error': repr(error)})
                ok = attempt(name + "-tasks-disposed", dispose)
            quiesced = ok and quiesced
        if quiesced:
            quiesced = attempt('task-gc', gc.collect)
        if quiesced:
            for name, executor, _ in joined:
                quiesced = attempt(name + "-shutdown", lambda e=executor:
                                   e.shutdown(timeout_sec=min(2, remaining()))) and quiesced
        errors.extend(self.spin_errors)
        if quiesced and self.bridge is not None and self.bridge._teleop is not None:
            quiesced = attempt("session-end", self.bridge._teleop.shutdown)
        if quiesced:
            for name, node in reversed(self.nodes):
                quiesced = attempt(name + "-destroy", node.destroy_node)
                if not quiesced:
                    break
        if not quiesced:
            errors.append({"step": "nodes", "error": "handles retained because cleanup did not quiesce"})
        for process in self.processes:
            try:
                code = process.poll()
                if code is not None:
                    errors.append({"step": "native-child", "error": "exited before cleanup", "returncode": code})
                # Signal the group even if ros2's parent exited before its native child.
                sent = []
                for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
                    try:
                        os.killpg(process.pid, sig)
                        sent.append(sig)
                    except ProcessLookupError:
                        break
                    except OSError as error:
                        errors.append({"step": "native-child", "error": repr(error)})
                        break
                    if sig == signal.SIGKILL:
                        errors.append({"step": "native-child", "error": "forced kill", "pid": process.pid})
                    try:
                        process.wait(timeout=min(2, remaining()))
                    except subprocess.TimeoutExpired:
                        continue
                    # Allow native descendants to exit after the ros2 launcher.
                    grace = time.monotonic() + min(.3, remaining())
                    gone = False
                    while True:
                        try:
                            os.killpg(process.pid, 0)
                        except ProcessLookupError:
                            gone = True
                            break
                        if time.monotonic() >= grace:
                            break
                        time.sleep(min(.02, remaining()))
                    if gone:
                        break
                else:
                    errors.append({"step": "native-child", "error": "process group survived", "pid": process.pid})
                code = process.wait(timeout=remaining())
                # ros2 run can sys.exit() with the native child's negative signal code.
                accepted = ({0} | {-sig for sig in sent} | {128 + sig for sig in sent}
                            | {256 - sig for sig in sent})
                if code not in accepted:
                    errors.append({"step": "native-child", "error": "unexpected exit", "returncode": code})
            except Exception as error:
                errors.append({"step": "native-child", "error": repr(error)})
        if self.server is not None:
            server, worker = self.server
            server.should_exit = True
            if getattr(worker, "ident", 1) is not None:
                worker.join(min(3, remaining()))
            if worker.is_alive():
                server.force_exit = True
                errors.append({"step": "http", "error": "server worker survived"})
        if self.listener is not None:
            attempt("listener-close", self.listener.close)
        if quiesced and self.context_shutdown is not None:
            attempt("context-shutdown", self.context_shutdown)
        if self.log is not None:
            attempt("log-close", self.log.close)
        for worker in set(threading.enumerate()) - self.initial_workers:
            if worker.is_alive():
                errors.append({"step": "worker", "error": "thread survived", "name": worker.name})
        errors.extend(self.background_errors)
        report = {"teardown": {"passed": not errors, "steps": steps, "errors": errors,
                                "elapsed_s": timeout - remaining()}}
        if self.directory is not None:
            (self.directory / "teardown.json").write_text(json.dumps(report) + "\n")
        print(json.dumps(report), flush=True)
        return not errors


def run_lab(resources):
    global PORT
    require_isolation()
    os.environ.update(WEB_BRIDGE_ENABLE_COMMANDS="1",
        WEB_BRIDGE_SERVER_URL=f"ws://127.0.0.1:{PORT}/ws/robot",
        WEB_BRIDGE_TOKEN="isolated-teleop-only")

    import rclpy
    from geometry_msgs.msg import Twist
    from lifecycle_msgs.srv import ChangeState, GetState
    from nav_msgs.msg import Odometry
    from patrolbot_interfaces.msg import BaseState
    from patrolbot_web_bridge.bridge_node import WebBridgeNode
    from patrolbot_web_bridge.teleop import read_contract
    from rclpy.executors import MultiThreadedExecutor, SingleThreadedExecutor
    from rclpy.node import Node
    from rclpy.signals import SignalHandlerOptions
    from std_msgs.msg import Bool
    from std_srvs.srv import Trigger
    import uvicorn
    from app.main import create_app
    from app.settings import Settings
    from app.commands.gates import teleop_reason

    directory = Path(os.environ.get("TELEOP_LAB_EVIDENCE_DIR") or tempfile.mkdtemp(prefix="teleop-software-lab-"))
    resources.directory = directory
    listener = socket.socket(fileno=int(os.environ["TELEOP_LAB_SOCKET_FD"])) if "TELEOP_LAB_SOCKET_FD" in os.environ else socket.socket()
    resources.listener = listener
    if "TELEOP_LAB_SOCKET_FD" not in os.environ:
        listener.bind(("127.0.0.1", int(os.environ.get("TELEOP_LAB_PORT", PORT))))
    PORT = listener.getsockname()[1]
    os.environ["WEB_BRIDGE_SERVER_URL"] = f"ws://127.0.0.1:{PORT}/ws/robot"
    history = {topic: deque(maxlen=4000) for topic in (
        "/input/teleop", "/patrolbot/web_teleop_active", "/cmd_vel_out", "/cmd_vel")}
    lock = threading.Lock()
    state = {"fault": "healthy", "odom_count": 0, "base_count": 0, "sensor_at": None, "sensor_max_gap_s": 0.0}
    settings = Settings(_env_file=None, database_path=str(directory / "test.sqlite3"),
                        default_robot_id=ROBOT_ID, robot_token="isolated-teleop-only")
    app = create_app(settings)
    # Register lab-only diagnostics before the application's SPA catch-all.
    from starlette.routing import Mount
    static_mounts = [route for route in app.router.routes if isinstance(route, Mount) and route.path == ""]
    for route in static_mounts:
        app.router.routes.remove(route)

    @app.get("/lab/status")
    async def lab_status():
        with lock:
            records = {topic: list(samples) for topic, samples in history.items()}
        return {"physical_acceptance": False, "simulated_hardware": True,
                "robot_id": ROBOT_ID, "fault": state["fault"],
                "odom_count": state["odom_count"], "base_count": state["base_count"],
                "received": records}

    @app.post("/lab/fault")
    async def lab_fault(payload: dict):
        fault = payload.get("fault")
        if fault not in {"healthy", "sbc_down", "estop", "odom_stale", "scan_stale", "f710"}:
            return {"accepted": False}
        state['fault_injected_mono'] = time.monotonic()
        state['odom_fault_evidence'] = None
        state["fault"] = fault
        return {"accepted": True, "physical_acceptance": False}

    holder = {"bridge": None, "executor": None, "observation": None}
    lifecycle = {"state": None, "at": None}
    metrics = {"ticks": 0, "buffers": 0, "probe_issued": 0, "probe_done": 0, "probe_at": None,
               "tick_at": None, "tick_max_gap_s": 0.0, "buffer_at": None, "buffer_max_gap_s": 0.0}

    @app.get("/lab/ready")
    async def lab_ready(after: float = 0, fault: str = "healthy"):
        # Observe on the bridge executor, never infer receipt from publication.
        bridge = holder["bridge"]
        if bridge is None or holder["executor"] is None:
            return {"ready": False, "detail": "bridge not started"}
        if holder["observation"] is not None:
            return {"ready": False, "detail": "bridge executor observation pending"}
        loop = asyncio.get_running_loop()
        answer = loop.create_future()
        hub = getattr(app.state, "hub", None)
        robot = hub.robots.get(ROBOT_ID) if hub else None
        base = robot.state.base_state.data if robot else None
        server_state = {"online_capable": bool(robot and robot.state.connection == "online"
                            and "web_teleop_v1" in robot.state.capabilities),
                        "base_at": robot.state.base_state.received_mono if robot else None,
                        "reason": teleop_reason(robot.state) if robot else "robot missing",
                        "link_connected": base.link_connected if base else None}
        def observe():
            try:
                data = readiness_snapshot(bridge, node, server_state, state, lifecycle, metrics, after, fault)
            except Exception as error:
                data = {"ready": False, "detail": repr(error)}
            def deliver():
                if holder["observation"] is answer:
                    holder["observation"] = None
                if not answer.done():
                    # The server store belongs to this event loop; recheck its
                    # gate after any bridge scheduling delay before admitting.
                    if data.get("ready") and fault == "healthy":
                        current_reason = teleop_reason(robot.state)
                        data["detail"]["server_safety_reason"] = current_reason
                        data["ready"] = current_reason is None
                    answer.set_result(data)
            loop.call_soon_threadsafe(deliver)
        holder["executor"].create_task(observe)
        holder["observation"] = answer
        try:
            return await asyncio.wait_for(answer, timeout=1)
        except asyncio.TimeoutError:
            # Jazzy retains canceled queued tasks; only the answer may time out.
            return {"ready": False, "detail": "bridge executor observation timed out"}

    app.router.routes.extend(static_mounts)

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    server_thread = threading.Thread(target=lambda: server.run(sockets=[listener]), name="lab-http", daemon=True)
    resources.server = (server, server_thread)
    server_thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline and not resources.stopped.is_set():
        time.sleep(.05)
    assert server.started, "Isolated HTTP/WebSocket server did not start"

    rclpy.init(args=["--ros-args", "-p", f"robot_id:={ROBOT_ID}"],
               signal_handler_options=SignalHandlerOptions.NO)
    resources.context_shutdown = lambda: rclpy.shutdown() if rclpy.ok() else None
    node = Node("teleop_software_receiver")
    resources.nodes.append(("receiver", node))
    bridge = None
    # Production runs the bridge on its own rclpy.spin executor. Keep the lab
    # faithful: a busy receiver must not starve the bridge's clients/timers.
    executor = MultiThreadedExecutor(num_threads=2)
    resources.executors.append(("receiver", executor, None))
    bridge_executor = SingleThreadedExecutor()
    resources.executors.append(("bridge", bridge_executor, None))
    bridge_worker = None
    worker = None
    processes = resources.processes
    log = (directory / "native.log").open("w")
    resources.log = log
    base_pub = node.create_publisher(BaseState, "/patrolbot/base_state", 10)
    odom_pub = node.create_publisher(Odometry, "/odom", 10)
    manual_pub = node.create_publisher(Bool, "/patrolbot/manual_active", 10)
    joy_pub = node.create_publisher(Twist, "/input/joy", 10)

    def idle(request, response):
        response.success = True
        response.message = "Synthetic lab has no navigation action server or goal"
        return response

    node.create_service(Trigger, "/bt_navigator/navigation_idle", idle)

    def record(topic, message):
        item = {"received_mono": time.monotonic()}
        if isinstance(message, Bool):
            item["active"] = bool(message.data)
        else:
            item.update(linear=message.linear.x, angular=message.angular.z)
        with lock:
            history[topic].append(item)

    for topic in history:
        kind = Bool if topic == "/patrolbot/web_teleop_active" else Twist
        node.create_subscription(kind, topic,
            lambda message, name=topic: record(name, message), 10)

    def sensors():
        now = time.monotonic()
        if state["sensor_at"] is not None:
            state["sensor_max_gap_s"] = max(state["sensor_max_gap_s"], now - state["sensor_at"])
        state["sensor_at"] = now
        fault = state["fault"]
        stamp = node.get_clock().now().to_msg()
        if fault != "odom_stale":
            odom = Odometry()
            odom.header.stamp = stamp
            odom.header.frame_id = "odom"
            odom.child_frame_id = "base_link"
            odom.pose.pose.orientation.w = 1.0
            odom_pub.publish(odom)
            state["odom_count"] += 1
        base = BaseState()
        base.header.stamp = stamp
        base.header.frame_id = "base_link"
        base.session_generation = 17
        base.link_connected = fault != "sbc_down"
        base.hardware_state_valid = fault != "sbc_down"
        base.telemetry_age = .01
        base.motors_enabled = True  # SYNTHETIC field; no enable service exists here.
        base.estop_pressed = fault == "estop"
        base.bumpers_valid = True
        base.dock_state_valid = True
        base.dock_state = "CLEAR_CONFIRMED"
        base.charge_state_valid = True
        base.charge_state = 0
        base.odom_epoch = "00000000-0000-4000-8000-000000000017"
        base.odom_epoch_generation = 1
        base.odom_epoch_valid = True
        base.localization_recovery_required = True
        base.localization_recovery_stage = "wait-operator-seed"
        base.localization_seed_stamp_ns = 0
        base.lidar_state = "READY"
        base.lidar_scan_age = 2.0 if fault == "scan_stale" else .01
        base_pub.publish(base)
        manual_pub.publish(Bool(data=fault == "f710"))
        if fault == "f710":
            sample = Twist()
            sample.linear.x = -.12
            joy_pub.publish(sample)
        state["base_count"] += 1

    node.create_timer(.04, sensors)
    executor.add_node(node)
    resources.start_executor(0)

    # Actual native arbitration/smoothing, using the published shared limits.
    contract = read_contract()
    config = {"cmd_vel_mux": {"ros__parameters": {"use_stamped": False, "topics": {}}}}
    mapping = {"safety_controller": "/input/safety_controller", "undock_turn": "/input/undock_turn",
               "joy": "/input/joy", "teleop": "/input/teleop",
               "switch": "/input/switch", "navigation": "/input/navi"}
    for source, topic in mapping.items():
        item = contract["mux"][source]
        config["cmd_vel_mux"]["ros__parameters"]["topics"][source] = {
            "topic": topic, "timeout": item["timeout_s"], "priority": item["priority"]}
    import yaml
    mux_config = directory / "mux.yaml"
    mux_config.write_text(yaml.safe_dump(config))
    from ament_index_python.packages import get_package_share_directory, PackageNotFoundError
    smoother_config, config_evidence = resolve_smoother_config(
        get_package_share_directory, PackageNotFoundError, os.environ.get('TELEOP_LAB_SMOOTHER_CONFIG'))
    (directory / 'smoother-config.json').write_text(json.dumps(config_evidence) + '\n')
    print(json.dumps({'smoother_config': config_evidence}), flush=True)
    commands = [
        ["ros2", "run", "twist_mux", "twist_mux", "--ros-args", "-r", "__node:=cmd_vel_mux",
         "--params-file", str(mux_config)],
        ["ros2", "run", "nav2_velocity_smoother", "velocity_smoother", "--ros-args",
         "-r", "__node:=teleop_velocity_smoother", "--params-file", str(smoother_config),
         "-r", "/cmd_vel:=/cmd_vel_out", "-r", "cmd_vel_smoothed:=cmd_vel"],
    ]
    for command in commands:
        processes.append(subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                           start_new_session=True))
        (directory / "native-pids.json").write_text(json.dumps([process.pid for process in processes]))
    client = node.create_client(ChangeState, "/teleop_velocity_smoother/change_state")
    assert client.wait_for_service(timeout_sec=15), "Native smoother service missing"
    for transition in (1, 3):
        request = ChangeState.Request()
        request.transition.id = transition
        future = client.call_async(request)
        deadline = time.monotonic() + 10
        while not future.done() and time.monotonic() < deadline and not resources.stopped.is_set():
            time.sleep(.02)
        assert future.done() and future.result().success, "Native smoother activation failed"
    bridge = WebBridgeNode()
    resources.bridge = bridge
    observe_odom_fault(bridge._teleop, state)
    resources.nodes.append(("bridge", bridge))
    holder["bridge"] = bridge
    bridge_executor.add_node(bridge)
    holder["executor"] = bridge_executor
    resources.start_executor(1)

    # Diagnostics: counters prove which executor/entity still runs, and an
    # independent probe node with its own executor distinguishes a stuck
    # bridge executor from a stuck service server in the lab graph.
    session_tick = bridge._teleop.session.tick
    teleop_buffer = bridge._teleop._on_buffer

    def counting_session_tick(context):
        metrics["ticks"] += 1
        now = time.monotonic()
        if metrics["tick_at"] is not None:
            metrics["tick_max_gap_s"] = max(metrics["tick_max_gap_s"], now - metrics["tick_at"])
        metrics["tick_at"] = now
        return session_tick(context)

    def counting_buffer(topic, publisher, message, info):
        metrics["buffers"] += 1
        now = time.monotonic()
        if metrics["buffer_at"] is not None:
            metrics["buffer_max_gap_s"] = max(metrics["buffer_max_gap_s"], now - metrics["buffer_at"])
        metrics["buffer_at"] = now
        return teleop_buffer(topic, publisher, message, info)

    bridge._teleop.session.tick = counting_session_tick
    bridge._teleop._on_buffer = counting_buffer

    probe_node = Node("idle_probe")
    resources.nodes.append(("probe", probe_node))
    probe_client = probe_node.create_client(Trigger, "/bt_navigator/navigation_idle")
    lifecycle_client = probe_node.create_client(GetState, "/teleop_velocity_smoother/get_state")
    lifecycle_future = {"value": None, "at": None}
    probe_future = {"value": None}

    def probe_call():
        pending = lifecycle_future["value"]
        if pending is not None and pending.done():
            lifecycle.update(state=pending.result().current_state.id, at=lifecycle_future["at"])
            lifecycle_future["value"] = None
        if lifecycle_future["value"] is None and lifecycle_client.service_is_ready():
            lifecycle_future["at"] = time.monotonic()
            lifecycle_future["value"] = lifecycle_client.call_async(GetState.Request())
        if probe_future["value"] is not None and probe_future["value"].done():
            probe_future["value"] = None
            metrics["probe_done"] += 1
            metrics["probe_at"] = None
        if probe_future["value"] is None and probe_client.service_is_ready():
            probe_future["value"] = probe_client.call_async(Trigger.Request())
            metrics["probe_issued"] += 1
            metrics["probe_at"] = time.monotonic()

    probe_node.create_timer(.5, probe_call)
    probe_executor = SingleThreadedExecutor()
    resources.executors.append(("probe", probe_executor, None))
    probe_executor.add_node(probe_node)
    resources.start_executor(2)

    print(json.dumps({"lab_started": True, "port": PORT, "robot_id": ROBOT_ID,
                      "physical_acceptance": False, "evidence_dir": str(directory)}), flush=True)
    debug_at = 0.0
    while not resources.stopped.wait(.2):
        if any(process.poll() is not None for process in processes):
            raise RuntimeError("Native mux or smoother exited; inspect " + str(directory / "native.log"))
        if not server_thread.is_alive() or any(not worker.is_alive() for _, _, worker in resources.executors):
            raise RuntimeError("Lab HTTP or executor worker exited")
        now = time.monotonic()
        if bridge._teleop is not None and now - debug_at > 1.0:
            debug_at = now
            teleop = bridge._teleop
            print(json.dumps({"debug": {
                "idle_ready": teleop.idle.service_is_ready(),
                "idle_future_pending": teleop.idle_future is not None and not teleop.idle_future.done(),
                "idle_at_age": None if teleop.idle_at is None else round(now - teleop.idle_at, 3),
                "idle_request_age": None if teleop.idle_request_at is None else round(now - teleop.idle_request_at, 3),
                "last_idle_request_age": None if teleop.last_idle_request == float("-inf") else round(now - teleop.last_idle_request, 3),
                "session_active": teleop.session.active,
                "acknowledged": teleop.session.acknowledged,
                "ticks": metrics["ticks"],
                "buffers": metrics["buffers"],
                "probe_issued": metrics["probe_issued"],
                "probe_done": metrics["probe_done"],
                "probe_pending_age": None if metrics["probe_at"] is None else round(now - metrics["probe_at"], 2),
                "base_count": state["base_count"],
                "odom_count": state["odom_count"],
                "bridge_ages_s": {key: None if at is None else now - at for key, at in
                    zip(("base", "odom"), (teleop.state()[1], teleop.state()[3]))},
                "sensor_age_s": None if state["sensor_at"] is None else now - state["sensor_at"],
                "sensor_max_gap_s": state["sensor_max_gap_s"],
                "tick_max_gap_s": metrics["tick_max_gap_s"],
                "buffer_max_gap_s": metrics["buffer_max_gap_s"],
                "drain": {topic: [None if item[0] is None else round(now - item[0], 3),
                                 round(now - item[1], 3)] for topic, item in teleop._drain.items()},
            }}), flush=True)


def main():
    resources = LabResources()
    previous_thread, previous_unraisable = threading.excepthook, sys.unraisablehook
    def thread_error(args):
        resources.background_errors.append({'step': 'background-thread', 'error': repr(args.exc_value)})
        previous_thread(args)
    def unraisable(args):
        resources.background_errors.append({'step': 'unraisable', 'error': repr(args.exc_value)})
        previous_unraisable(args)
    threading.excepthook, sys.unraisablehook = thread_error, unraisable
    signal.signal(signal.SIGTERM, lambda *_: resources.stopped.set())
    signal.signal(signal.SIGINT, lambda *_: resources.stopped.set())
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings('error', message='coroutine .* was never awaited', category=RuntimeWarning)
            try:
                run_lab(resources)
            finally:
                if not resources.close():
                    raise RuntimeError("Isolated lab teardown failed; see teardown evidence")
    finally:
        threading.excepthook, sys.unraisablehook = previous_thread, previous_unraisable


def supervise():
    """Bound the whole run and preserve both streams, including failed startup."""
    import urllib.request

    require_isolation()
    directory = Path(tempfile.mkdtemp(prefix="teleop-lab-evidence-"))
    env = dict(os.environ, TELEOP_LAB_EVIDENCE_DIR=str(directory))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    env.update(TELEOP_LAB_PORT=str(port), TELEOP_LAB_SOCKET_FD=str(listener.fileno()))
    script = Path(__file__).resolve()
    lab = checker = None
    status = 1
    teardown = {"passed": False, "errors": ["missing lab teardown evidence"]}
    interrupted = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: interrupted.set())
    signal.signal(signal.SIGINT, lambda *_: interrupted.set())
    def cleanup_step(name, callback):
        nonlocal status
        try:
            callback()
        except Exception as error:
            print(json.dumps({"supervisor_cleanup_error": {"step": name, "error": repr(error)}}), flush=True)
            status = status or 1

    try:
        with (directory / "lab.log").open("w") as lab_log, (directory / "checker.log").open("w") as checker_log:
            try:
                lab = subprocess.Popen([sys.executable, "-u", str(script)], env=env,
                    pass_fds=(listener.fileno(),), stdout=lab_log, stderr=subprocess.STDOUT,
                    start_new_session=True)
                listener.close()
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline and not interrupted.is_set():
                    if lab.poll() is not None:
                        raise RuntimeError(f"Lab exited during startup: {lab.returncode}")
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/lab/status", timeout=.5) as response:
                            data = json.load(response)
                        assert data["robot_id"] == ROBOT_ID and data["physical_acceptance"] is False
                        break
                    except (OSError, ValueError):
                        interrupted.wait(.1)
                else:
                    raise RuntimeError("Lab HTTP startup deadline/interruption")
                checker = subprocess.Popen([sys.executable, "-u", str(script.with_name("check_teleop_receipt.py"))],
                    env=env, stdout=checker_log, stderr=subprocess.STDOUT, start_new_session=True)
                deadline = time.monotonic() + 120
                while checker.poll() is None:
                    if interrupted.wait(.1) or time.monotonic() >= deadline or lab.poll() is not None:
                        raise RuntimeError("Lab/checker interrupted, exited or exceeded run deadline")
                status = checker.returncode
            finally:
                def stop_checker():
                    if checker is not None and checker.poll() is None:
                        try:
                            os.killpg(checker.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        checker.wait(timeout=2)
                def stop_lab():
                    nonlocal status
                    if lab is None:
                        return
                    if lab.poll() is None:
                        lab.send_signal(signal.SIGTERM)
                        try:
                            lab.wait(timeout=25)
                        except subprocess.TimeoutExpired:
                            os.killpg(lab.pid, signal.SIGKILL)
                            lab.wait(timeout=2)
                            status = status or 1
                    else:
                        # An early clean exit is also a broken lab lifecycle.
                        status = status or 1
                    status = status or lab.returncode
                def check_teardown():
                    nonlocal status, teardown
                    teardown_path = directory / "teardown.json"
                    if teardown_path.exists():
                        teardown = json.loads(teardown_path.read_text())["teardown"]
                    if not teardown["passed"]:
                        status = status or 1
                cleanup_step("checker", stop_checker)
                cleanup_step("lab", stop_lab)
                # Launchers can exit while descendants keep their process group alive.
                def retire_group(pid):
                    nonlocal status
                    try:
                        os.killpg(pid, 0)
                    except ProcessLookupError:
                        return
                    status = status or 1
                    print(json.dumps({"surviving_process_group": pid, "action": "SIGKILL"}), flush=True)
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        return
                    deadline = time.monotonic() + 2
                    while True:
                        try:
                            os.killpg(pid, 0)
                        except ProcessLookupError:
                            return
                        if time.monotonic() >= deadline:
                            raise RuntimeError(f"process group survived forced retirement: {pid}")
                        time.sleep(.02)
                for process in (checker, lab):
                    if process is not None:
                        cleanup_step(f"group-{process.pid}", lambda p=process: retire_group(p.pid))
                cleanup_step("teardown-evidence", check_teardown)
                native_pids = directory / "native-pids.json"
                if native_pids.exists():
                    for pid in json.loads(native_pids.read_text()):
                        cleanup_step(f"native-{pid}", lambda p=pid: retire_group(p))
    except Exception as error:
        print(json.dumps({"supervisor_error": repr(error)}), flush=True)
        status = status or 1
    finally:
        listener.close()
        for name in ("checker.log", "lab.log", "native.log"):
            path = directory / name
            print(f"--- full {name}: {path} ---", flush=True)
            if path.exists():
                output = path.read_text(errors="replace")
                print(output, flush=True)
                issues = [line for line in output.splitlines() if
                    ('was never awaited' in line and ('RuntimeWarning' in line or 'coroutine' in line))
                    or 'Exception ignored in' in line or 'Exception in thread' in line
                    or 'exception was never retrieved' in line]
                if issues:
                    status = status or 1
                    print(json.dumps({'unexpected_background_errors': issues, 'log': name}), flush=True)
        summary = {"lab_run": {"passed": status == 0, "returncode": status,
                               "teardown": teardown, "evidence_dir": str(directory), "port": port,
                               "physical_acceptance": False}}
        (directory / "run.json").write_text(json.dumps(summary) + "\n")
        print(json.dumps(summary), flush=True)
    return status


if __name__ == "__main__":
    if sys.argv[1:] == ["--supervise"]:
        raise SystemExit(supervise())
    main()
