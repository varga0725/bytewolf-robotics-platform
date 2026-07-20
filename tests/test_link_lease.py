"""One process owns the PX4 endpoint at a time, and says so out loud.

The bridge and a flying mission both need `udpin://0.0.0.0:14540`, and only one
can bind it. The lease decides which — it grants no authority over the vehicle,
it only says who currently holds the socket.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from brain.telemetry.link_lease import (
    claim_link,
    lease_link,
    link_is_leased,
    read_lease,
    release_link,
    wait_for_free_link,
)


class LinkLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.path = Path(self.directory.name) / "mavlink-link.lease"

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_an_unclaimed_link_is_free(self) -> None:
        self.assertFalse(link_is_leased(self.path))
        self.assertIsNone(read_lease(self.path))

    def test_a_claim_names_its_owner_and_process(self) -> None:
        claim_link("mission", path=self.path)

        lease = read_lease(self.path)
        assert lease is not None
        self.assertEqual(lease["owner"], "mission")
        self.assertEqual(lease["pid"], os.getpid())

    def test_the_lease_is_given_back_even_when_the_mission_raises(self) -> None:
        """A lease that outlives its mission leaves the dashboard blind.

        Releasing only on success would mean any crashed flight silently
        stopped telemetry until someone deleted a file.
        """
        with self.assertRaises(RuntimeError):
            with lease_link("mission", path=self.path):
                self.assertTrue(link_is_leased(self.path))
                raise RuntimeError("the flight failed")

        self.assertFalse(link_is_leased(self.path))

    def test_a_lease_from_a_dead_process_is_litter_not_a_lease(self) -> None:
        """A crash must not hold the link forever."""
        self.path.write_text(json.dumps({"owner": "mission", "pid": 999_999}))

        self.assertFalse(link_is_leased(self.path))

    def test_a_process_group_is_never_mistaken_for_an_owner(self) -> None:
        """Signal 0 to pid 0 addresses this whole process group and succeeds.

        Read naively that makes a zero-pid lease look permanently live, which
        would hold the dashboard's link forever on a malformed file.
        """
        self.path.write_text(json.dumps({"owner": "mission", "pid": 0}))

        self.assertFalse(link_is_leased(self.path))

    def test_an_unreadable_lease_file_does_not_hold_the_link(self) -> None:
        self.path.write_text("{ this is not json")

        self.assertFalse(link_is_leased(self.path))

    def test_releasing_a_link_nobody_holds_is_not_an_error(self) -> None:
        release_link(self.path)

        self.assertFalse(link_is_leased(self.path))

    def test_waiting_reports_a_link_that_never_frees_instead_of_hanging(self) -> None:
        """MAVSDK's server does not retry a failed bind.

        Claiming the lease only asks the holder to leave; a mission that starts
        before it has left dies on "Address already in use" and never flies. So
        the wait has to end in an answer, not in a hope.
        """
        import socket

        held = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        held.bind(("0.0.0.0", 0))
        port = held.getsockname()[1]
        try:
            self.assertFalse(wait_for_free_link(port=port, timeout_s=0.0, sleep=lambda _s: None))
        finally:
            held.close()

    def test_waiting_succeeds_once_nothing_holds_the_port(self) -> None:
        import socket

        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.bind(("0.0.0.0", 0))
        port = probe.getsockname()[1]
        probe.close()

        self.assertTrue(wait_for_free_link(port=port, timeout_s=1.0, sleep=lambda _s: None))

    def test_no_temporary_file_is_left_beside_the_lease(self) -> None:
        claim_link("mission", path=self.path)

        self.assertEqual([entry.name for entry in self.path.parent.iterdir()], [self.path.name])


if __name__ == "__main__":
    unittest.main()
