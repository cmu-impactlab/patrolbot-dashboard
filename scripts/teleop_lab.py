"""Isolated software lab: real ROS receiver/mux/smoother, never a physical base.

Run only inside the dedicated network-none test container. Synthetic hardware
exists here, never in application startup or production readiness code.
"""
from __future__ import annotations

import asyncio
from collections import deque
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time


ROBOT_ID = "isolated-web-teleop"
PORT = 8027


def main():
    assert os.environ.get("PATROLBOT_ISOLATED_TEST") == "1"
    assert os.environ.get("ROS_DOMAIN_ID") == "188"
    assert {path.name for path in Path("/sys/class/net").iterdir()} == {"lo"}
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
    from std_msgs.msg import Bool
    from std_srvs.srv import Trigger
    import uvicorn
    from app.main import create_app
    from app.settings import Settings

    directory = Path(tempfile.mkdtemp(prefix="teleop-software-lab-"))
    history = {topic: deque(maxlen=4000) for topic in (
        "/input/teleop", "/patrolbot/web_teleop_active", "/cmd_vel_out", "/cmd_vel")}
    lock = threading.Lock()
    state = {"fault": "healthy", "odom_count": 0, "base_count": 0}
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
        state["fault"] = fault
        return {"accepted": True, "physical_acceptance": False}

    holder = {"bridge": None}

    @app.get("/lab/ready")
    async def lab_ready():
        hub = getattr(app.state, "hub", None)
        robot = hub.robots.get(ROBOT_ID) if hub else None
        base = robot.state.base_state.data if robot else None
        bridge = holder["bridge"]
        teleop = getattr(bridge, "_teleop", None) if bridge is not None else None
        idle_age = None if teleop is None or teleop.idle_at is None else time.monotonic() - teleop.idle_at
        ready = bool(robot and robot.state.connection == "online" and base
                     and base.raw_odom_age_s is not None
                     and "web_teleop_v1" in robot.state.capabilities
                     and idle_age is not None and 0 <= idle_age <= .4)
        return {"ready": ready, "physical_acceptance": False,
                "has_amcl_pose": bool(robot and robot.state.pose.data is not None),
                "idle_age": idle_age}

    app.router.routes.extend(static_mounts)

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(.05)
    assert server.started, "Isolated HTTP/WebSocket server did not start"

    rclpy.init(args=["--ros-args", "-p", f"robot_id:={ROBOT_ID}"])
    node = Node("teleop_software_receiver")
    bridge = None
    # Production runs the bridge on its own rclpy.spin executor. Keep the lab
    # faithful: a busy receiver must not starve the bridge's clients/timers.
    executor = MultiThreadedExecutor(num_threads=2)
    bridge_executor = SingleThreadedExecutor()
    bridge_worker = None
    worker = None
    processes = []
    log = (directory / "native.log").open("w")
    try:
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
        worker = threading.Thread(target=executor.spin, daemon=True)
        worker.start()

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
        smoother_config = Path("/opt/patrolbot/ros2_ws/src/patrolbot-launch/param/defaults/smoother.yaml")
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
        client = node.create_client(ChangeState, "/teleop_velocity_smoother/change_state")
        assert client.wait_for_service(timeout_sec=15), "Native smoother service missing"
        for transition in (1, 3):
            request = ChangeState.Request()
            request.transition.id = transition
            future = client.call_async(request)
            deadline = time.monotonic() + 10
            while not future.done() and time.monotonic() < deadline:
                time.sleep(.02)
            assert future.done() and future.result().success, "Native smoother activation failed"
        bridge = WebBridgeNode()
        bridge_executor.add_node(bridge)
        bridge_worker = threading.Thread(target=bridge_executor.spin, daemon=True)
        bridge_worker.start()

        # Diagnostics: counters prove which executor/entity still runs, and an
        # independent probe node with its own executor distinguishes a stuck
        # bridge executor from a stuck service server in the lab graph.
        metrics = {"ticks": 0, "buffers": 0, "probe_issued": 0, "probe_done": 0, "probe_at": None}
        session_tick = bridge._teleop.session.tick
        teleop_buffer = bridge._teleop._on_buffer

        def counting_session_tick(context):
            metrics["ticks"] += 1
            return session_tick(context)

        def counting_buffer(topic, publisher, message, info):
            metrics["buffers"] += 1
            return teleop_buffer(topic, publisher, message, info)

        bridge._teleop.session.tick = counting_session_tick
        bridge._teleop._on_buffer = counting_buffer

        probe_node = Node("idle_probe")
        probe_client = probe_node.create_client(Trigger, "/bt_navigator/navigation_idle")
        probe_future = {"value": None}

        def probe_call():
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
        probe_executor.add_node(probe_node)
        threading.Thread(target=probe_executor.spin, daemon=True).start()

        print(json.dumps({"lab_ready": True, "port": PORT, "robot_id": ROBOT_ID,
                          "physical_acceptance": False, "evidence_dir": str(directory)}), flush=True)
        stopped = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stopped.set())
        signal.signal(signal.SIGINT, lambda *_: stopped.set())
        debug_at = 0.0
        while not stopped.wait(.2):
            if any(process.poll() is not None for process in processes):
                raise RuntimeError("Native mux or smoother exited; inspect " + str(directory / "native.log"))
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
                    "drain": {topic: [None if item[0] is None else round(now - item[0], 3),
                                     round(now - item[1], 3)] for topic, item in teleop._drain.items()},
                }}), flush=True)
    finally:
        if bridge is not None:
            if bridge._teleop is not None:
                bridge._teleop.shutdown()
            bridge.ws.stop()
        bridge_executor.shutdown(timeout_sec=5)
        if bridge_worker is not None:
            bridge_worker.join(timeout=5)
        executor.shutdown(timeout_sec=5)
        if worker is not None:
            worker.join(timeout=5)
        if bridge is not None:
            bridge_executor.remove_node(bridge)
            bridge.destroy_node()
        executor.remove_node(node)
        node.destroy_node()
        for process in processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGINT)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=5)
        server.should_exit = True
        server_thread.join(timeout=10)
        log.close()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
