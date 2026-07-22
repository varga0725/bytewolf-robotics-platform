# Vision reuse audit — `codex/v1-stabilization-plan`

**Decision date:** 2026-07-21
**Compared revisions:** current `HEAD` and `codex/v1-stabilization-plan` (`af55946`)
**Rule:** this is a component-by-component audit, not permission for a branch merge.  The P0 implementation may reuse only the pieces marked below, after moving them behind the `brain/vision/` observation-only boundary and adding the stated gaps/tests.

## Decision summary

The stabilization branch contains useful, tested simulation-camera and dashboard work, but it also contains a perception-to-flight path that is incompatible with the Vision Core boundary.  Preserve the data-plane ideas, not the package layout or control-plane coupling.

| Decision | Components | P0 disposition |
| --- | --- | --- |
| **Reuse** | image encoders; colour-marker test backend; Gazebo camera-overlay renderer; read-only overlay rendering patterns | Carry forward with minimal relocation and regression tests. |
| **Wrap behind adapter** | frame contract concepts; detector contract/adapter; Gazebo transport subscriber; dashboard endpoints/UI; recorded scenario fixtures | Keep behaviour, but expose only P0 `brain.vision` contracts and runtime ports. |
| **Rewrite** | frame/result schemas; health/freshness; profile registry; runtime/reconnect/backpressure; benchmark harness; local metadata/evidence storage | Required because the existing code lacks transport identity, sequence/replay semantics, health, tracking, and P0 storage policy. |
| **Delete from Vision P0** | target estimator/reaction/approach; autonomous-approach mission scenario; target-observation command-oriented contract | Do not copy or invoke from Vision.  These form a perception-to-waypoint chain, contradicting observation-only P0. |

## Component decisions

