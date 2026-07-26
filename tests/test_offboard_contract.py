"""The Offboard setpoint contract: what it accepts, and what it refuses.

A setpoint is a command, so nearly every test here is about refusal. The
fixture pairs under shared/interfaces/control/examples are the specification: a
valid document must load, and every invalid one must be rejected for the reason
its filename states.
"""

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import unittest

from brain.control.contract import (
    OFFBOARD_CONTRACT_VERSION,
    OffboardContractError,
    SetpointFrame,
    SetpointState,
    load_setpoint,
)


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "shared/interfaces/control/examples"
ISSUED_AT = datetime(2026, 7, 26, 12, 0, 0, 100000, tzinfo=UTC)


def _document(**overrides) -> dict:
    document = {
        "contract_version": "v0.1",
        "vehicle_id": "x500v2_reference_01",
        "stream_id": "stream-a",
        "sequence": 7,
        "issued_at": ISSUED_AT.isoformat().replace("+00:00", "Z"),
        "ttl_s": 0.2,
        "frame": "local_ned",
        "kind": "velocity",
        "validity": "valid",
        "velocity": {"x_m_s": 1.0, "y_m_s": 0.0, "z_m_s": 0.0, "yaw_rate_deg_s": 0.0},
    }
    document.update(overrides)
    return document


class FixtureTests(unittest.TestCase):
    """The example documents are the contract's own specification."""

    def test_every_valid_example_loads(self) -> None:
        paths = sorted((EXAMPLES / "valid").glob("*.json"))
        self.assertTrue(paths, "the valid example directory is empty")
        for path in paths:
            with self.subTest(example=path.name):
                setpoint = load_setpoint(json.loads(path.read_text(encoding="utf-8")))
                self.assertEqual(setpoint.vehicle_id, "x500v2_reference_01")

    def test_every_invalid_example_is_refused(self) -> None:
        paths = sorted((EXAMPLES / "invalid").glob("*.json"))
        self.assertTrue(paths, "the invalid example directory is empty")
        for path in paths:
            with self.subTest(example=path.name), self.assertRaises(OffboardContractError):
                load_setpoint(json.loads(path.read_text(encoding="utf-8")))


class ContractShapeTests(unittest.TestCase):
    def test_it_reads_a_well_formed_setpoint(self) -> None:
        setpoint = load_setpoint(_document(uncertainty_m_s=0.3))

        self.assertEqual(setpoint.stream_id, "stream-a")
        self.assertEqual(setpoint.sequence, 7)
        self.assertIs(setpoint.frame, SetpointFrame.LOCAL_NED)
        self.assertEqual(setpoint.uncertainty_m_s, 0.3)
        self.assertEqual(setpoint.issued_at, ISSUED_AT)

    def test_an_absent_uncertainty_stays_absent(self) -> None:
        # Not defaulted to zero: a producer that said nothing has not claimed
        # perfect confidence, and inventing one here would make the boundary's
        # uncertainty limit unenforceable.
        self.assertIsNone(load_setpoint(_document()).uncertainty_m_s)

    def test_the_contract_version_is_pinned(self) -> None:
        self.assertEqual(OFFBOARD_CONTRACT_VERSION, "v0.1")
        with self.assertRaises(OffboardContractError):
            load_setpoint(_document(contract_version="v0.2"))

    def test_speed_is_the_magnitude_not_one_axis(self) -> None:
        # Three components each inside the limit can compose a speed past it.
        setpoint = load_setpoint(
            _document(velocity={"x_m_s": 3.0, "y_m_s": 4.0, "z_m_s": 0.0, "yaw_rate_deg_s": 0.0})
        )
        self.assertAlmostEqual(setpoint.velocity.speed_m_s, 5.0)

    def test_a_document_carrying_an_actuator_field_is_refused(self) -> None:
        # unevaluatedProperties keeps the command surface exactly as narrow as
        # it was designed to be, whatever a producer tries to append.
        for extra in ({"arm": True}, {"mode": "OFFBOARD"}, {"motor_pwm": [1500] * 4}):
            with self.subTest(extra=extra), self.assertRaises(OffboardContractError):
                load_setpoint(_document(**extra))


