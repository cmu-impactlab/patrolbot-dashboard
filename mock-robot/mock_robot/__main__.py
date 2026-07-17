from __future__ import annotations

import argparse
import asyncio
import logging

from .client import MockRobot


def main() -> None:
    parser = argparse.ArgumentParser(description="ROS-free simulated PatrolBot")
    parser.add_argument("--server", default="ws://localhost:8000/ws/robot")
    parser.add_argument("--token", default="dev-token")
    parser.add_argument("--robot-id", default="patrolbot-01")
    parser.add_argument("--scenario", choices=["full", "calm", "chaos"], default="full")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    robot = MockRobot(args.server, args.token, args.robot_id, args.scenario)
    try:
        asyncio.run(robot.run_forever())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
