"""Unit tests for relay loopback inspection via /proc UDP tables."""

from __future__ import annotations

import unittest

from brain.mission.acceptance_inflight_relay_inspect import (
    loopback_udp_ingest_from_proc,
    parse_proc_udp_sockets,
)


_LOOPBACK_ROW = (
    "     7: 0100007F:38D6 00000000:0000 07 00000000:00000000 00:00000000 00000000        0        0 99 2 0000000000000000 0\n"
)
_ANYADDR_ROW = (
    "     8: 00000000:38D6 00000000:0000 07 00000000:00000000 00:00000000 00000000        0        0 100 2 0000000000000000 0\n"
)
_FOREIGN_LOOPBACK_ROW = (
    "     9: 0100007F:38D6 00000000:0000 07 00000000:00000000 00:00000000 00000000        0        0 101 2 0000000000000000 0\n"
)


class RelayProcUdpTests(unittest.TestCase):
    def test_parse_local_address_not_remote(self) -> None:
        table = parse_proc_udp_sockets(_LOOPBACK_ROW)
        self.assertEqual(table[99], ("0100007F", 0x38D6))

    def test_loopback_bind_on_ingest_port_passes(self) -> None:
        proc_text = _LOOPBACK_ROW
        fd = {3: "socket:[99]"}
        self.assertTrue(
            loopback_udp_ingest_from_proc(
                pids=[4242],
                ingest_port=14550,
                proc_udp_text=proc_text,
                fd_entries_by_pid={4242: fd},
            )
        )

    def test_wildcard_bind_fails(self) -> None:
        proc_text = _ANYADDR_ROW
        fd = {3: "socket:[100]"}
        self.assertFalse(
            loopback_udp_ingest_from_proc(
                pids=[4242],
                ingest_port=14550,
                proc_udp_text=proc_text,
                fd_entries_by_pid={4242: fd},
            )
        )

    def test_foreign_pid_without_matching_fd_fails(self) -> None:
        proc_text = _FOREIGN_LOOPBACK_ROW + _LOOPBACK_ROW
        fd = {3: "socket:[99]"}
        self.assertFalse(
            loopback_udp_ingest_from_proc(
                pids=[9999],
                ingest_port=14550,
                proc_udp_text=proc_text,
                fd_entries_by_pid={4242: fd},
            )
        )


if __name__ == "__main__":
    unittest.main()