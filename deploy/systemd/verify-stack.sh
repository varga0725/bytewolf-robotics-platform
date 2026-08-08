#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=/home/bytewolf/bytewolf-robotics/platform
API_ROOT=http://127.0.0.1:8080
units=(
  bytewolf-sitl.service
  bytewolf-telemetry.service
  bytewolf-camera-front.service
  bytewolf-camera-down.service
  bytewolf-world-map.service
  bytewolf-yolo.service
  bytewolf-controlroom.service
  bytewolf.target
)

for unit in "${units[@]}"; do
  systemctl is-active --quiet "$unit"
  echo "active  $unit"
done

cd "$PROJECT_ROOT"
.venv/bin/python - "$API_ROOT" <<'PY'
from datetime import datetime, timezone
import json
import sys
from urllib.request import urlopen

root = sys.argv[1]

def get(path: str) -> tuple[bytes, str]:
    with urlopen(root + path, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f"{path}: HTTP {response.status}")
        return response.read(), response.headers.get_content_type()

telemetry_raw, _ = get("/api/v1/telemetry")
telemetry = json.loads(telemetry_raw)
captured = datetime.fromisoformat(telemetry["captured_at"].replace("Z", "+00:00"))
age = (datetime.now(timezone.utc) - captured.astimezone(timezone.utc)).total_seconds()
if not -5 <= age <= 5:
    raise RuntimeError(f"telemetry is not live: age={age:.1f}s")

for sensor in ("front", "down"):
    frame, content_type = get(f"/api/v1/cameras/{sensor}")
    if content_type != "image/jpeg" or not frame.startswith(b"\xff\xd8"):
        raise RuntimeError(f"{sensor} camera did not return a JPEG frame")
    detections_raw, _ = get(f"/api/v1/cameras/{sensor}/detections")
    detections = json.loads(detections_raw)
    if detections.get("contract_version") != "v0.1" or detections.get("validity") != "valid":
        raise RuntimeError(f"{sensor} detections are not a valid v0.1 observation")
    observed = datetime.fromisoformat(detections["captured_at"].replace("Z", "+00:00"))
    detection_age = (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()
    if not -1 <= detection_age <= float(detections["max_age_s"]):
        raise RuntimeError(f"{sensor} detections are stale: age={detection_age:.2f}s")

world_raw, _ = get("/api/v1/world-map")
world = json.loads(world_raw)
if world.get("occupancy_only") is not True or not isinstance(world.get("cells"), list):
    raise RuntimeError("world map does not match the occupancy-only read model")

get("/api/v1/safety-envelope")
get("/control-room/")
get("/")
print(f"live telemetry age: {age:.2f}s")
print(f"world-map cells: {len(world['cells'])}")
print("HTTP, cameras, detections, safety envelope and frontends: healthy")
PY
