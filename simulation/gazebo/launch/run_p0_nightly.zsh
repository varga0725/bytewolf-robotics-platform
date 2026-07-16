#!/usr/bin/env zsh
# Run the reproducible Apple Silicon P0 release gate and retain its JSON evidence.
set -euo pipefail

SCRIPT_DIR=${0:A:h}
PROJECT_ROOT=${SCRIPT_DIR:h:h:h}
cd "$PROJECT_ROOT"

./simulation/gazebo/launch/validate_px4_gazebo.zsh
.venv/bin/python -m simulation.scenarios.scenarios --runs 10
