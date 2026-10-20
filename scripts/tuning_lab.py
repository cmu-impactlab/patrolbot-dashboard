"""Isolated native tuning lab: real patched Nav2/AMCL nodes, never a physical base.

Run only inside the dedicated ``--network none`` test container
(``infrastructure/tuning-lab/Dockerfile``). This harness proves, from the
locally cached patched-Nav2 image:

* installed package / executable / library / local-patch provenance
  (image-internal evidence, explicitly NOT live-Pi evidence);
* the FINAL ``teleop_velocity_smoother`` actually applies the reduced patrolbot
  caps/minima/acceleration, stays proportional for a small target instead of
  creeping to the cap, reverses through zero, honours the ordinary stop
  deadline, never exceeds the configured bounds, and whether its
  ``max_velocity``/``min_velocity`` keys genuinely hot-reload (effect observed)
  or must be treated as restart-required;
* curated AMCL and Nav2/RPP candidate groups reach the native nodes through
  startup YAML and read back, each with an explicit mutability classification
  and reason. Unproven hot behaviour is classified ``restart_required`` rather
  than optimistically claimed.

No hardware, no SBC, no DDS beyond the container's loopback, ``ROS_DOMAIN_ID``
188. Synthetic ``Twist`` messages are published only inside the isolated
container; they are never a hardware input and never a motion command.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

SCHEMA_VERSION = 1
GENERATOR = "patrolbot-tuning-native-lab"
MIN_DOMAIN_ID = 188

# Bound the receipt so it stays machine-checkable and diffable.
MAX_SAMPLES_RETAINED = 4000
MAX_LIBRARIES = 24
MAX_PATCHES = 64

SMOOTHER_PARAMS = (
    "/opt/patrolbot_ws/install/patrolbot-launch/share/patrolbot-launch/param/defaults/smoother.yaml"
)
NAV2_PARAMS = (
    "/opt/patrolbot_ws/install/patrolbot_navigation/share/patrolbot_navigation/config/nav2_params.yaml"
)
PATCH_MANIFEST = "/opt/patrolbot-nav2-patches.sha256"
PATCH_DIR = "/opt/patrolbot-nav2-patches"
SOURCE_SHA_FILE = "/opt/patrolbot-source.sha256"

KEY_LIBRARIES = (
    "/opt/patrolbot_nav2/install/nav2_amcl/lib/libamcl_core.so",
    "/opt/patrolbot_nav2/install/nav2_velocity_smoother/lib/libvelocity_smoother_core.so",
    "/opt/patrolbot_nav2/install/nav2_controller/lib/libcontroller_server_core.so",
    "/opt/patrolbot_nav2/install/nav2_regulated_pure_pursuit_controller/lib/"
    "libnav2_regulated_pure_pursuit_controller.so",
)
RPP_PLUGIN_XML = (
    "/opt/patrolbot_nav2/install/nav2_regulated_pure_pursuit_controller/share/"
    "nav2_regulated_pure_pursuit_controller/nav2_regulated_pure_pursuit_controller.xml"
)
REPORTED_PACKAGES = (
    "nav2_amcl",
    "nav2_controller",
    "nav2_regulated_pure_pursuit_controller",
    "nav2_velocity_smoother",
    "nav2_behaviors",
    "nav2_costmap_2d",
    "nav2_lifecycle_manager",
)

# Curated, coherent candidate groups. Values are inside the slice-1 registry
# candidate bounds and preserve the documented dependency relationships
# (min_particles <= max_particles, z_hit + z_rand == 1.0, lookahead ordering,
# rotate_to_heading_angular_vel <= the commissioned 0.70 rad/s cap).
AMCL_CANDIDATES = {
    "alpha1": 0.11,
    "alpha4": 0.07,
    "update_min_d": 0.25,
    "update_min_a": 0.20,
    "min_particles": 1500,
    "max_particles": 3000,
    "max_beams": 90,
    "z_hit": 0.60,
    "z_rand": 0.40,
    "sigma_hit": 0.15,
    "pf_err": 0.06,
    "pf_z": 0.98,
}
RPP_CANDIDATES = {
    "desired_linear_vel": 0.18,
    "lookahead_dist": 0.40,
    "min_lookahead_dist": 0.20,
    "max_lookahead_dist": 0.65,
    "lookahead_time": 1.20,
    "min_approach_linear_velocity": 0.06,
    "approach_velocity_scaling_dist": 0.50,
    "rotate_to_heading_angular_vel": 0.60,
}

# Behavior-test bounds. These are lab-local acceptance bounds, not product values.
CURATED_LINEAR_CAP = 0.30
CURATED_ANGULAR_CAP = 0.70
CURATED_MAX_ACCEL = 1.0
CURATED_MAX_DECEL = -1.4
EXPLICIT_ZERO_STOP_DEADLINE_S = 0.55
SILENCE_TIMEOUT_STOP_DEADLINE_S = 0.95
SMALL_TARGET = 0.05
SMALL_TARGET_TOLERANCE = 0.02
BOUND_EPSILON = 1e-3

# ---------------------------------------------------------------------------
# Isolation guard. MUST run before rclpy is imported or any publisher exists.
# Nothing in this module imports ROS at import time.
# ---------------------------------------------------------------------------


def _interfaces(link_root: str | None = None) -> list[str]:
    root = Path(link_root or "/sys/class/net")
    try:
        return sorted(path.name for path in root.iterdir())
    except OSError:
        return []


def isolation_snapshot(link_root: str | None = None) -> dict:
    return {
        "patrolbot_isolated_test": os.environ.get("PATROLBOT_ISOLATED_TEST"),
        "ros_domain_id": os.environ.get("ROS_DOMAIN_ID"),
        "ros_localhost_only": os.environ.get("ROS_LOCALHOST_ONLY"),
        "ros_automatic_discovery_range": os.environ.get("ROS_AUTOMATIC_DISCOVERY_RANGE"),
        "interfaces": _interfaces(link_root),
    }


def isolation_failures(snapshot: dict) -> list[str]:
    failures: list[str] = []
    if snapshot.get("patrolbot_isolated_test") != "1":
        failures.append("PATROLBOT_ISOLATED_TEST must be 1")
    domain = snapshot.get("ros_domain_id")
    try:
        domain = int(domain)
    except (TypeError, ValueError):
        domain = None
        failures.append("ROS_DOMAIN_ID must be an integer")
    if domain is not None and domain < MIN_DOMAIN_ID:
        failures.append(f"ROS_DOMAIN_ID {domain} is below the isolated minimum {MIN_DOMAIN_ID}")
    if not str(snapshot.get("ros_localhost_only", "")).lower() in {"1", "true"}:
        failures.append("ROS_LOCALHOST_ONLY must be enabled")
    if list(snapshot.get("interfaces", [])) != ["lo"]:
        failures.append(
            "network interfaces must be exactly ['lo'], got " + repr(snapshot.get("interfaces"))
        )
    return failures


def assert_isolated(snapshot: dict | None = None) -> dict:
    """Raise before any ROS import/publisher if the sandbox is not isolated."""
    snapshot = snapshot or isolation_snapshot()
    failures = isolation_failures(snapshot)
    if failures:
        raise RuntimeError("refusing native ROS outside isolation: " + "; ".join(failures))
    return snapshot


# ---------------------------------------------------------------------------
# Small bounded helpers.
# ---------------------------------------------------------------------------


def _run(command: list[str], timeout: float = 30.0) -> dict:
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
        return {"returncode": completed.returncode,
                "stdout": completed.stdout.strip(), "stderr": completed.stderr.strip()}
    except subprocess.TimeoutExpired:
        return {"returncode": None, "stdout": "", "stderr": "timeout"}
    except OSError as error:  # pragma: no cover - defensive
        return {"returncode": None, "stdout": "", "stderr": str(error)}


def _sha256_file(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _start(command: list[str], log_handle):
    return subprocess.Popen(
        command, stdout=log_handle, stderr=subprocess.STDOUT, start_new_session=True
    )


def _stop(process: subprocess.Popen) -> None:
    if process is None or process.poll() is not None:
        return
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=8)
            return
        except subprocess.TimeoutExpired:
            continue
    try:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):  # pragma: no cover
        pass


# ---------------------------------------------------------------------------
# Inventory (image-internal). Never a live-Pi claim.
# ---------------------------------------------------------------------------


def inventory() -> dict:
    packages: dict[str, dict] = {}
    executables: dict[str, str] = {}
    prefixes: dict[str, str] = {}
    for name in REPORTED_PACKAGES:
        result = _run(["ros2", "pkg", "prefix", name])
        prefixes[name] = result["stdout"] if result["returncode"] == 0 else None
        listing = _run(["ros2", "pkg", "executables", name])
        if listing["returncode"] == 0:
            executables[name] = listing["stdout"]
    for package in _run(["ros2", "pkg", "list"])["stdout"].splitlines():
        package = package.strip()
        if package.startswith(("nav2_", "nav_2d", "opennav_")) or package in {"twist_mux"}:
            version = _run(["dpkg-query", "-W", "-f=${Version}", f"ros-jazzy-{package.replace('_', '-')}"])
            packages[package] = {
                "apt_version": version["stdout"] if version["returncode"] == 0 else None,
                "overlay_prefix": None,
            }
    for name in REPORTED_PACKAGES:
        if name in packages:
            packages[name]["overlay_prefix"] = prefixes.get(name)

    libraries = []
    observed_libraries = []
    for package, library in (
        ("nav2_amcl", "libamcl_core.so"),
        ("nav2_velocity_smoother", "libvelocity_smoother_core.so"),
        ("nav2_controller", "libcontroller_server_core.so"),
        ("nav2_regulated_pure_pursuit_controller", "libnav2_regulated_pure_pursuit_controller.so"),
    ):
        prefix = prefixes.get(package)
        if prefix:
            observed_libraries.append(str(Path(prefix) / "lib" / library))
    for path in observed_libraries[:MAX_LIBRARIES]:
        candidate = Path(path)
        entry = {"path": path, "sha256": _sha256_file(candidate)}
        try:
            entry["bytes"] = candidate.stat().st_size
        except OSError:
            entry["bytes"] = None
        libraries.append(entry)

    patches = []
    patch_root = Path(PATCH_DIR)
    if patch_root.is_dir():
        for patch in sorted(patch_root.glob("*.patch"))[:MAX_PATCHES]:
            patches.append({"name": patch.name, "sha256": _sha256_file(patch)})

    provenance = {
        "note": (
            "Image-internal provenance of the locally cached patched-Nav2 image. "
            "This is NOT live-Pi evidence and does not claim the robot's installed version."
        ),
        "nav2_upstream_archive_sha256": "bcdf575c86250d63007595c992b9c5785c3ec907e897b79abfb4fc7eacbd1129",
        "nav2_upstream_tag": "1.3.12",
        "archive_digest_evidence": "Expected from reviewed Dockerfile.nav2-fixes, not independently observed inside this image; observed library hashes and image IDs identify the tested runtime.",
        "patch_manifest_file": PATCH_MANIFEST,
        "patch_manifest_text": _read_text(Path(PATCH_MANIFEST)),
        "source_tree_sha256_text": _read_text(Path(SOURCE_SHA_FILE)),
        "patch_count": len(patches),
        "patches": patches,
        "smoother_params_file": SMOOTHER_PARAMS,
        "smoother_params_sha256": _sha256_file(Path(SMOOTHER_PARAMS)),
        "nav2_params_file": NAV2_PARAMS,
        "nav2_params_sha256": _sha256_file(Path(NAV2_PARAMS)),
    }
    return {
        "packages": packages,
        "executables": executables,
        "libraries": libraries,
        "provenance": provenance,
        "ubuntu": _read_text(Path("/etc/os-release")) or "",
    }


# ---------------------------------------------------------------------------
# Native node runtime helpers.
# ---------------------------------------------------------------------------


class LabRuntime:
    """In-process rclpy observer plus lifecycle/parameter service helpers."""

    def __init__(self):
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.node import Node

        self.rclpy = rclpy
        self.node = Node("tuning_lab_observer")
        self.executor = SingleThreadedExecutor()
        self.executor.add_node(self.node)
        import threading

        self._thread = threading.Thread(target=self.executor.spin, daemon=True)
        self._thread.start()
        self._twist_type = None

    def twist_publisher(self, topic: str):
        from geometry_msgs.msg import Twist

        self._twist_type = Twist
        return self.node.create_publisher(Twist, topic, 50)

    def twist_subscriber(self, topic: str, sink):
        from geometry_msgs.msg import Twist

        def handler(message):
            sink(time.monotonic(), float(message.linear.x), float(message.angular.z))

        return self.node.create_subscription(Twist, topic, handler, 50)

    def client(self, service_type, name: str, timeout: float = 20.0):
        client = self.node.create_client(service_type, name)
        if not client.wait_for_service(timeout_sec=timeout):
            raise RuntimeError(f"service {name} not available")
        return client

    def call(self, client, request, timeout: float = 15.0):
        future = client.call_async(request)
        deadline = time.monotonic() + timeout
        while not future.done() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not future.done():
            raise TimeoutError("service call timed out")
        return future.result()

    def transition(self, name: str, transition_id: int, timeout: float = 15.0) -> None:
        from lifecycle_msgs.srv import ChangeState

        client = self.client(ChangeState, name + "/change_state")
        request = ChangeState.Request()
        request.transition.id = transition_id
        result = self.call(client, request, timeout)
        if not result.success:
            raise RuntimeError(f"lifecycle transition {transition_id} on {name} failed")

    def get_parameters(self, node_name: str, names: list[str], timeout: float = 8.0):
        from rcl_interfaces.srv import GetParameters

        client = self.client(GetParameters, node_name + "/get_parameters")
        request = GetParameters.Request()
        request.names = list(names)
        result = self.call(client, request, timeout)
        return {name: _parameter_python(value) for name, value in zip(names, result.values)}

    def set_double_array(self, node_name: str, name: str, values: list[float], timeout: float = 8.0):
        from rcl_interfaces.msg import Parameter, ParameterType, ParameterValue
        from rcl_interfaces.srv import SetParameters

        client = self.client(SetParameters, node_name + "/set_parameters")
        parameter = Parameter()
        parameter.name = name
        value = ParameterValue()
        value.type = ParameterType.PARAMETER_DOUBLE_ARRAY
        value.double_array_value = [float(item) for item in values]
        parameter.value = value
        request = SetParameters.Request()
        request.parameters = [parameter]
        result = self.call(client, request, timeout)
        return bool(result.results and result.results[0].successful)

    def node_present(self, name: str) -> bool:
        return name in [item.lstrip("/") for item in self.node.get_node_names()]

    def shutdown(self):
        try:
            self.executor.shutdown(timeout_sec=5)
            self.node.destroy_node()
            self.rclpy.shutdown()
        except Exception:  # pragma: no cover - defensive cleanup
            pass


def _parameter_python(value):
    # rcl_interfaces/msg/ParameterType: NOT_SET=0, BOOL=1, INTEGER=2, DOUBLE=3,
    # STRING=4, BYTE_ARRAY=5, BOOL_ARRAY=6, INTEGER_ARRAY=7, DOUBLE_ARRAY=8,
    # STRING_ARRAY=9.
    kind = value.type
    if kind == 1:  # PARAMETER_BOOL
        return bool(value.bool_value)
    if kind == 2:  # PARAMETER_INTEGER
        return int(value.integer_value)
    if kind == 3:  # PARAMETER_DOUBLE
        return float(value.double_value)
    if kind == 4:  # PARAMETER_STRING
        return value.string_value
    if kind == 5:  # PARAMETER_BYTE_ARRAY
        return list(value.byte_array_value)
    if kind == 6:  # PARAMETER_BOOL_ARRAY
        return list(value.bool_array_value)
    if kind == 7:  # PARAMETER_INTEGER_ARRAY
        return list(value.integer_array_value)
    if kind == 8:  # PARAMETER_DOUBLE_ARRAY
        return list(value.double_array_value)
    if kind == 9:  # PARAMETER_STRING_ARRAY
        return list(value.string_array_value)
    return None


def _smoother_command(node_name: str, params_file: str, in_topic: str, out_topic: str) -> list[str]:
    return [
        "ros2", "run", "nav2_velocity_smoother", "velocity_smoother", "--ros-args",
        "-r", f"__node:={node_name}",
        "--params-file", params_file,
        "-r", f"cmd_vel:={in_topic}",
        "-r", f"cmd_vel_smoothed:={out_topic}",
    ]


class Collectors:
    """Bounded, timestamped Twist sample collection with phase helpers."""

    def __init__(self):
        from collections import deque

        self.samples = deque(maxlen=MAX_SAMPLES_RETAINED)

    def record(self, when: float, linear: float, angular: float) -> None:
        self.samples.append((when, linear, angular))

    def clear(self) -> None:
        self.samples.clear()

    def since(self, when: float):
        return [item for item in list(self.samples) if item[0] >= when]

    def snapshot(self):
        return list(self.samples)


def _publish(publisher, twist_type, linear: float, angular: float, duration: float,
             frequency: float = 50.0) -> None:
    end = time.monotonic() + duration
    period = 1.0 / frequency
    while time.monotonic() < end:
        message = twist_type()
        message.linear.x = float(linear)
        message.angular.z = float(angular)
        publisher.publish(message)
        time.sleep(period)


def _wait_for(predicate, timeout: float, poll: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(poll)
    return predicate()


# ---------------------------------------------------------------------------
# Analysis phases.
# ---------------------------------------------------------------------------


def _record(checks: list, check_id: str, status: str, classification: str, detail: dict) -> None:
    checks.append({
        "id": check_id,
        "status": status,
        "classification": classification,
        "detail": detail,
    })


def _close(actual, expected, tolerance: float) -> bool:
    return actual is not None and abs(float(actual) - float(expected)) <= tolerance


def _smoother_expected() -> dict:
    import yaml

    document = yaml.safe_load(Path(SMOOTHER_PARAMS).read_text())
    return document["teleop_velocity_smoother"]["ros__parameters"]


def smoother_defaults_probe(runtime: LabRuntime, log_handle, checks: list) -> dict:
    """Observe the installed smoother's own defaults by name-mismatching the YAML."""
    in_topic = "/tuning_lab/defaults_in"
    out_topic = "/tuning_lab/defaults_out"
    process = _start(
        _smoother_command("tuning_lab_defaults_probe", SMOOTHER_PARAMS, in_topic, out_topic),
        log_handle,
    )
    try:
        if not _wait_for(lambda: runtime.node_present("tuning_lab_defaults_probe"), 20.0):
            _record(checks, "vs_upstream_defaults_observed", "fail", "unverified",
                    {"reason": "defaults-probe smoother did not appear"})
            return {}
        runtime.transition("tuning_lab_defaults_probe", 1)
        values = runtime.get_parameters(
            "tuning_lab_defaults_probe", ["max_velocity", "min_velocity", "max_accel", "max_decel"]
        )
        _record(checks, "vs_upstream_defaults_observed", "pass", "inventory", {
            "note": "Installed smoother defaults with no matching params file; used only to "
                    "quantify the reduction of the patrolbot caps.",
            "max_velocity": values.get("max_velocity"),
            "max_accel": values.get("max_accel"),
        })
        return values
    except Exception as error:  # noqa: BLE001 - recorded, not raised
        _record(checks, "vs_upstream_defaults_observed", "fail", "unverified",
                {"reason": f"{type(error).__name__}: {error}"})
        return {}
    finally:
        _stop(process)


