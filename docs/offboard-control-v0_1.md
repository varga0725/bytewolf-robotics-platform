# Offboard control contract v0.1

The safety-reviewed boundary a continuous control stream must pass before it
could ever influence flight. It is **disabled**, and this document is as much
about what it deliberately does not do as about what it does.

## Why this exists

The autonomy roadmap measured its own assumption and found it wrong. PX4
Collision Prevention runs only in Position mode — in v1.17 the `CollisionPrevention`
class is used by `FlightTaskManualPosition` and `StickAccelerationXY` and by no
Auto/Mission/Hold task at all. This project's missions fly with MAVSDK
`goto_location`, which is Hold/Auto. A capability probe confirmed the
consequence directly: with `CP_DIST=5` the vehicle reached **0.36 m** from the
obstacle without intervening.

So the mission path has no runtime shield, and PX4 will not provide one. The
shield has to be ours, and a shield can only act through a continuous setpoint
stream. That stream is the thing this contract guards. Nothing in the roadmap's
Phase D — MPC, CBF, any runtime filter — can exist until the boundary it acts
through is defined and trusted.

## What it is not

- **Not a second way to fly a mission.** The mission path keeps its own adapter,
  its own gate and its own human approval. This is a velocity stream, nothing else.
- **Not a widening of the telemetry bridge.** `brain/telemetry` and the ROS 2
  bridge stay read-only. A static test asserts that no read-only package imports
  `brain.control`.
- **Not active.** `safety.offboard.enabled` is `false` in `twin.yaml`, and a
  session runs in shadow mode unless that flag *and* an explicit constructor
  argument both say otherwise. Two switches, so neither alone is a single point
  of failure.

## The contract

`shared/schemas/control/offboard_setpoint_v0_1.schema.json`. One streamed
velocity setpoint, with every field that decides obeyability mandatory:

| Field | Why it is required |
| --- | --- |
| `vehicle_id` | A setpoint meant for another vehicle is refused, not adapted. |
| `stream_id` | One continuous session. Sequence numbers only compare within a stream. |
| `sequence` | Strictly increasing, so a late duplicate cannot reverse a newer command. |
| `issued_at` | RFC 3339 with offset. Age is measured from when the command was *computed*, so a slow link cannot hide behind a fresh transmit time. |
| `ttl_s` | How long it may be obeyed. A producer may ask for less than policy; more is refused. |
| `frame` | `local_ned` or `body_frd`, stated not assumed. The wrong one is a sign error in a movement command. |
| `kind` | `velocity` only. Trajectory is absent rather than declared-but-unvalidated. |
| `validity` | A producer that does not trust its own result says so. |
| `velocity` | All four components mandatory; an omitted axis would default to a silent zero, which is itself a command. |
| `uncertainty_m_s` | Optional — and omitting it is therefore a claim of confidence. |

The contract carries no arm, mode, actuator or motor field, and
`unevaluatedProperties: false` keeps it that way whatever a producer appends.

## The limits

All in `twin.yaml` under `safety.offboard`, because a second copy of a limit is
a limit that can drift looser. Speed is deliberately **not** there: the boundary
reads `safety.max_speed_m_s`, the vehicle's one speed limit.

```yaml
safety:
  offboard:
    enabled: false
    frame: local_ned
    max_setpoint_ttl_s: 0.5
    max_rate_hz: 50
    max_acceleration_m_s2: 2.0     # conservative envelope, NOT a measured airframe figure
    max_yaw_rate_deg_s: 45
    max_uncertainty_m_s: 1.0
    watchdog_timeout_s: 1.0
    fallback_sequence: [zero_velocity, hold, land]
```

The loader refuses an incoherent policy rather than running with it: a
`watchdog_timeout_s` shorter than `max_setpoint_ttl_s`, a fallback that does not
begin with `zero_velocity`, an unknown step, a repeated step, an unknown frame,
or a half-written block with any limit missing.

`max_acceleration_m_s2` is an envelope, not a capability claim. No X500 V2
dynamics have been measured — every physical parameter in `twin.yaml` is still
`provenance: simulated`.

## The two clocks

This is the heart of the design.

```
issued_at ──── ttl_s (0.5 s) ────► setpoint EXPIRED
          ──── watchdog_timeout_s (1.0 s) ────► stream STOPPED → fallback
```

A setpoint stops being obeyable strictly **before** the stream that produced it
is declared dead. Between the two the state is `EXPIRED`: nothing is commanded,
but a late producer can still recover. The profile loader enforces the ordering,
because reversing it would leave the vehicle flying a command the watchdog had
already given up on.

The dangerous failure of a control stream is not a bad command but *silence* —
the producer crashes and the vehicle keeps flying the last thing it heard. A
dead producer sends nothing to raise the alarm with, which is why `poll()`
exists and why a **rejected** setpoint does not restart the clock: a producer
flooding the boundary with refused commands must not look alive.

## What the boundary refuses

Rejection, never correction. A setpoint over the speed limit is not clamped and
forwarded — a producer that misunderstands the envelope would keep doing so,
invisibly, for as long as its illegal requests were quietly made legal.

`offboard_disabled` · `wrong_vehicle` · `wrong_frame` · `producer_declared_invalid` ·
`expired` · `ttl_exceeds_policy` · `superseded_stream` · `out_of_order` ·
`rate_exceeds_policy` · `speed_exceeds_limit` · `yaw_rate_exceeds_limit` ·
`acceleration_exceeds_limit` · `uncertainty_exceeds_limit`

Named reasons, not free text, so a scenario can assert the specific rule it
provoked and an audit record says which one fired.

Two are worth spelling out. The speed limit applies to the **composed**
magnitude: 2.5 m/s on two axes is 3.54 m/s, past a 3 m/s limit neither component
breaks alone. And the acceleration check compares consecutive accepted setpoints:
both endpoints can sit inside the speed limit while the step between them is one
no airframe can follow.

## Shadow mode

The default, and the reason this can exist before the architecture review the
roadmap requires. Every setpoint is validated, every rejection counted by
reason, every fallback decided — and the adapter is never called. That produces
the intervention-rate and rejection evidence the roadmap asks for without a
single command leaving the process.

`SessionRecord.as_document()` is the artifact shape: offered, approved,
delivered, rejections by reason, and the fallbacks that fired.

## Status and what is not claimed

**Proof level: unit/contract.** 58 deterministic tests, no PX4, no Gazebo, no
MAVSDK.

Not done, and not claimed:

- **No SITL scenario.** The watchdog fallback has not been exercised against a
  real PX4. That is the next step and it needs the architecture review first.
- **No adapter implementation.** `VelocityAdapter` is a Protocol with one
  method; nothing implements it against MAVSDK yet. That is deliberate — the
  boundary is being trusted before it is given something to command.
- **No shield.** This is the boundary a shield would act *through*, not the
  shield. Phase D is still open.
- **The fallback is a decision, not an execution.** The watchdog returns the
  ordered sequence; nothing here performs it. And a stopped process commands
  nothing at all — PX4's own failsafe remains the authority for that case, as it
  does everywhere else in this platform.

## Next gates

1. Architecture review against the autonomy roadmap (roadmap gate, required
   before active control).
2. A MAVSDK offboard adapter behind `VelocityAdapter`.
3. Headless SITL: interrupt the stream mid-flight, verify PX4 enters the
   documented safe mode, record the artifact.
4. Only then Phase D — MPC and CBF — with shadow-mode KPIs before anything is
   allowed to act.
