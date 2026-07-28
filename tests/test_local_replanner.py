import unittest

from brain.control.contract import Velocity
from brain.navigation.local_replanner import (
    LocalReplanner,
    ReplanMode,
)


class LocalReplannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.replanner = LocalReplanner(lateral_speed_m_s=0.4)
        self.direct = Velocity(0.6, 0.0, 0.0, 0.0)

    def test_offers_right_then_left_and_keeps_the_selected_side_sticky(self) -> None:
        initial = self.replanner.candidates(self.direct)

        self.assertEqual([candidate.mode for candidate in initial], [ReplanMode.RIGHT, ReplanMode.LEFT])
        self.assertEqual(initial[0].velocity, Velocity(0.0, 0.4, 0.0, 0.0))
        self.replanner.select(ReplanMode.LEFT, now_s=1.0)

        sticky = self.replanner.candidates(self.direct)

        self.assertEqual([candidate.mode for candidate in sticky], [ReplanMode.LEFT, ReplanMode.RIGHT])
        self.assertEqual(sticky[0].velocity, Velocity(0.0, -0.4, 0.0, 0.0))

    def test_clear_direct_path_resets_the_detour_side(self) -> None:
        self.replanner.select(ReplanMode.RIGHT, now_s=1.0)

        self.replanner.clear()

        self.assertEqual(self.replanner.mode, ReplanMode.DIRECT)
        self.assertEqual(self.replanner.candidates(self.direct)[0].mode, ReplanMode.RIGHT)

    def test_invalid_speed_is_refused(self) -> None:
        for speed in (0.0, -0.1, float("nan"), float("inf")):
            with self.subTest(speed=speed):
                with self.assertRaises(ValueError):
                    LocalReplanner(lateral_speed_m_s=speed)

    def test_detour_waits_for_stop_and_then_expires(self) -> None:
        self.replanner.note_blocked(now_s=2.0, stop_duration_s=0.8)

        self.assertFalse(self.replanner.ready(now_s=2.7))
        self.assertTrue(self.replanner.ready(now_s=2.81))
        self.replanner.select(ReplanMode.RIGHT, now_s=2.81)
        self.assertFalse(self.replanner.exhausted(now_s=14.7))
        self.assertTrue(self.replanner.exhausted(now_s=14.82))

    def test_direct_resume_requires_the_configured_bypass_offset(self) -> None:
        self.replanner.select(ReplanMode.RIGHT, now_s=1.0, east_error_m=5.0)
        self.assertFalse(self.replanner.may_resume_direct(east_error_m=2.1))
        self.assertTrue(self.replanner.may_resume_direct(east_error_m=2.0))


if __name__ == "__main__":
    unittest.main()