def velocity_smoother_behavior(
    runtime: LabRuntime, log_handle, checks: list, defaults: dict, collectors: Collectors
) -> None:
    in_topic = "/tuning_lab/cmd_vel_in"
    out_topic = "/tuning_lab/cmd_vel_out"
    node_name = "teleop_velocity_smoother"
    process = _start(_smoother_command(node_name, SMOOTHER_PARAMS, in_topic, out_topic), log_handle)
    publisher = runtime.twist_publisher(in_topic)
    twist_type = runtime._twist_type
    try:
        if not _wait_for(lambda: runtime.node_present(node_name), 20.0):
            _record(checks, "vs_startup_readback", "fail", "unverified",
                    {"reason": "smoother node never appeared"})
            return
        runtime.transition(node_name, 1)
        time.sleep(0.5)
        runtime.transition(node_name, 3)
        time.sleep(0.5)

        expected = _smoother_expected()
        keys = ["max_velocity", "min_velocity", "max_accel", "max_decel"]
        readback = runtime.get_parameters(node_name, keys)
        if not readback.get("max_velocity") or not readback.get("max_accel"):
            _record(checks, "vs_startup_readback", "fail", "unverified",
                    {"reason": "smoother parameters not readable after configure",
                     "readback": readback})
            return
        mismatches = {
            key: {"expected": expected[key], "readback": readback.get(key)}
            for key in keys
            if readback.get(key) is None
            or any(abs(a - b) > 1e-6 for a, b in zip(readback[key], expected[key]))
        }
        _record(
            checks, "vs_startup_readback", "pass" if not mismatches else "fail",
            "live_verified" if not mismatches else "unverified",
            {"params_file": SMOOTHER_PARAMS, "expected": {key: expected[key] for key in keys},
             "readback": readback, "mismatches": mismatches},
        )

        max_linear = float(readback["max_velocity"][0])
        min_linear = float(readback["min_velocity"][0])
        max_angular = float(readback["max_velocity"][2])
        max_accel = float(readback["max_accel"][0])
        max_decel = float(readback["max_decel"][0])
        reduced = {
            "configured_max_linear": max_linear,
            "configured_min_linear": min_linear,
            "configured_max_angular": max_angular,
            "configured_max_accel": max_accel,
            "configured_max_decel": max_decel,
            "defaults": defaults,
        }
        is_reduced = (
            _close(max_linear, CURATED_LINEAR_CAP, 1e-6)
            and _close(min_linear, -CURATED_LINEAR_CAP, 1e-6)
            and _close(max_angular, CURATED_ANGULAR_CAP, 1e-6)
            and _close(max_accel, CURATED_MAX_ACCEL, 1e-6)
            and _close(max_decel, CURATED_MAX_DECEL, 1e-6)
        )
        below_defaults = None
        if defaults.get("max_velocity"):
            below_defaults = (
                abs(max_linear) < abs(defaults["max_velocity"][0]) - 1e-9
                and abs(max_angular) < abs(defaults["max_velocity"][2]) - 1e-9
            )
        _record(checks, "vs_caps_reduced_and_symmetric", "pass" if is_reduced else "fail",
                "live_verified", {**reduced, "below_installed_defaults": below_defaults})

        # Bounds are never exceeded even for over-cap synthetic input.
        collectors.clear()
        start = time.monotonic()
        _publish(publisher, twist_type, 0.50, 1.20, 1.2)
        over = collectors.since(start)
        peak_linear = max((abs(v) for _, v, _ in over), default=0.0)
        peak_angular = max((abs(w) for _, _, w in over), default=0.0)
        bounds_ok = len(over) >= 5 and 0.25 <= peak_linear <= CURATED_LINEAR_CAP + BOUND_EPSILON and \
            0.60 <= peak_angular <= CURATED_ANGULAR_CAP + BOUND_EPSILON
        _record(checks, "vs_over_cap_input_clamped", "pass" if bounds_ok else "fail",
                "live_verified",
                {"commanded": {"linear": 0.50, "angular": 1.20}, "peak_linear": round(peak_linear, 4),
                 "peak_angular": round(peak_angular, 4),
                 "caps": {"linear": CURATED_LINEAR_CAP, "angular": CURATED_ANGULAR_CAP},
                 "samples": len(over)})

        # Proportional small target, and no creep to the cap over time. Start from
        # a settled zero so the ramp-in from the previous over-cap phase does not
        # masquerade as creep.
        _publish(publisher, twist_type, 0.0, 0.0, 0.8)
        collectors.clear()
        start = time.monotonic()
        _publish(publisher, twist_type, SMALL_TARGET, 0.0, 4.0)
        small = collectors.since(start)
        final = small[-1][1] if small else None
        early_max = max((v for when, v, _ in small if when - start < 0.6), default=0.0)
        late = [v for when, v, _ in small if when - start > 3.0]
        late_max = max(late, default=0.0)
        proportional = (
            final is not None
            and abs(final - SMALL_TARGET) <= SMALL_TARGET_TOLERANCE
            and early_max <= SMALL_TARGET + SMALL_TARGET_TOLERANCE
            and late_max <= SMALL_TARGET + SMALL_TARGET_TOLERANCE
        )
        _record(checks, "vs_proportional_small_target_no_creep", "pass" if proportional else "fail",
                "live_verified",
                {"target": SMALL_TARGET, "final_output": round(final, 4) if final is not None else None,
                 "early_max": round(early_max, 4), "late_window_max": round(late_max, 4),
                 "cap": CURATED_LINEAR_CAP, "samples": len(small)})

        # Reversal passes through zero.
        collectors.clear()
        start = time.monotonic()
        _publish(publisher, twist_type, 0.20, 0.0, 1.2)
        _publish(publisher, twist_type, -0.20, 0.0, 1.5)
        reversal = collectors.since(start)
        values = [v for _, v, _ in reversal]
        reached_negative = min(values, default=0.0) <= -0.15
        crossed = any(values[i] > 0.005 and values[i + 1] <= 0.005
                      for i in range(len(values) - 1))
        _record(checks, "vs_reversal_through_zero", "pass" if (reached_negative and crossed) else "fail",
                "live_verified",
                {"positive_target": 0.20, "negative_target": -0.20,
                 "min_output": round(min(values, default=0.0), 4),
                 "max_output": round(max(values, default=0.0), 4),
                 "crossed_zero": crossed, "samples": len(values)})

        # Acceleration bounded during a ramp from rest to the cap.
        collectors.clear()
        start = time.monotonic()
        _publish(publisher, twist_type, CURATED_LINEAR_CAP, 0.0, 2.0)
        ramp = collectors.since(start)
        peak_rate, peak_step = _peak_rate(ramp)
        # Per smoothing cycle (20 Hz) the change must stay within the configured
        # max_accel budget; the factor allows for subscriber timestamp jitter.
        step_budget = abs(max_accel) * (1.0 / 20.0) * 2.0 + 0.02
        accel_ok = len(ramp) >= 5 and peak_step > 0 and peak_step <= step_budget
        _record(checks, "vs_acceleration_bounded", "pass" if accel_ok else "fail", "live_verified",
                {"configured_max_accel": max_accel, "peak_dv_per_s": peak_rate,
                 "peak_step": peak_step, "step_budget": round(step_budget, 4),
                 "samples": len(ramp)})

        # Ordinary stop: explicit zero command respects the deadline.
        collectors.clear()
        _publish(publisher, twist_type, 0.25, 0.0, 1.0)
        zero_start = time.monotonic()
        explicit = _stop_deadline(publisher, twist_type, collectors, zero_start, keep_zero=True)
        _record(checks, "vs_stop_explicit_zero_deadline",
                "pass" if explicit is not None and explicit <= EXPLICIT_ZERO_STOP_DEADLINE_S else "fail",
                "live_verified",
                {"deadline_s": EXPLICIT_ZERO_STOP_DEADLINE_S, "observed_s": explicit})

        # Command silence: the velocity_timeout path also comes to rest.
        collectors.clear()
        _publish(publisher, twist_type, 0.25, 0.0, 1.0)
        silence_start = time.monotonic()
        silent = _stop_deadline(publisher, twist_type, collectors, silence_start, keep_zero=False)
        _record(checks, "vs_stop_silence_timeout_deadline",
                "pass" if silent is not None and silent <= SILENCE_TIMEOUT_STOP_DEADLINE_S else "fail",
                "live_verified",
                {"deadline_s": SILENCE_TIMEOUT_STOP_DEADLINE_S, "observed_s": silent})

        # Hot-reload claim: only if set-parameter is followed by an observed effect.
        effect = _hot_reload_probe(runtime, publisher, twist_type, collectors)
        _record(checks, "vs_hot_reload_max_velocity_effect",
                "pass" if effect["effect_observed"] else "fail",
                "live_verified" if effect["effect_observed"] else "restart_required", effect)
    finally:
        _stop(process)


