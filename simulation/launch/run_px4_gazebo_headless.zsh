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

print "Headless PX4 SITL indítása: $TARGET a(z) $WORLD worldben"
cd "$PX4_ROOT"
exec make px4_sitl "$TARGET"
