# Lidar obstacle perception

The first slice of the autonomy roadmap's Phase B perception path:
`brain/perception/lidar_obstacle.py` turns a planar laser scan into an
`obstacle` observation, and nothing else. It emits data; it never commands,
never touches an actuator, and never decides to move. The output is a plain
observation document that the caller passes through
`brain.telemetry.observation.load_observation`, so the same schema that guards
every other observation guards this one — the adapter is never trusted on its
own say-so.

## What the sensor can and cannot see

Measured from `lidar_2d_v2` in PX4's model tree, not assumed:

- **270° field of view** (−135° to +135°), 1080 samples, 0.1–30 m range.
- The **90° behind the vehicle is a blind spot.** Those sectors come out
  `unobserved` on every scan — never `clear`. Under the roadmap's rule "no
  coverage means no movement in that direction", this is the contract saying the
  vehicle must not move backward on lidar evidence, because it cannot see there.

## The three things a sector can say

| Coverage | Meaning | Carries a distance? |
| --- | --- | --- |
| `measured` | an obstacle returned within range | yes — the **nearest** return |
| `clear` | the sensor swept this bearing and saw nothing to `max_range_m` | no |
| `unobserved` | no beam pointed here — outside the FOV, or a gap | no |

`unobserved` is not `clear`. A return at or past `max_range_m` is read as
`clear` ("nothing to the edge"), not as an obstacle sitting on the edge. The
distance is the nearest return in the sector, because the closest obstacle is
the one that constrains motion.

## Frames: the sign is flipped on purpose

A gz laser scan measures angle **counter-clockwise** from the vehicle's forward
axis. The obstacle contract frame is body forward-right-down, with yaw
**clockwise** seen from above. The two run in opposite directions, so a beam at
gz `+θ` (to the left) becomes a sector at `−θ` yaw. An obstacle to the left
lands on a negative-yaw sector; to the right, positive.

This mapping is pinned by unit tests, but a unit test only proves internal
consistency. The direction is confirmed against ground truth by the headless
obstacle scenario, where an obstacle at a known bearing must appear on the
matching sector. The project has already paid once for trusting a frame by its
label instead of its ground truth — the wind fixture was labelled north while
Gazebo's ENU frame blew it east — so the mapping is asserted, not assumed.

## Evidence

Two headless scenarios turn the adapter into recorded evidence, each producing a
durable artifact under `simulation/artifacts/perception/`.

**Obstacle scenario** (`simulation.perception.obstacle_scenario`, gate G2). Drives
a real `gz_x500_lidar_2d` SITL, places a box at a known bearing, captures many
scans, and scores what the adapter saw. It passes only if the obstacle is
detected on nearly every scan, on the right sector, at the right distance, with
the rear blind spot unobserved throughout. First artifact: 30/30 scans detected
the box ahead at 4.38 m against a placed 4.4 m, 0 % false-negative. This is also
where the gz-to-FRD frame sign is confirmed against ground truth — a front box
lands on yaw 0, a left box on yaw −90.

**Collision Prevention baseline** (`simulation.perception.collision_prevention_baseline`,
gate G3). Enables PX4 Collision Prevention (`CP_DIST=5`) and flies a
`goto_location` mission straight at an obstacle, recording the closest approach
from Gazebo ground truth. If CP shielded the flight the vehicle would hold near
5 m; it closed to **0.36 m**. CP did nothing, because it runs only in Position
mode while the project flies Auto/Hold.

## Scope

This is the perception path plus the measured limit of the PX4 baseline shield.
The CP baseline above settles gate G3: the mission-path runtime shield cannot be
PX4 Collision Prevention, so it moves to the Offboard phases (autonomy roadmap,
2026-07-17). The obstacle observation produced here is the evidence that a later
shield — wherever it runs — consumes.
