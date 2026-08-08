#!/usr/bin/env zsh
# Verify prerequisites without installing or changing any local dependency.
set -euo pipefail

SCRIPT_DIR=${0:A:h}
PROJECT_ROOT=${SCRIPT_DIR:h:h:h}
PX4_ROOT=${PX4_ROOT:-${PROJECT_ROOT}/PX4-Autopilot}
PX4_ROOT=${PX4_ROOT:A}
WORLD=${PX4_GZ_WORLD:-baylands}
WORLD_FILE=${PX4_GZ_WORLD_FILE:-$PX4_ROOT/Tools/simulation/gz/worlds/${WORLD}.sdf}

typeset -i failed=0

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    print -u2 "Hiányzó parancs: $1"
    failed=1
  else
    print "Rendben: $1 ($(command -v "$1"))"
  fi
}

# Homebrew is how macOS supplies this native toolchain, so it stays required
# there. On Linux the same dependencies come from the distribution and no
# Homebrew exists; demanding it would fail a host that satisfies every actual
# prerequisite. This check is the single source of the environment contract for
# both hosts — a second, per-platform validator would drift from this one.
required_commands=(cmake ninja gz make)
if [[ "$(uname)" == "Darwin" ]]; then
  required_commands+=(brew)
fi

for command_name in $required_commands; do
  require_command "$command_name"
done

if [[ ! -d "$PX4_ROOT" ]]; then
  print -u2 "Nem található PX4 forrás: $PX4_ROOT"
  failed=1
else
  print "Rendben: PX4 forrás ($PX4_ROOT)"

  if [[ ! -f "$WORLD_FILE" ]]; then
    print -u2 "Nem található Gazebo world: $WORLD_FILE"
    failed=1
  else
    print "Rendben: Gazebo world ($WORLD_FILE)"
  fi

  if [[ ! -d "$PX4_ROOT/Tools/simulation/gz/models/x500" ]]; then
    print -u2 "Nem található a PX4 X500 modell."
    failed=1
  else
    print "Rendben: PX4 X500 modell"
  fi
fi

if command -v brew >/dev/null 2>&1 && [[ ! -d "$(brew --prefix qt@5 2>/dev/null)" ]]; then
  print -u2 "A Qt 5 Homebrew csomag nem található (brew install qt@5)."
  failed=1
fi

if (( failed )); then
  exit 1
fi

print "A natív PX4 + Gazebo előfeltételek teljesülnek."