def _peak_rate(samples):
    peak_rate = None
    peak_step = 0.0
    for (t0, v0, _), (t1, v1, _) in zip(samples, samples[1:]):
        step = abs(v1 - v0)
        peak_step = max(peak_step, step)
        dt = t1 - t0
        if 0.01 <= dt <= 0.15:
            rate = step / dt
            if peak_rate is None or rate > peak_rate:
                peak_rate = rate
    return (round(peak_rate, 4) if peak_rate is not None else None, round(peak_step, 4))


def _stop_deadline(publisher, twist_type, collectors: Collectors, start: float, keep_zero: bool):
    end = time.monotonic() + 2.5
    while time.monotonic() < end:
        if keep_zero:
            _publish(publisher, twist_type, 0.0, 0.0, 0.05)
        samples = collectors.since(start)
        if samples and abs(samples[-1][1]) <= 0.011:
            return round(samples[-1][0] - start, 4)
        time.sleep(0.01)
    return None


def _hot_reload_probe(runtime: LabRuntime, publisher, twist_type, collectors: Collectors) -> dict:
    result = {
        "param": "max_velocity",
        "requested": [0.10, 0.0, 0.30],
        "set_accepted": False,
        "readback": None,
        "peak_output": None,
        "effect_observed": False,
    }
    try:
        result["set_accepted"] = runtime.set_double_array(
            "teleop_velocity_smoother", "max_velocity", [0.10, 0.0, 0.30]
        )
        time.sleep(0.5)
        readback = runtime.get_parameters("teleop_velocity_smoother", ["max_velocity"])
        result["readback"] = readback.get("max_velocity")
        collectors.clear()
        start = time.monotonic()
        _publish(publisher, twist_type, 0.25, 0.0, 1.5)
        samples = collectors.since(start)
        peak = max((abs(v) for _, v, _ in samples), default=0.0)
        result["peak_output"] = round(peak, 4)
        result["samples"] = len(samples)
        result["effect_observed"] = (
            result["set_accepted"]
            and len(samples) >= 5
            and readback.get("max_velocity") is not None
            and _close(readback["max_velocity"][0], 0.10, 1e-6)
            and 0.08 <= peak <= 0.10 + BOUND_EPSILON
        )
        result["note"] = (
            "Effect observed: after the runtime set the clamp tightened to the new value. "
            "A bare SetParameters success without this effect is NOT a live claim."
        )
    except Exception as error:  # noqa: BLE001 - recorded as no-effect
        result["error"] = f"{type(error).__name__}: {error}"
    return result


