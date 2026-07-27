#!/usr/bin/env zsh
# Start PX4 SITL + Gazebo X500 without an interactive Gazebo window.
set -euo pipefail

SCRIPT_DIR=${0:A:h}
PROJECT_ROOT=${SCRIPT_DIR:h:h:h}
PX4_ROOT=${PX4_ROOT:-${PROJECT_ROOT}/PX4-Autopilot}
PX4_ROOT=${PX4_ROOT:A}
PROFILE=${1:-base}
WORLD=${PX4_GZ_WORLD:-baylands}

case "$PROFILE" in
  base) TARGET=gz_x500 ;;
  vision) TARGET=gz_x500_mono_cam ;;
  depth) TARGET=gz_x500_depth ;;
  mono-front) TARGET=gz_x500_mono_cam ;;
  mono-down) TARGET=gz_x500_mono_cam_down ;;
  lidar-down) TARGET=gz_x500_lidar_down ;;
  lidar-front) TARGET=gz_x500_lidar_front ;;
  lidar-2d) TARGET=gz_x500_lidar_2d ;;
  # The one airframe carrying front camera, down camera and 2D lidar at
  # once. It is an overlay rather than a PX4 model, rendered below.
  full-sensors) TARGET=gz_x500_mono_cam_down ;;
  # The real payload: the front Hawkeye and nothing else. x500_mono_cam carries
  # one camera, so the airframe is already right; what the overlay adds is the
  # twin's declared geometry for it.
  hawkeye-front) TARGET=gz_x500_mono_cam ;;
  -h|--help)
    print "Használat: $0 [base|vision|depth|mono-front|mono-down|lidar-down|lidar-front|lidar-2d|full-sensors|hawkeye-front]"
    print "Választható környezet: PX4_ROOT, PX4_GZ_WORLD, PX4_GZ_WORLD_FILE,"
    print "                      PX4_GZ_MODELS, PX4_GZ_SERVER_CONFIG"
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
WORLD_FILE=${PX4_GZ_WORLD_FILE:-$PX4_ROOT/Tools/simulation/gz/worlds/$WORLD.sdf}
WORLD_FILE=${WORLD_FILE:A}

if [[ ! -f "$WORLD_FILE" ]]; then
  print -u2 "Nem található a Gazebo world: $WORLD_FILE"
  exit 2
fi

# full-sensors is not a PX4 model: it is an overlay this project renders, which
# merges the front camera, the down camera and the 2D lidar onto one airframe.
# The interactive launcher already built it; the daemon launcher did not, so the
# only way to get all three sensors was through the launcher that stalls without
# a terminal.
PX4_STOCK_MODELS="$PX4_ROOT/Tools/simulation/gz/models"
if [[ "$PROFILE" == "full-sensors" ]]; then
  OVERLAY_ROOT="$PROJECT_ROOT/simulation/artifacts/full-sensors-overlay"
  "$PROJECT_ROOT/.venv/bin/python" -m simulation.gazebo.camera_profiles \
    --source-models "$PX4_STOCK_MODELS" --models-root "$OVERLAY_ROOT" --include-lidar-2d
  export PX4_GZ_MODELS="$OVERLAY_ROOT"
elif [[ "$PROFILE" == "hawkeye-front" ]]; then
  # BYTEWOLF_CAMERA_PIXELS trades rendered resolution for simulation rate. The
  # twin declares 4K because the Hawkeye is a 4K camera, and rendering it is
  # four times the pixels of 1080p on a machine that has already starved PX4's
  # accelerometer once. The FOV -- the part that decides what the camera can
  # see -- is unaffected by this.
  OVERLAY_ROOT="$PROJECT_ROOT/simulation/artifacts/hawkeye-front-overlay"
  HAWKEYE_ARGS=(--source-models "$PX4_STOCK_MODELS" --models-root "$OVERLAY_ROOT" --front-only)
  if [[ -n "${BYTEWOLF_CAMERA_PIXELS:-}" ]]; then
    HAWKEYE_ARGS+=(--width "${BYTEWOLF_CAMERA_PIXELS%%x*}" --height "${BYTEWOLF_CAMERA_PIXELS##*x}")
  fi
  "$PROJECT_ROOT/.venv/bin/python" -m simulation.gazebo.camera_profiles "${HAWKEYE_ARGS[@]}"
  export PX4_GZ_MODELS="$OVERLAY_ROOT"
