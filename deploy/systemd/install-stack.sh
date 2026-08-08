#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=/home/bytewolf/bytewolf-robotics/platform
UNIT_ROOT="$PROJECT_ROOT/deploy/systemd"
SYSTEMD_ROOT=/etc/systemd/system

if [[ "$(id -un)" != "bytewolf" ]]; then
  echo "Run this installer as the bytewolf user; it uses sudo only for system state." >&2
  exit 2
fi

required=(
  "$PROJECT_ROOT/.venv/bin/python"
  "$PROJECT_ROOT/.venv-ros/bin/python"
  /usr/bin/python3.10
  /home/bytewolf/bytewolf-robotics/PX4-Autopilot/build/px4_sitl_default/bin/px4
  /usr/bin/gz
  /usr/bin/npm
  /usr/bin/systemctl
  /usr/bin/tailscale
)
for path in "${required[@]}"; do
  if [[ ! -e "$path" ]]; then
    echo "Missing deployment prerequisite: $path" >&2
    exit 2
  fi
done

cd "$PROJECT_ROOT"

# Refuse a drifted simulator before replacing the running stack.  A service
# that starts is not evidence if its PX4/Gazebo baseline changed underneath it.
.venv/bin/python -m simulation.baseline

# Gazebo's Jammy bindings and generated messages are tested against their
# matching system packages.  -s prevents a newer user-site protobuf used by
# MAVSDK from shadowing Ubuntu's compatible package.
PYTHONPATH="$PROJECT_ROOT" /usr/bin/python3.10 -s -c \
  'import PIL, gz.transport13, gz.msgs10.image_pb2, simulation.perception.camera_stream'

npm --prefix apps/dashboard/frontend run build
npm --prefix apps/marketing/frontend run build

sudo systemd-analyze verify "$UNIT_ROOT"/bytewolf.target "$UNIT_ROOT"/bytewolf-*.service
sudo install -m 0644 "$UNIT_ROOT"/bytewolf.target "$SYSTEMD_ROOT/bytewolf.target"
for unit in "$UNIT_ROOT"/bytewolf-*.service; do
  sudo install -m 0644 "$unit" "$SYSTEMD_ROOT/$(basename "$unit")"
done

sudo systemctl daemon-reload
sudo systemctl enable bytewolf.target
sudo systemctl restart bytewolf.target

# The backend remains loopback-only. Tailscale Serve terminates HTTPS and
# injects the signed-in tailnet identity; the API allowlist enforces operator
# authorization on every remotely served endpoint.
sudo tailscale serve --bg --https=443 http://127.0.0.1:8080
echo "ByteWolf stack installed locally and on its authenticated Tailscale HTTPS URL."
echo "Verify with: deploy/systemd/verify-stack.sh"
