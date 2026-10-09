#!/usr/bin/env python3
"""Check packaged maps against canonical robot content before coordinated release."""
import argparse
import hashlib
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("map_catalog", ROOT / "server/app/telemetry/map_catalog.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def verify(robot_root):
    robot_root = Path(robot_root)
    canonical = module.MapCatalog(robot_root / "ros2_ws/src/patrolbot_navigation/maps")
    packaged = module.MapCatalog(ROOT / "server/maps/catalog")
    for key, expected in canonical.maps.items():
        actual = packaged.require(key, expected["revision"])
        if {k:v for k,v in actual.items() if k != "path"} != {k:v for k,v in expected.items() if k != "path"}:
            raise ValueError("catalog metadata differs: " + key)
    shared = robot_root / "ros2_ws/src/patrolbot_bridge/patrolbot_bridge/map_catalog.py"
    if shared.read_bytes() != (ROOT / "server/app/telemetry/map_catalog.py").read_bytes():
        raise ValueError("catalog validator differs from canonical source")
    manifest = ROOT / "server/maps/catalog/catalog.json"
    print("map catalog parity verified: sha256:" + hashlib.sha256(manifest.read_bytes()).hexdigest())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--robot-root", type=Path, default=ROOT.parent / "patrolbot-repo")
    verify(parser.parse_args().robot_root)
