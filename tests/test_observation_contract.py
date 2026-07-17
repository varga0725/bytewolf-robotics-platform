"""Coverage for the versioned observation contract.

The contract's job is to stop a consumer from acting on something it should not
trust, so these tests are mostly about refusal: wrong frame, absent origin, a
sector that cannot speak, an observation too old to use.
"""

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import unittest

from brain.telemetry.observation import (
    OBSERVATION_CONTRACT_VERSION,
    ObservationContractError,
    ObservationState,
    load_observation,
    validate_observation_document,
)


EXAMPLES = Path(__file__).resolve().parents[1] / "shared/interfaces/observation/examples"
_OBSERVED_AT = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


def _observation(**overrides: object) -> dict:
    document = {
        "contract_version": OBSERVATION_CONTRACT_VERSION,
        "kind": "battery",
        "vehicle_id": "x500v2_reference_01",
        "observed_at": "2026-07-17T12:00:00Z",
        "max_age_s": 5.0,
        "validity": "valid",
        "payload": {"remaining_percent": 87.5},
    }
    return {**document, **overrides}


class FixtureTests(unittest.TestCase):
    def test_every_valid_fixture_is_accepted(self) -> None:
        fixtures = sorted((EXAMPLES / "valid").glob("*.json"))

        self.assertTrue(fixtures, "the contract needs valid fixtures to be worth anything")
        for path in fixtures:
            with self.subTest(fixture=path.name):
                load_observation(json.loads(path.read_text(encoding="utf-8")))

    def test_every_invalid_fixture_is_refused(self) -> None:
        fixtures = sorted((EXAMPLES / "invalid").glob("*.json"))

        self.assertTrue(fixtures, "a contract that refuses nothing guarantees nothing")
        for path in fixtures:
            with self.subTest(fixture=path.name):
                with self.assertRaises(ObservationContractError):
                    load_observation(json.loads(path.read_text(encoding="utf-8")))

    def test_each_family_has_a_valid_and_an_invalid_fixture(self) -> None:
        def kinds(directory: str) -> set[str]:
            return {
                json.loads(path.read_text(encoding="utf-8"))["kind"]
                for path in (EXAMPLES / directory).glob("*.json")
            }

        for family in ("position", "velocity", "obstacle"):
            with self.subTest(family=family):
                self.assertIn(family, kinds("valid"))
                self.assertIn(family, kinds("invalid"))


class ValidityStateTests(unittest.TestCase):
    def test_a_fresh_trusted_observation_is_usable(self) -> None:
        observation = load_observation(_observation())

        self.assertEqual(observation.state(_OBSERVED_AT), ObservationState.VALID)
        self.assertTrue(observation.state(_OBSERVED_AT).usable)
        self.assertEqual(observation.usable_payload(_OBSERVED_AT)["remaining_percent"], 87.5)

    def test_an_observation_older_than_its_own_limit_is_stale(self) -> None:
        """Staleness is derived, because only the consumer knows the time now."""
        observation = load_observation(_observation(max_age_s=5.0))

        self.assertEqual(observation.state(_OBSERVED_AT + timedelta(seconds=4.9)), ObservationState.VALID)
        self.assertEqual(observation.state(_OBSERVED_AT + timedelta(seconds=5.1)), ObservationState.STALE)

    def test_the_four_states_are_distinguishable(self) -> None:
        cases = {
            ObservationState.VALID: (_observation(), _OBSERVED_AT),
            ObservationState.INVALID: (_observation(validity="invalid"), _OBSERVED_AT),
            ObservationState.MISSING: ({k: v for k, v in _observation(validity="missing").items() if k != "payload"}, _OBSERVED_AT),
            ObservationState.STALE: (_observation(), _OBSERVED_AT + timedelta(hours=1)),
        }

        for expected, (document, now) in cases.items():
            with self.subTest(state=expected.value):
                self.assertEqual(load_observation(document).state(now), expected)

    def test_nothing_but_a_valid_observation_yields_a_payload(self) -> None:
        for state, document, now in (
            ("invalid", _observation(validity="invalid"), _OBSERVED_AT),
            ("stale", _observation(), _OBSERVED_AT + timedelta(hours=1)),
        ):
            with self.subTest(state=state), self.assertRaisesRegex(ObservationContractError, state):
                load_observation(document).usable_payload(now)

    def test_age_never_runs_backwards_on_a_clock_step(self) -> None:
        observation = load_observation(_observation())

        self.assertEqual(observation.age_s(_OBSERVED_AT - timedelta(seconds=30)), 0.0)

    def test_a_naive_now_cannot_measure_an_age(self) -> None:
        observation = load_observation(_observation())

        with self.assertRaisesRegex(ObservationContractError, "timezone-aware"):
            observation.state(datetime(2026, 7, 17, 12, 0))


