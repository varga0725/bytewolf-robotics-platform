#!/usr/bin/env bash
# Apply idempotent host firewall drops so only loopback can reach mavsdk_server gRPC.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DPORT_SPEC="$("${PROJECT_ROOT}/.venv/bin/python" -c "from brain.deploy.mavsdk_grpc_containment import mavsdk_grpc_dport_spec; print(mavsdk_grpc_dport_spec())")"
COMMENT="$("${PROJECT_ROOT}/.venv/bin/python" -c "from brain.deploy.mavsdk_grpc_containment import firewall_comment; print(firewall_comment())")"

IPTABLES=/usr/sbin/iptables
IP6TABLES=/usr/sbin/ip6tables

if [[ ! -x "$IPTABLES" ]]; then
  echo "Missing ${IPTABLES}; cannot apply mavsdk_server gRPC firewall containment." >&2
  echo "Install the host iptables frontend (no new apt packages required on Ubuntu server images)." >&2
  exit 2
fi

_apply_rule() {
  local tool=$1
  shift
  local family_args=("$@")
  if sudo "${tool}" -C INPUT "${family_args[@]}" -p tcp -m conntrack --ctstate NEW -m multiport \
      --dports "${DPORT_SPEC}" -m comment --comment "${COMMENT}" -j DROP 2>/dev/null; then
    echo "present  ${tool} mavsdk gRPC containment rule"
    return 0
  fi
  sudo "${tool}" -I INPUT "${family_args[@]}" -p tcp -m conntrack --ctstate NEW -m multiport \
    --dports "${DPORT_SPEC}" -m comment --comment "${COMMENT}" -j DROP
  echo "applied  ${tool} mavsdk gRPC containment rule"
}

# NEW inbound only, not arriving on loopback — avoids breaking return traffic on ephemeral ports.
_apply_rule "$IPTABLES" ! -i lo

if [[ -x "$IP6TABLES" ]]; then
  _apply_rule "$IP6TABLES" ! -i lo "!" -s ::1/128
else
  echo "skip     ${IP6TABLES} (not installed)"
fi

echo "mavsdk_server gRPC ports (${DPORT_SPEC}): remote NEW inbound denied (comment=${COMMENT})"