| Existing component on stabilization branch | Decision | P0 status | Rationale and required condition |
| --- | --- | --- | --- |
| `brain/perception/camera_frame.py` | **Rewrite** (use concepts) | P0 | Its immutable encoding/shape validation is sound, but it has only `sensor_id`, capture time and optional frame ID.  P0 `CameraFrame v1` must additionally model `device_id`, `camera_id`, `stream_session_id`, monotonic `frame_sequence`, `received_at`, calibration version, payload hash, latency/drop counts, and transport validation outcomes. |
| `brain/perception/detector.py` plus `shared/schemas/perception/detection_v0_1.schema.json` and `docs/detector-contract-v0_1.md` | **Wrap behind adapter** | P0 | Reuse the backend protocol, deterministic stub, box validation and `valid/missing/stale/invalid` distinction.  Replace the public schema/result with Vision v1 carrying model/version, source-frame identity, tracker results, failure reason and health/freshness.  Never import `brain.perception` from the new public domain. |
| `brain/perception/colour_marker_backend.py` | **Reuse** | P0 | A dependency-free, content-reading backend is valuable for deterministic Gazebo and failure fixtures.  Relocate or import through the detector adapter; retain RGB-only refusal and bounds tests.  It is a test/baseline backend, not the person-detector baseline. |
| `brain/perception/jpeg_encoder.py`, `png_encoder.py` | **Reuse** | P0 | Their exact RGB validation and JPEG/PNG UI encoding are useful.  Keep them as presentation/evidence encoders only; detector input remains the original validated frame. |
| `simulation/gazebo/camera_profiles.py` and camera SDF overlays | **Wrap behind adapter** | P0 | The overlay renderer, twin-config-derived resolution and unique front/down link names solve real SITL integration issues.  Put the profile choice behind the common profile registry; generate artifacts at runtime and do not commit generated camera overlays. |
| `simulation/perception/camera_stream.py` | **Rewrite** (reuse transport knowledge) | P0 | Keep the in-process Gazebo subscription, atomic publication idea and newest-frame-wins principle.  Replace hard-coded topics/sensor names and file-only contract with a Gazebo ingest adapter that assigns sessions/sequences, validates timestamps/payload hashes, records drops/backlog, emits health, and reconnects.  Remove environment-specific Homebrew path assumptions from the domain runtime. |
| `apps/dashboard/server.py` and `apps/dashboard/web/index.html` camera/overlay portions | **Wrap behind adapter** | P0 | The existing GET-only endpoints, image display, SVG overlay and UI reconnect behaviour are directly relevant.  Rework them to consume the Vision API read model (including health, freshness and track IDs), keep Vision routes read-only, and separate them from mission/chat controls. |
| `apps/dashboard/telemetry.py`, `brain/cli/dashboard_telemetry.py`, dashboard command/mission features | **Delete from Vision P0** | P0 | Flight telemetry can be displayed by the host dashboard, but Vision Core must not depend on telemetry/command gateways or share a control API surface.  No copy into `brain/vision`. |
| `brain/perception/target_estimator.py` and `shared/schemas/perception/target_observation_v0_1.schema.json` | **Delete from Vision P0** | P0 | It couples detections to vehicle altitude, pose and target positions intended for subsequent action.  Down-camera ArUco/landing remains a future Vision profile, but not this waypoint-estimation implementation. |
| `brain/perception/target_reaction.py`, `target_approach.py` | **Delete from Vision P0** | P0 | These import `brain.mission.commands` and `brain.safety.gate` and produce an approved `WaypointCommand`; that is explicitly forbidden for Vision Core.  Retain only as historical reference on its branch. |
| `simulation/perception/autonomous_approach.py`, `target_ground_truth.py`, `gz_scene.py`, `obstacle_scenario.py`, `survey_recorder.py` | **Delete from Vision P0** (selective fixture reuse) | P0 | Do not bring flight scenarios or mission recording into the runtime.  Copy only consent-free recorded frames / Gazebo scene setup that can be made deterministic as benchmark fixtures; the new benchmark must never fly a vehicle. |
| Existing camera/detection dashboard artifacts (`simulation/artifacts/dashboard/*`) and generated SDF overlay artifacts | **Delete** | P0 | These are generated/runtime outputs, including `.tmp` files.  Regenerate in test or local runtime locations; do not treat them as source, fixtures, or evidence storage. |
| Existing tests `test_camera_frame`, `test_detector`, `test_camera_stream`, `test_camera_profiles`, encoder and dashboard tests | **Reuse as test cases** | P0 | Port assertions that test raw-frame shape, malformed input, stale results, bounds, atomic output, topic selection and overlay rendering.  Add new tests for sessions, sequence/replay, clock skew, payload hash, reconnect, health/backlog, tracking and import boundaries. |

## P0 integration sequence

1. Establish `brain/vision/` types, profile registry and architectural import test before importing any audited code.
2. Implement a new Vision frame/result contract and runtime health model.  Port only the immutable validation and fail-closed test cases from the old frame/detector code.
3. Wrap the Gazebo subscriber and camera-overlay renderer as `gazebo_simulation` adapters; add recorded-video ingest with the same contract.  The adapter owns session creation, sequencing, timestamp tolerance, replay rejection and reconnect state.
4. Wrap the detector interface; first ship deterministic and colour-marker fixtures, then YOLO/RT-DETR and BoT-SORT adapters selected by the benchmark configuration.
5. Reuse the read-only dashboard rendering patterns behind Vision API endpoints, then add local metadata/evidence-clip storage and benchmark output.  Keep full-session recording disabled by default.

## Mandatory P0 guardrails

- An import-boundary test must fail if any `brain.vision` module imports `brain.mission`, `brain.safety`, MAVSDK/PX4 modules, or creates a command/actuator type.
- Runtime output is observation data only: detections, tracks, health, benchmark records and evidence references.  It cannot return a waypoint, mission plan or flight decision.
- Generated images/SDF files and captured evidence are excluded from source control; evidence uses the configured encrypted local directory and seven-day clip retention.
- The reuse decision is revisited when each copied component passes its P0 contract, load/reconnect, and benchmark tests; a decision here does not waive those tests.