def amcl_startup_probe(runtime: LabRuntime, evidence_dir: Path, log_handle, checks: list) -> None:
    import yaml

    params_path = evidence_dir / "amcl_startup.yaml"
    params_path.write_text(yaml.safe_dump({"amcl": {"ros__parameters": AMCL_CANDIDATES}}))
    process = _start(
        ["ros2", "run", "nav2_amcl", "amcl", "--ros-args", "-r", "__node:=amcl",
         "--params-file", str(params_path)], log_handle,
    )
    try:
        if not _wait_for(lambda: runtime.node_present("amcl"), 20.0):
            _record(checks, "amcl_startup_readback", "fail", "unverified",
                    {"reason": "amcl node never appeared"})
            return
        readback = runtime.get_parameters("amcl", list(AMCL_CANDIDATES))
        mismatches = {
            key: {"expected": AMCL_CANDIDATES[key], "readback": readback.get(key)}
            for key in AMCL_CANDIDATES
            if not _close(readback.get(key), AMCL_CANDIDATES[key], 1e-6)
        }
        _record(checks, "amcl_startup_readback", "pass" if not mismatches else "fail",
                "restart_required", {
                    "params_file": str(params_path),
                    "changed": AMCL_CANDIDATES,
                    "readback": readback,
                    "mismatches": mismatches,
                    "mutability_reason": (
                        "Startup YAML is proven to reach the real installed patched nav2_amcl "
                        "node (constructor parameter readback). Runtime hot behaviour is NOT "
                        "proven offline: alpha/particles/laser-model are consumed while building "
                        "the filter on configure, so a runtime set is not treated as live. "
                        "Classified restart_required."
                    ),
                })
    except Exception as error:  # noqa: BLE001
        _record(checks, "amcl_startup_readback", "fail", "unverified",
                {"reason": f"{type(error).__name__}: {error}"})
    finally:
        _stop(process)