class ContractBoundaryTests(unittest.TestCase):
    def test_a_document_from_another_contract_version_is_refused(self) -> None:
        """A consumer must not guess at fields whose meaning it does not know."""
        with self.assertRaises(ObservationContractError):
            validate_observation_document(_observation(contract_version="v0.2"))

    def test_a_control_field_cannot_ride_along_on_an_observation(self) -> None:
        """Observations describe; they never command."""
        with self.assertRaises(ObservationContractError):
            validate_observation_document(_observation(setpoint_velocity_m_s=1.5))

        with self.assertRaises(ObservationContractError):
            validate_observation_document(
                _observation(kind="battery", payload={"remaining_percent": 87.5, "arm": True})
            )

    def test_a_missing_observation_cannot_carry_a_payload(self) -> None:
        """A zeroed payload would read as a measurement rather than as absence."""
        with self.assertRaises(ObservationContractError):
            validate_observation_document(_observation(validity="missing"))

    def test_a_timestamp_without_an_offset_is_refused(self) -> None:
        with self.assertRaises(ObservationContractError):
            load_observation(_observation(observed_at="2026-07-17T12:00:00"))

    def test_an_observation_must_declare_how_long_it_lasts(self) -> None:
        document = _observation()
        del document["max_age_s"]

        with self.assertRaises(ObservationContractError):
            validate_observation_document(document)

        for bad_age in (0, -1):
            with self.subTest(max_age_s=bad_age), self.assertRaises(ObservationContractError):
                validate_observation_document(_observation(max_age_s=bad_age))


class FrameTests(unittest.TestCase):
    _ORIGIN = {"latitude_deg": 47.397971, "longitude_deg": 8.546164, "absolute_altitude_m": 488.0}

    def _position(self, payload: dict) -> dict:
        return _observation(kind="position", payload=payload)

    def test_enu_axes_are_refused_in_a_ned_frame(self) -> None:
        """A frame mismatch must be a schema error, not a silent sign error."""
        with self.assertRaises(ObservationContractError):
            validate_observation_document(
                self._position({"frame": "ned", "origin": self._ORIGIN, "east_m": 1.0, "north_m": 2.0, "up_m": 3.0})
            )

    def test_a_local_position_without_its_origin_is_refused(self) -> None:
        with self.assertRaises(ObservationContractError):
            validate_observation_document(
                self._position({"frame": "ned", "north_m": 1.0, "east_m": 2.0, "down_m": -3.0})
            )

    def test_both_local_frames_are_expressible(self) -> None:
        for payload in (
            {"frame": "ned", "origin": self._ORIGIN, "north_m": 1.0, "east_m": 2.0, "down_m": -3.0},
            {"frame": "enu", "origin": self._ORIGIN, "east_m": 2.0, "north_m": 1.0, "up_m": 3.0},
        ):
            with self.subTest(frame=payload["frame"]):
                validate_observation_document(self._position(payload))

    def test_velocity_cannot_be_expressed_in_degrees(self) -> None:
        with self.assertRaises(ObservationContractError):
            validate_observation_document(
                _observation(kind="velocity", payload={"frame": "wgs84", "north_m_s": 1.0})
            )

    def test_an_unknown_frame_is_refused(self) -> None:
        with self.assertRaises(ObservationContractError):
            validate_observation_document(self._position({"frame": "body", "north_m": 1.0}))


class ObstacleCoverageTests(unittest.TestCase):
    _SENSOR = {"id": "lidar_2d", "min_range_m": 0.2, "max_range_m": 12.0}

    def _obstacle(self, sectors: list[dict]) -> dict:
        return _observation(
            kind="obstacle", payload={"frame": "body_frd", "sensor": self._SENSOR, "sectors": sectors}
        )

    def test_a_sector_must_say_whether_it_can_speak_at_all(self) -> None:
        with self.assertRaises(ObservationContractError):
            validate_observation_document(self._obstacle([{"yaw_deg": 0, "width_deg": 15}]))

    def test_an_unobserved_sector_cannot_carry_a_distance(self) -> None:
        """It would invite reading max_range_m as an obstacle, or as clear space."""
        for coverage in ("unobserved", "clear"):
            with self.subTest(coverage=coverage), self.assertRaises(ObservationContractError):
                validate_observation_document(
                    self._obstacle([{"yaw_deg": 0, "width_deg": 15, "coverage": coverage, "distance_m": 12.0}])
                )

    def test_a_measured_sector_must_carry_its_distance(self) -> None:
        with self.assertRaises(ObservationContractError):
            validate_observation_document(
                self._obstacle([{"yaw_deg": 0, "width_deg": 15, "coverage": "measured"}])
            )

    def test_the_three_coverages_are_expressible_side_by_side(self) -> None:
        validate_observation_document(
            self._obstacle(
                [
                    {"yaw_deg": 0, "width_deg": 15, "coverage": "measured", "distance_m": 3.4, "confidence": 0.9},
                    {"yaw_deg": 15, "width_deg": 15, "coverage": "clear"},
                    {"yaw_deg": 30, "width_deg": 15, "coverage": "unobserved"},
                ]
            )
        )

    def test_an_obstacle_observation_reports_on_something(self) -> None:
        with self.assertRaises(ObservationContractError):
            validate_observation_document(self._obstacle([]))


if __name__ == "__main__":
    unittest.main()
