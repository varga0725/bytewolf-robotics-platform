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

## Scope

This is the perception path only. PX4 Collision Prevention, the roadmap's first
proposed shield, runs solely in Position mode and does nothing on the `goto_location`
mission path this project flies; the mission-path runtime shield therefore moves
to the Offboard phases (see the autonomy roadmap, 2026-07-17). The obstacle
observation produced here is the evidence that a later shield — wherever it runs
— consumes.