fi

# The Baylands mesh is offset inside its world and offers no safe collision
# surface at the origin, so the vehicle spawns over the park.
#
# The height matters more than it looks. At z=2 the vehicle fell 1.7 m onto
# sloping ground and came to rest pitched 60 degrees nose-down -- which PX4
# reports as "Preflight Fail: Attitude failure (pitch)" and refuses to arm,
# with nothing in the message to suggest the vehicle is simply lying on its
# face. Hours were spent reading that as thermal throttling. Spawning just
# above the surface leaves it at 6 degrees, which is the slope itself, and it
# arms.
if [[ "$WORLD" == "baylands" ]]; then
  export PX4_GZ_MODEL_POSE=${PX4_GZ_MODEL_POSE:-205,155,0.4,0,0,0}
fi

# A fixture may override the spawned model set (PX4_GZ_MODELS) and the loaded
# Gazebo systems (PX4_GZ_SERVER_CONFIG); a wind run needs both.  PX4 only
# derives these itself in its non-standalone branch, so the launcher must be
# explicit about them here.  Overlay models come first, and PX4's own package
# stays on the path so an overlay can reference PX4's meshes without copying.
PX4_STOCK_MODELS="$PX4_ROOT/Tools/simulation/gz/models"
export PX4_GZ_MODELS="${PX4_GZ_MODELS:-$PX4_STOCK_MODELS}"
PX4_GZ_MODELS=${PX4_GZ_MODELS:A}

if [[ ! -d "$PX4_GZ_MODELS" ]]; then
  print -u2 "Nem található a Gazebo modellkönyvtár: $PX4_GZ_MODELS"
  exit 2
fi

GZ_SERVER_CONFIG=${PX4_GZ_SERVER_CONFIG:-$PX4_ROOT/Tools/simulation/gz/server.config}
GZ_SERVER_CONFIG=${GZ_SERVER_CONFIG:A}

if [[ ! -f "$GZ_SERVER_CONFIG" ]]; then
  print -u2 "Nem található a Gazebo server config: $GZ_SERVER_CONFIG"
  exit 2
fi

# Start the Gazebo server explicitly.  With HEADLESS=1, relying on the PX4 make
# target to launch it can leave PX4 alive without a world or simulated GPS.
export GZ_SIM_RESOURCE_PATH="$PX4_GZ_MODELS:$PX4_STOCK_MODELS:${GZ_SIM_RESOURCE_PATH:-}"
export GZ_SIM_SERVER_CONFIG_PATH="$GZ_SERVER_CONFIG"
# PX4's gz_bridge is launched with this interface; the server must use the same one.
export GZ_IP=127.0.0.1
export PX4_GZ_STANDALONE=1
PX4_BUILD_DIR="$PX4_ROOT/build/px4_sitl_default"
PX4_BINARY="$PX4_BUILD_DIR/bin/px4"

if [[ ! -x "$PX4_BINARY" ]]; then
  print -u2 "Nem található a lefordított PX4 SITL bináris: $PX4_BINARY"
  print -u2 "Fordítsd le egyszer a PX4 SITL-t: make px4_sitl $TARGET"
  exit 2
fi

