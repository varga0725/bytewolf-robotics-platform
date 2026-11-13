"""Render trajectory evidence plots for demo missions (headless-safe)."""

from __future__ import annotations

from pathlib import Path


def render_demo_trajectory_png(
    *,
    positions: list[dict[str, object]],
    mission_items: list[dict[str, object]],
    output_path: Path,
) -> Path:
    """Write a top-down XY plot and altitude-vs-time chart."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    north = [float(sample.get("north_m", 0.0)) for sample in positions]
    east = [float(sample.get("east_m", 0.0)) for sample in positions]
    altitudes = [float(sample.get("relative_altitude_m", 0.0)) for sample in positions]
    times = list(range(len(altitudes)))

    waypoint_north = [
        float(item["north_m"]) for item in mission_items if item.get("kind") == "waypoint"
    ]
    waypoint_east = [
        float(item["east_m"]) for item in mission_items if item.get("kind") == "waypoint"
    ]

    figure, axes = plt.subplots(1, 2, figsize=(10, 4.5))
    axes[0].plot(east, north, color="#2563eb", linewidth=1.5, label="flight path")
    if waypoint_east:
        axes[0].scatter(waypoint_east, waypoint_north, color="#dc2626", marker="x", label="waypoints")
    axes[0].scatter([0.0], [0.0], color="#16a34a", marker="o", label="home")
    axes[0].set_xlabel("East (m)")
    axes[0].set_ylabel("North (m)")
    axes[0].set_title("Top-down trajectory")
    axes[0].axis("equal")
    axes[0].grid(True, alpha=0.3)
    axes[0].legend(loc="best", fontsize=8)

    axes[1].plot(times, altitudes, color="#7c3aed", linewidth=1.5)
    axes[1].set_xlabel("Sample index (~1 Hz)")
    axes[1].set_ylabel("Relative altitude (m)")
    axes[1].set_title("Altitude profile")
    axes[1].grid(True, alpha=0.3)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.tight_layout()
    figure.savefig(output_path, dpi=120)
    plt.close(figure)
    return output_path
