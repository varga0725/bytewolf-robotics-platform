# shellcheck shell=bash
# Apply simulation/px4/params/sitl-failsafe.params to a running PX4 SITL daemon.
# Sourced from the platform launchers after PX4 comes up; guarded in Python.
apply_bytewolf_sitl_failsafe_profile() {
  local project_root="$1"
  local px4_build_dir="$2"
  local px4_run_dir="${3:-${PX4_RUN_DIR:-}}"
  if [[ -n "${BYTEWOLF_SKIP_SITL_FAILSAFE_PROFILE:-}" ]]; then
    if [[ -n "$px4_run_dir" ]]; then
      print "skipped" >"${px4_run_dir}/sitl-failsafe-profile.status"
    fi
    return 0
  fi
  local python_bin="${project_root}/.venv/bin/python"
  if [[ ! -x "$python_bin" ]]; then
    python_bin=python3
  fi
  local -a status_args=()
  if [[ -n "$px4_run_dir" ]]; then
    status_args=(--px4-run-directory "$px4_run_dir")
  fi
  local attempt=0
  local exit_code=0
  while (( attempt < 90 )); do
    "$python_bin" -m simulation.px4.apply_sitl_failsafe_profile \
      --px4-build-directory "$px4_build_dir" "${status_args[@]}"
    exit_code=$?
    if (( exit_code == 0 )); then
      return 0
    fi
    if (( exit_code == 2 )); then
      print -u2 "SITL failsafe profile refused (SITL gate or verify); not retrying."
      if [[ -n "$px4_run_dir" && -f "${px4_run_dir}/sitl-failsafe-profile.status" ]]; then
        print -u2 "See ${px4_run_dir}/sitl-failsafe-profile.status"
      fi
      return 2
    fi
    sleep 1
    (( attempt++ )) || true
  done
  print -u2 "SITL failsafe profile did not apply within 90 s (is PX4 SITL running?)."
  if [[ -n "$px4_run_dir" && -f "${px4_run_dir}/sitl-failsafe-profile.status" ]]; then
    print -u2 "See ${px4_run_dir}/sitl-failsafe-profile.status"
  fi
  return 1
}