"""Loopback-only UDP ingest inspection for the pymavlink relay (testable without live SITL)."""

from __future__ import annotations

import os
import re
from pathlib import Path

_PROC_UDP_ROW = re.compile(
    r"^\s*\d+:\s+"
    r"(?P<local_addr>[0-9A-F]{8}):(?P<local_port>[0-9A-F]{4})\s+"
    r"(?P<rem_addr>[0-9A-F]{8}):(?P<rem_port>[0-9A-F]{4})\s+"
    r"(?:\S+\s+){4}"
    r"(?P<uid>\d+)\s+(?P<timeout>\d+)\s+(?P<inode>\d+)\s+(?P<ref>\d+)",
    re.MULTILINE,
)
_SOCKET_INODE = re.compile(r"^socket:\[(\d+)\]$")
_LOOPBACK_LOCAL_IPV4 = frozenset({"0100007F", "7F000001"})


def parse_proc_udp_sockets(proc_udp_text: str) -> dict[int, tuple[str, int]]:
    """Map socket inode to (local_address_hex, local_port)."""
    sockets: dict[int, tuple[str, int]] = {}
    for match in _PROC_UDP_ROW.finditer(proc_udp_text):
        inode = int(match.group("inode"))
        local_addr = match.group("local_addr").upper()
        local_port = int(match.group("local_port"), 16)
        sockets[inode] = (local_addr, local_port)
    return sockets


def socket_inodes_for_pid(pid: int, *, fd_entries: dict[int, str] | None = None) -> set[int]:
    """Read ``socket:[inode]`` targets from ``/proc/<pid>/fd`` (or injected test fixtures)."""
    inodes: set[int] = set()
    if fd_entries is not None:
        for target in fd_entries.values():
            found = _SOCKET_INODE.match(target.strip())
            if found:
                inodes.add(int(found.group(1)))
        return inodes
    fd_dir = Path(f"/proc/{pid}/fd")
    if not fd_dir.is_dir():
        return inodes
    for entry in fd_dir.iterdir():
        try:
            target = os.readlink(entry)
        except OSError:
            continue
        found = _SOCKET_INODE.match(target)
        if found:
            inodes.add(int(found.group(1)))
    return inodes


def child_pids(pid: int) -> list[int]:
    """Best-effort child PID list without psutil."""
    children: set[int] = set()
    task_dir = Path(f"/proc/{pid}/task")
    if not task_dir.is_dir():
        return []
    for entry in task_dir.iterdir():
        children_path = entry / "children"
        try:
            raw = children_path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        for token in raw.split():
            try:
                children.add(int(token))
            except ValueError:
                continue
    return sorted(children)


def loopback_udp_ingest_from_proc(
    *,
    pids: list[int],
    ingest_port: int,
    proc_udp_text: str,
    fd_entries_by_pid: dict[int, dict[int, str]] | None = None,
) -> bool:
    """True when a listed PID holds a loopback-only UDP ingest bind on ``ingest_port``."""
    table = parse_proc_udp_sockets(proc_udp_text)
    port_hex = ingest_port
    for pid in pids:
        fd_map = None if fd_entries_by_pid is None else fd_entries_by_pid.get(pid)
        fd_map_str = {k: v for k, v in fd_map.items()} if fd_map else None
        inodes = socket_inodes_for_pid(pid, fd_entries=fd_map_str)
        for inode in inodes:
            row = table.get(inode)
            if row is None:
                continue
            local_addr, local_port = row
            if local_port != port_hex:
                continue
            if local_addr == "00000000":
                return False
            if local_addr in _LOOPBACK_LOCAL_IPV4:
                return True
            return False
    return False


def relay_uses_loopback_udp_only(process: object, *, ingest_port: int) -> bool:
    """Confirm the relay process tree listens only on loopback UDP for ingest."""
    pid = int(getattr(process, "pid", 0))
    if pid <= 0:
        return False
    if getattr(process, "poll", lambda: None)() is not None:
        return False
    try:
        import psutil
    except ModuleNotFoundError:
        proc_text = _read_proc_udp(pid)
        if proc_text is None:
            return False
        pids = [pid, *child_pids(pid)]
        return loopback_udp_ingest_from_proc(
            pids=pids,
            ingest_port=ingest_port,
            proc_udp_text=proc_text,
        )

    root = psutil.Process(pid)
    processes = [root, *root.children(recursive=True)]
    saw_ingest = False
    for candidate in processes:
        for connection in candidate.net_connections(kind="all"):
            if connection.type == 2 and connection.status == psutil.CONN_LISTEN:
                return False
            if connection.type != 1:
                continue
            host = _psutil_host(connection.laddr)
            if not host or not _is_loopback(host):
                return False
            port = _psutil_port(connection.laddr)
            if port == ingest_port:
                saw_ingest = True
    return saw_ingest


def _read_proc_udp(pid: int) -> str | None:
    path = Path(f"/proc/{pid}/net/udp")
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _psutil_host(laddr: object) -> str | None:
    host = getattr(laddr, "host", None)
    if host is not None:
        return str(host)
    if isinstance(laddr, tuple) and laddr:
        return str(laddr[0])
    return None


def _psutil_port(laddr: object) -> int | None:
    port = getattr(laddr, "port", None)
    if port is not None:
        return int(port)
    if isinstance(laddr, tuple) and len(laddr) > 1:
        return int(laddr[1])
    return None


def _is_loopback(host: str) -> bool:
    import ipaddress

    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in {"127.0.0.1", "::1", "localhost"}