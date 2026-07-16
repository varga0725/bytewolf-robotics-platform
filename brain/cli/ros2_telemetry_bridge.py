"""Run the optional read-only MAVSDK to ROS 2 telemetry bridge."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path
import signal

from brain.cli.mavsdk_lifecycle import stop_owned_mavsdk_server
from robots.drone.x500v2.ros2.bridge_runtime import TelemetryBridgeRuntime
from robots.drone.x500v2.ros2.telemetry_adapter import create_ros2_telemetry_node


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Relay MAVSDK telemetry to ROS 2 and the read-only local dashboard."
    )
    parser.add_argument("--endpoint", default="udpin://0.0.0.0:14540")
    parser.add_argument("--mavsdk-server-port", type=int, default=50051)
    parser.add_argument(
        "--connection-timeout",
        type=float,
        default=15.0,
        help="Maximum seconds to wait for PX4 discovery before this telemetry-only bridge stops.",
    )
    parser.add_argument(
        "--dashboard-snapshot",
        type=Path,
        default=Path("simulation/artifacts/dashboard/live-telemetry.json"),
    )
    return parser.parse_args(arguments)


async def run(arguments: argparse.Namespace) -> None:
    """Own all optional bridge resources until SIGINT/SIGTERM requests shutdown."""
    try:
        import rclpy
        from mavsdk import System
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "The live telemetry bridge requires ROS 2 Humble and MAVSDK; use Ubuntu Humble."
        ) from error

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except NotImplementedError:
            pass

    system = System(port=arguments.mavsdk_server_port)
    runtime = TelemetryBridgeRuntime(
        vehicle=system,
        ros_client=rclpy,
        node_factory=create_ros2_telemetry_node,
        destination=arguments.dashboard_snapshot,
        endpoint=arguments.endpoint,
        connection_timeout=arguments.connection_timeout,
    )
    try:
        await runtime.run(stop_event)
    finally:
        stop_owned_mavsdk_server(system)


def main(arguments: Sequence[str] | None = None) -> None:
    asyncio.run(run(parse_arguments(arguments)))


if __name__ == "__main__":
    main()