def rpp_startup_probe(runtime: LabRuntime, evidence_dir: Path, log_handle, checks: list) -> None:
    import yaml

    document = yaml.safe_load(Path(NAV2_PARAMS).read_text())
    controller = document.setdefault("controller_server", {}).setdefault("ros__parameters", {})
    follow_path = controller.setdefault("FollowPath", {})
    follow_path.update(RPP_CANDIDATES)
    params_path = evidence_dir / "controller_startup.yaml"
    params_path.write_text(yaml.safe_dump(document))
    process = _start(
        ["ros2", "run", "nav2_controller", "controller_server", "--ros-args",
         "-r", "__node:=controller_server", "--params-file", str(params_path)], log_handle,
    )
    names = [f"FollowPath.{key}" for key in RPP_CANDIDATES]
    try:
        if not _wait_for(lambda: runtime.node_present("controller_server"), 20.0):
            _record(checks, "rpp_startup_readback", "fail", "unverified",
                    {"reason": "controller_server node never appeared"})
            return
        runtime.transition("controller_server", 1, timeout=40.0)
        sleep_until = time.monotonic() + 3.0
        readback = {}
        while time.monotonic() < sleep_until:
            readback = runtime.get_parameters("controller_server", names)
            if all(readback.get(name) is not None for name in names):
                break
            time.sleep(0.3)
        mismatches = {
            name: {"expected": RPP_CANDIDATES[name.split(".", 1)[1]], "readback": readback.get(name)}
            for name in names
            if not _close(readback.get(name), RPP_CANDIDATES[name.split(".", 1)[1]], 1e-6)
        }
        _record(checks, "rpp_startup_readback", "pass" if not mismatches else "fail",
                "restart_required", {
                    "params_file": str(params_path),
                    "changed": {f"FollowPath.{k}": v for k, v in RPP_CANDIDATES.items()},
                    "readback": readback,
                    "mismatches": mismatches,
                    "mutability_reason": (
                        "Startup YAML is proven to reach the configured native controller_server "
                        "and its RPP FollowPath plugin (post-configure readback). A runtime "
                        "SetParameters on the same keys is accepted and changes the stored value, "
                        "but no behavioural effect is proven offline without a full path/odom/TF "
                        "graph, so it is NOT a live claim. Classified restart_required."
                    ),
                })
        runtime_set = _run(
            ["ros2", "param", "set", "/controller_server", "FollowPath.desired_linear_vel", "0.15"]
        )
        _record(checks, "rpp_runtime_set_not_effect", "pass", "restart_required", {
            "set_accepted": runtime_set["returncode"] == 0,
            "note": (
                "SetParameters acceptance is explicitly NOT treated as hot-reload evidence. "
                "The controller caches plugin parameters at configure; no effect was measured."
            ),
        })
    except Exception as error:  # noqa: BLE001
        _record(checks, "rpp_startup_readback", "fail", "unverified",
                {"reason": f"{type(error).__name__}: {error}"})
    finally:
        _stop(process)


