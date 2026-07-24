#!/usr/bin/env zsh
# Start one PX4 SITL + Gazebo Harmonic X500 profile from the project root.
set -euo pipefail

SCRIPT_DIR=${0:A:h}
PROJECT_ROOT=${SCRIPT_DIR:h:h:h}
PX4_ROOT=${PX4_ROOT:-${PROJECT_ROOT}/PX4-Autopilot}
PX4_ROOT=${PX4_ROOT:A}
PROFILE=${1:-base}
WORLD=${PX4_GZ_WORLD:-baylands}

case "$PROFILE" in
  base) TARGET=gz_x500 ;;
  vision) TARGET=gz_x500_vision ;;
  depth) TARGET=gz_x500_depth ;;
  mono-front) TARGET=gz_x500_mono_cam ;;
  mono-down) TARGET=gz_x500_mono_cam_down ;;
  full-sensors) TARGET=gz_x500_mono_cam_down ;;
  lidar-down) TARGET=gz_x500_lidar_down ;;
  lidar-front) TARGET=gz_x500_lidar_front ;;
  lidar-2d) TARGET=gz_x500_lidar_2d ;;
  -h|--help)
    print "Használat: $0 [base|vision|depth|mono-front|mono-down|full-sensors|lidar-down|lidar-front|lidar-2d]"
    print "Választható környezet: PX4_ROOT, PX4_GZ_WORLD"
    exit 0
    ;;
  *)
    print -u2 "Ismeretlen X500 profil: $PROFILE"
    exit 2
    ;;
esac

"${SCRIPT_DIR}/validate_px4_gazebo.zsh"

PX4_STOCK_MODELS="$PX4_ROOT/Tools/simulation/gz/models"
if [[ "$PROFILE" == "full-sensors" ]]; then
  OVERLAY_ROOT="$PROJECT_ROOT/simulation/artifacts/full-sensors-overlay"
  "$PROJECT_ROOT/.venv/bin/python" -m simulation.gazebo.camera_profiles \
    --source-models "$PX4_STOCK_MODELS" --models-root "$OVERLAY_ROOT" --include-lidar-2d
  export PX4_GZ_MODELS="$OVERLAY_ROOT"
fi

if [[ -f "$PX4_ROOT/.venv/bin/activate" ]]; then
  source "$PX4_ROOT/.venv/bin/activate"
fi

export PX4_GZ_WORLD="$WORLD"
# The Baylands mesh is offset inside the world and does not provide a safe
# collision surface at world origin.  Spawn above the park, unless an operator
# explicitly supplied a scenario-specific pose.
if [[ "$WORLD" == "baylands" ]]; then
  export PX4_GZ_MODEL_POSE=${PX4_GZ_MODEL_POSE:-205,155,2,0,0,0}
fi
export CMAKE_PREFIX_PATH="$(brew --prefix qt@5):${CMAKE_PREFIX_PATH:-}"
export PX4_GZ_MODELS="${PX4_GZ_MODELS:-$PX4_STOCK_MODELS}"
PX4_GZ_MODELS=${PX4_GZ_MODELS:A}
export GZ_SIM_RESOURCE_PATH="$PX4_GZ_MODELS:$PX4_STOCK_MODELS:${GZ_SIM_RESOURCE_PATH:-}"

print "PX4 SITL indítása: $TARGET a(z) $WORLD worldben"

# PX4's stock `gz_env.sh` unconditionally replaces PX4_GZ_MODELS when it
# starts Gazebo itself.  The full profile has generated, project-owned model
# files, so start Gazebo here and use PX4's standalone path; the exact overlay
# that was rendered above remains authoritative.
if [[ "$PROFILE" == "full-sensors" ]]; then
  # Load PX4's server plugins (including the camera sensor system), then put
  # our generated model directory back in front because gz_env.sh hardcodes
  # PX4_GZ_MODELS to the stock tree.
  : "${GZ_SIM_SYSTEM_PLUGIN_PATH:=}"
  export GZ_SIM_SYSTEM_PLUGIN_PATH
  source "$PX4_ROOT/build/px4_sitl_default/rootfs/gz_env.sh"
  export PX4_GZ_MODELS="$OVERLAY_ROOT"
  export GZ_SIM_RESOURCE_PATH="$PX4_GZ_MODELS:$PX4_STOCK_MODELS:${GZ_SIM_RESOURCE_PATH:-}"
  GZ_IP=127.0.0.1 gz sim --verbose=1 -r -s "$PX4_ROOT/Tools/simulation/gz/worlds/$WORLD.sdf" &
  GZ_SERVER_PID=$!
  if [[ -z "${HEADLESS:-}" ]]; then
    GZ_IP=127.0.0.1 gz sim -g &
    GZ_GUI_PID=$!
  fi
  PX4_BUILD_DIR="$PX4_ROOT/build/px4_sitl_default"
  PX4_BINARY="$PX4_BUILD_DIR/bin/px4"
  PX4_RUN_DIR=${PX4_RUN_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/bytewolf-px4-run.XXXXXX")}
  PX4_OWNS_RUN_DIR=0
  if [[ -z "${PX4_RUN_DIR_KEEP:-}" ]]; then
    PX4_OWNS_RUN_DIR=1
  fi
  PX4_PID=0
  cleanup_full_sensors() {
    if (( PX4_PID > 0 )) && kill -0 "$PX4_PID" 2>/dev/null; then
      kill -TERM "$PX4_PID" 2>/dev/null || true
      wait "$PX4_PID" 2>/dev/null || true
    fi
    kill "$GZ_SERVER_PID" 2>/dev/null || true
    [[ -n "${GZ_GUI_PID:-}" ]] && kill "$GZ_GUI_PID" 2>/dev/null || true
    if (( PX4_OWNS_RUN_DIR )) && [[ -d "$PX4_RUN_DIR" ]]; then
      rm -rf "$PX4_RUN_DIR"
    fi
  }
  trap cleanup_full_sensors EXIT INT TERM
  sleep 2
  cd "$PX4_BUILD_DIR"
  PX4_GZ_STANDALONE=1 PX4_SYS_AUTOSTART=4014 PX4_SIM_MODEL="$TARGET" GZ_IP=127.0.0.1 \
    "$PX4_BINARY" -d -w "$PX4_RUN_DIR" "$PX4_BUILD_DIR/etc" &
  PX4_PID=$!
  wait "$PX4_PID"
  exit $?
fi

cd "$PX4_ROOT"
exec make px4_sitl "$TARGET"
