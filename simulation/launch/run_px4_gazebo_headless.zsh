#!/usr/bin/env zsh
# Start PX4 SITL + Gazebo X500 without an interactive Gazebo window.
set -euo pipefail

SCRIPT_DIR=${0:A:h}
PROJECT_ROOT=${SCRIPT_DIR:h:h}
PX4_ROOT=${PX4_ROOT:-${PROJECT_ROOT}/PX4-Autopilot}
PX4_ROOT=${PX4_ROOT:A}
PROFILE=${1:-base}
WORLD=${PX4_GZ_WORLD:-default}

case "$PROFILE" in
  base) TARGET=gz_x500 ;;
  vision) TARGET=gz_x500_vision ;;
  depth) TARGET=gz_x500_depth ;;
  mono-front) TARGET=gz_x500_mono_cam ;;
  mono-down) TARGET=gz_x500_mono_cam_down ;;
  lidar-down) TARGET=gz_x500_lidar_down ;;
  lidar-front) TARGET=gz_x500_lidar_front ;;
  lidar-2d) TARGET=gz_x500_lidar_2d ;;
  -h|--help)
    print "Használat: $0 [base|vision|depth|mono-front|mono-down|lidar-down|lidar-front|lidar-2d]"
    print "Választható környezet: PX4_ROOT, PX4_GZ_WORLD"
    exit 0
    ;;
  *)
    print -u2 "Ismeretlen X500 profil: $PROFILE"
    exit 2
    ;;
esac

"${SCRIPT_DIR}/validate_px4_gazebo.zsh"

if [[ -f "$PX4_ROOT/.venv/bin/activate" ]]; then
  source "$PX4_ROOT/.venv/bin/activate"
fi

export HEADLESS=1
export PX4_GZ_WORLD="$WORLD"
export CMAKE_PREFIX_PATH="$(brew --prefix qt@5):${CMAKE_PREFIX_PATH:-}"
WORLD_FILE="$PX4_ROOT/Tools/simulation/gz/worlds/$WORLD.sdf"

if [[ ! -f "$WORLD_FILE" ]]; then
  print -u2 "Nem található a Gazebo world: $WORLD_FILE"
  exit 2
fi

# Start the Gazebo server explicitly.  With HEADLESS=1, relying on the PX4 make
# target to launch it can leave PX4 alive without a world or simulated GPS.
export GZ_SIM_RESOURCE_PATH="$PX4_ROOT/Tools/simulation/gz/models:${GZ_SIM_RESOURCE_PATH:-}"
export GZ_SIM_SERVER_CONFIG_PATH="$PX4_ROOT/Tools/simulation/gz/server.config"
export PX4_GZ_STANDALONE=1

cleanup() {
  kill "$GZ_SERVER_PID" 2>/dev/null || true
}

print "Headless Gazebo szerver indítása: $WORLD"
gz sim -r -s "$WORLD_FILE" &
GZ_SERVER_PID=$!
trap cleanup EXIT INT TERM

sleep 2
if ! kill -0 "$GZ_SERVER_PID" 2>/dev/null; then
  print -u2 "A headless Gazebo szerver nem indult el."
  exit 1
fi

print "Headless PX4 SITL indítása: $TARGET a(z) $WORLD worldben"
cd "$PX4_ROOT"
make px4_sitl "$TARGET"