# PX4 autosaves parameters into its working directory, so sharing one directory
# across runs lets an injected fault outlive the scenario that asked for it: a
# low-battery run left SIM_BAT_MIN_PCT=20 behind, and every later run booted with
# it and quietly flew a fault it never declared.  Each SITL therefore gets its
# own throwaway working directory and always starts from PX4's own defaults.
PX4_RUN_DIR=${PX4_RUN_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/bytewolf-px4-run.XXXXXX")}
PX4_OWNS_RUN_DIR=0
if [[ -z "${PX4_RUN_DIR_KEEP:-}" ]]; then
  PX4_OWNS_RUN_DIR=1
fi

if [[ ! -d "$PX4_RUN_DIR" ]]; then
  print -u2 "Nem hozható létre a PX4 futási könyvtár: $PX4_RUN_DIR"
  exit 1
fi

PX4_PID=0

stop_child() {
  local child_pid=$1
  if (( child_pid > 0 )) && kill -0 "$child_pid" 2>/dev/null; then
    kill -TERM "$child_pid" 2>/dev/null || true
    sleep 1
  fi
  if (( child_pid > 0 )) && kill -0 "$child_pid" 2>/dev/null; then
    kill -KILL "$child_pid" 2>/dev/null || true
  fi
}

cleanup() {
  trap - EXIT INT TERM
  stop_child "$PX4_PID"
  [[ -n "$GZ_GUI_PID" ]] && stop_child "$GZ_GUI_PID"
  stop_child "$GZ_SERVER_PID"
  wait "$PX4_PID" 2>/dev/null || true
  wait "$GZ_SERVER_PID" 2>/dev/null || true
  if (( PX4_OWNS_RUN_DIR )) && [[ -d "$PX4_RUN_DIR" ]]; then
    rm -rf "$PX4_RUN_DIR"
  fi
}

print "Headless Gazebo szerver indítása: $WORLD"
# ``-s`` alone starts a server without a rendering context, so camera sensors
# never publish image evidence.  The Vision profile needs the renderer without
# opening a GUI; Gazebo's explicit headless-rendering mode provides that.
# BYTEWOLF_GZ_GUI=1 keeps Gazebo's window open while PX4 still runs in daemon
# mode. That combination is what a human watching a flight needs, and it is not
# reachable through the interactive launcher: without a real terminal PX4's pxh
# prompt writes into an unread pipe and blocks before MAVLink comes up -- a
# 144 MB log in two minutes and no flight. Opt-in, so the regression runner's
# default path is untouched.
gz sim -r -s --headless-rendering "$WORLD_FILE" &
GZ_SERVER_PID=$!

# On macOS `gz sim` refuses to be both server and GUI in one process
# (gazebosim/gz-sim#44), so the window is a second process attaching to the
# server above. The server keeps --headless-rendering either way: that is what
# makes camera sensors publish, and it is orthogonal to whether a human is
# watching.
GZ_GUI_PID=""
if [[ -n "${BYTEWOLF_GZ_GUI:-}" ]]; then
  print "Gazebo GUI ablak indítása (BYTEWOLF_GZ_GUI); PX4 marad daemon módban."
  sleep 3
  gz sim -g &
  GZ_GUI_PID=$!
fi
trap cleanup EXIT INT TERM

sleep 2
if ! kill -0 "$GZ_SERVER_PID" 2>/dev/null; then
  print -u2 "A headless Gazebo szerver nem indult el."
  exit 1
fi

print "Headless PX4 SITL indítása: $TARGET a(z) $WORLD worldben"
# The launcher itself is run with stdout/stderr pipes by the regression runner.
# PX4's normal interactive pxh shell continuously writes prompts to such a pipe;
# once the pipe fills, PX4 blocks before MAVLink becomes available.  Daemon mode
# starts the same SITL stack without the interactive shell.
# -w is the isolation: PX4 changes into the run directory, symlinks etc/ from the
# data path it is given, and keeps its parameters and logs there.  The data path
# must therefore be the build's etc/, the same one PX4 picks for itself.
cd "$PX4_BUILD_DIR"
PX4_SIM_MODEL="$TARGET" "$PX4_BINARY" -d -w "$PX4_RUN_DIR" "$PX4_BUILD_DIR/etc" &
PX4_PID=$!
wait "$PX4_PID"