class NumericRefusalTests(unittest.TestCase):
    def test_a_non_finite_velocity_component_is_refused(self) -> None:
        # JSON Schema's "number" accepts NaN and Infinity once Python has parsed
        # them; a failed computation must not become a movement command.
        for component in ("x_m_s", "y_m_s", "z_m_s", "yaw_rate_deg_s"):
            for value in (float("nan"), float("inf"), float("-inf")):
                velocity = {"x_m_s": 0.0, "y_m_s": 0.0, "z_m_s": 0.0, "yaw_rate_deg_s": 0.0}
                velocity[component] = value
                with self.subTest(component=component, value=value):
                    with self.assertRaises(OffboardContractError):
                        load_setpoint(_document(velocity=velocity))

    def test_a_ttl_that_cannot_expire_is_refused(self) -> None:
        # JSON has no infinity literal, but 1e400 parses to inf and satisfies a
        # lower-bound-only schema check. An infinite ttl means a single setpoint
        # is obeyable forever, which is the coasting failure the ttl prevents.
        document = json.loads(json.dumps(_document()).replace('"ttl_s": 0.2', '"ttl_s": 1e400'))
        with self.assertRaises(OffboardContractError):
            load_setpoint(document)

    def test_a_non_positive_ttl_is_refused(self) -> None:
        for ttl in (0, -0.1):
            with self.subTest(ttl=ttl), self.assertRaises(OffboardContractError):
                load_setpoint(_document(ttl_s=ttl))


class SchemaIntegrityTests(unittest.TestCase):
    """An unreadable rulebook must not look like the absence of rules."""

    def setUp(self) -> None:
        from brain.control import contract

        self._contract = contract
        contract._validator.cache_clear()
        self.addCleanup(contract._validator.cache_clear)

    def _with_schema(self, text: str):
        import tempfile
        from unittest.mock import patch

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "schema.json"
        path.write_text(text, encoding="utf-8")
        return patch.object(self._contract, "OFFBOARD_SETPOINT_SCHEMA_PATH", path)

    def test_a_corrupt_schema_is_a_contract_refusal(self) -> None:
        # Not a bare JSONDecodeError: a caller catching the documented failure
        # would not catch that one, and the setpoint would escape unjudged.
        with self._with_schema("{ this is not json"):
            with self.assertRaises(OffboardContractError):
                load_setpoint(_document())

    def test_a_schema_that_is_not_a_schema_is_a_contract_refusal(self) -> None:
        with self._with_schema('{"type": "not-a-type"}'):
            with self.assertRaises(OffboardContractError):
                load_setpoint(_document())

    def test_a_missing_schema_is_a_contract_refusal(self) -> None:
        from unittest.mock import patch

        with patch.object(
            self._contract, "OFFBOARD_SETPOINT_SCHEMA_PATH", Path("/nonexistent/schema.json")
        ):
            with self.assertRaises(OffboardContractError):
                load_setpoint(_document())


class TimestampTests(unittest.TestCase):
    def test_a_timestamp_without_an_offset_is_refused(self) -> None:
        with self.assertRaises(OffboardContractError):
            load_setpoint(_document(issued_at="2026-07-26T12:00:00"))

    def test_a_date_without_a_time_is_refused(self) -> None:
        with self.assertRaises(OffboardContractError):
            load_setpoint(_document(issued_at="2026-07-26"))

    def test_a_naive_now_cannot_measure_an_age(self) -> None:
        setpoint = load_setpoint(_document())
        with self.assertRaises(OffboardContractError):
            setpoint.state(datetime(2026, 7, 26, 12, 0, 0))  # noqa: DTZ001 - the point of the test


class ExpiryTests(unittest.TestCase):
    def test_a_fresh_setpoint_is_valid(self) -> None:
        setpoint = load_setpoint(_document())
        self.assertIs(setpoint.state(ISSUED_AT + timedelta(seconds=0.1)), SetpointState.VALID)

    def test_a_setpoint_past_its_own_ttl_has_expired(self) -> None:
        setpoint = load_setpoint(_document())
        self.assertIs(setpoint.state(ISSUED_AT + timedelta(seconds=0.3)), SetpointState.EXPIRED)

    def test_a_producer_may_declare_its_own_result_untrustworthy(self) -> None:
        setpoint = load_setpoint(_document(validity="invalid"))
        self.assertIs(setpoint.state(ISSUED_AT), SetpointState.INVALID)

    def test_a_setpoint_from_the_future_is_invalid_not_fresh(self) -> None:
        # Clamping a negative age to zero would keep a command obeyable until
        # the wall clock caught up, and then for its full ttl on top of that.
        setpoint = load_setpoint(_document())
        self.assertIs(setpoint.state(ISSUED_AT - timedelta(seconds=5)), SetpointState.INVALID)

    def test_small_clock_skew_between_machines_is_tolerated(self) -> None:
        setpoint = load_setpoint(_document())
        self.assertIs(setpoint.state(ISSUED_AT - timedelta(seconds=0.1)), SetpointState.VALID)


if __name__ == "__main__":
    unittest.main()