# ---------------------------------------------------------------------------
# Receipt assembly and entrypoint.
# ---------------------------------------------------------------------------


def summarize(checks: list) -> dict:
    passed = sum(1 for check in checks if check["status"] == "pass")
    failed = sum(1 for check in checks if check["status"] == "fail")
    classifications: dict[str, int] = {}
    for check in checks:
        classifications[check["classification"]] = classifications.get(check["classification"], 0) + 1
    return {"result": "fail" if failed else "pass", "checks": len(checks),
            "passed": passed, "failed": failed, "classifications": classifications}


def run(output: Path, evidence_dir: Path) -> dict:
    # Isolation is asserted BEFORE any ROS import or publisher exists.
    isolation = assert_isolated()
    import rclpy

    evidence_dir.mkdir(parents=True, exist_ok=True)
    checks: list = []
    receipt_isolation = {**isolation,
                         "network_none_only_loopback": isolation["interfaces"] == ["lo"]}
    _record(checks, "isolation_enforced", "pass", "inventory",
            {**receipt_isolation,
             "note": "Guard runs before any ROS import/publisher; non-isolated runs refuse."})

    inventory_data = inventory()
    _record(checks, "native_inventory", "pass", "inventory", {
        "note": inventory_data["provenance"]["note"],
        "package_count": len(inventory_data["packages"]),
        "library_count": len(inventory_data["libraries"]),
        "patch_count": inventory_data["provenance"]["patch_count"],
    })

    rclpy.init()
    runtime = LabRuntime()
    collectors = Collectors()
    runtime.twist_subscriber("/tuning_lab/cmd_vel_out", collectors.record)
    runtime.twist_subscriber("/tuning_lab/defaults_out", collectors.record)
    log_path = evidence_dir / "native.log"
    log_handle = log_path.open("w")
    try:
        defaults = smoother_defaults_probe(runtime, log_handle, checks)
        velocity_smoother_behavior(runtime, log_handle, checks, defaults, collectors)
        amcl_startup_probe(runtime, evidence_dir, log_handle, checks)
        rpp_startup_probe(runtime, evidence_dir, log_handle, checks)
    finally:
        runtime.shutdown()
        log_handle.close()

    summary = summarize(checks)
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "physical_acceptance": False,
        "live_pi_evidence": False,
        "synthetic_inputs_only": True,
        "images": {
            "lab_image_id": os.environ.get("TUNING_LAB_IMAGE_ID"),
            "lab_image_ref": os.environ.get("TUNING_LAB_IMAGE_REF"),
            "base_image_id": os.environ.get("TUNING_LAB_BASE_IMAGE_ID"),
            "base_image_ref": os.environ.get("TUNING_LAB_BASE_IMAGE_REF"),
            "note": "Exact image IDs are supplied by the host wrapper; tags alone are not evidence.",
        },
        "isolation": receipt_isolation,
        "inventory": inventory_data,        "checks": checks,
        "summary": summary,
        "limitations": [
            "Image-internal evidence only; it does not claim the robot's installed versions.",
            "No hardware, SBC, DDS-wide graph, or physical motion was involved.",
            "AMCL and Nav2/RPP mutability is conservatively restart_required.",
            "A smoother live claim, when its check passes, covers only the tested positive linear max_velocity effect, not an atomic multi-node speed activation.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2, sort_keys=False) + "\n")
    print(json.dumps({"receipt": str(output), "summary": summary,
                      "physical_acceptance": False}, indent=2))
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="/tmp/tuning-lab-evidence/receipt.json")
    parser.add_argument("--evidence-dir", default=None)
    args = parser.parse_args(argv)
    output = Path(args.output)
    evidence_dir = Path(args.evidence_dir) if args.evidence_dir else output.parent
    receipt = run(output, evidence_dir)
    return 0 if receipt["summary"]["result"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
