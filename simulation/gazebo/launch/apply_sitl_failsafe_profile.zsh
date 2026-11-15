# shellcheck shell=bash
# Apply simulation/px4/params/sitl-failsafe.params to a running PX4 SITL daemon.
# Sourced from the platform launchers after PX4 comes up; guarded in Python.
apply_bytewolf_sitl_failsafe_profile() {
  local project_root="$1"
  local px4_build_dir="$2"
  if [[ -n "${BYTEWOLF_SKIP_SITL_FAILSAFE_PROFILE:-}" ]]; then
    return 0
  fi
  local python_bin="${project_root}/.venv/bin/python"
  if [[ ! -x "$python_bin" ]]; then
  python_bin=python3
  fi
  local attempt=0
  while (( attempt < 90 )); do
    if "$python_bin" -m simulation.px4.apply_sitl_failsafe_profile \
      --px4-build-directory "$px4_build_dir"; then
      return 0
    fi
    sleep 1
    (( attempt++ )) || true
  done
  print -u2 "SITL failsafe profile did not apply within 90 s (is PX4 SITL running?)."
  return 1
}