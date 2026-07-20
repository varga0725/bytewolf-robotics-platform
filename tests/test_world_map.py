"""The world map anchors body-frame scans to a grid that survives a turn.

A sector bearing means nothing once the vehicle rotates, so the map's whole
value is that the same wall lands in the same cell twice. What it must never
gain in the process is the ability to claim free space.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from brain.memory.world_map import (
    MapGrid,
    VehiclePose,
    WorldMapError,
    map_cell_of_claim,
    map_claims_from_obstacle_observation,
    map_view,
)
from brain.memory.world_memory import WorldMemory, load_world_claim
from brain.telemetry.observation import load_observation


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
ORIGIN = MapGrid(47.397971, 8.546164, cell_size_m=2.0)


def _observation(sectors: list[dict[str, object]], *, observed_at: datetime = NOW) -> object:
    return load_observation({
        "contract_version": "v0.1",
        "kind": "obstacle",
        "vehicle_id": "x500v2_reference_01",
        "observed_at": observed_at.isoformat(),
        "max_age_s": 1.0,
        "validity": "valid",
        "source": "gz lidar_2d_v2",
        "payload": {
            "frame": "body_frd",
            "sensor": {"id": "lidar_2d_v2", "min_range_m": 0.1, "max_range_m": 30.0},
            "sectors": sectors,
        },
    })


def _measured(yaw_deg: float, distance_m: float, confidence: float = 0.9) -> dict[str, object]:
    return {
        "yaw_deg": yaw_deg,
        "width_deg": 10.0,
        "coverage": "measured",
        "distance_m": distance_m,
        "confidence": confidence,
    }


class MapGridTests(unittest.TestCase):
    def test_a_grid_needs_a_positive_cell_size(self) -> None:
        with self.assertRaises(WorldMapError):
            MapGrid(47.0, 8.0, cell_size_m=0)

    def test_negative_offsets_land_in_distinct_cells(self) -> None:
        self.assertEqual(ORIGIN.cell_of(-0.5, -0.5), (-1, -1))
        self.assertEqual(ORIGIN.cell_of(0.5, 0.5), (0, 0))

    def test_a_cell_centre_maps_north_and_east_onto_the_globe(self) -> None:
        near_latitude, near_longitude = ORIGIN.global_of((0, 0))
        north_latitude, north_longitude = ORIGIN.global_of((5, 0))
        _, east_longitude = ORIGIN.global_of((0, 5))

        self.assertGreater(north_latitude, near_latitude, "north increases latitude")
        self.assertAlmostEqual(north_longitude, near_longitude, "north alone does not move east")
        self.assertGreater(east_longitude, near_longitude, "east increases longitude")


class ObstacleProjectionTests(unittest.TestCase):
    def test_the_same_wall_lands_in_the_same_cell_after_the_vehicle_turns(self) -> None:
        facing_north = map_claims_from_obstacle_observation(
            _observation([_measured(0.0, 10.0)]), VehiclePose(47.397971, 8.546164, yaw_deg=0.0), ORIGIN, NOW
        )
        turned_east = map_claims_from_obstacle_observation(
            _observation([_measured(-90.0, 10.0)]), VehiclePose(47.397971, 8.546164, yaw_deg=90.0), ORIGIN, NOW
        )

        self.assertEqual(len(facing_north), 1)
        self.assertEqual(facing_north[0].subject, turned_east[0].subject)
        self.assertEqual(facing_north[0].category, "map_region")

    def test_a_bearing_is_measured_clockwise_from_north(self) -> None:
        east = map_claims_from_obstacle_observation(
            _observation([_measured(90.0, 10.0)]), VehiclePose(47.397971, 8.546164, yaw_deg=0.0), ORIGIN, NOW
        )

        cell = map_cell_of_claim(east[0])

        assert cell is not None
        self.assertAlmostEqual(cell.east_m, 11.0, places=6)
        self.assertAlmostEqual(cell.north_m, 1.0, places=6)

    def test_the_vehicle_offset_moves_the_measurement_with_it(self) -> None:
        away = map_claims_from_obstacle_observation(
            _observation([_measured(0.0, 10.0)]),
            VehiclePose(47.397971, 8.546164, yaw_deg=0.0, north_m=20.0),
            ORIGIN,
            NOW,
        )

        cell = map_cell_of_claim(away[0])

        assert cell is not None
        self.assertAlmostEqual(cell.north_m, 31.0, places=6)

    def test_clear_and_unobserved_sectors_never_write_a_cell(self) -> None:
        observation = _observation([
            {"yaw_deg": 0.0, "width_deg": 10.0, "coverage": "clear"},
            {"yaw_deg": 180.0, "width_deg": 90.0, "coverage": "unobserved"},
        ])

        claims = map_claims_from_obstacle_observation(
            observation, VehiclePose(47.397971, 8.546164, yaw_deg=0.0), ORIGIN, NOW
        )

        self.assertEqual(claims, ())

    def test_two_beams_on_one_wall_are_one_piece_of_evidence(self) -> None:
        observation = _observation([_measured(0.0, 9.0, 0.6), _measured(2.0, 9.0, 0.95)])

        claims = map_claims_from_obstacle_observation(
            observation, VehiclePose(47.397971, 8.546164, yaw_deg=0.0), ORIGIN, NOW
        )

        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].confidence, 0.95, "the stronger return speaks for the cell")

    def test_a_stale_scan_is_not_mapped(self) -> None:
        claims = map_claims_from_obstacle_observation(
            _observation([_measured(0.0, 10.0)]),
            VehiclePose(47.397971, 8.546164, yaw_deg=0.0),
            ORIGIN,
            NOW + timedelta(seconds=5),
        )

        self.assertEqual(claims, ())

    def test_a_map_claim_carries_the_sector_width_it_came_from(self) -> None:
        claims = map_claims_from_obstacle_observation(
            _observation([_measured(0.0, 10.0)]), VehiclePose(47.397971, 8.546164, yaw_deg=0.0), ORIGIN, NOW
        )

        self.assertIn("10°-os szektorból", claims[0].statement)
        self.assertEqual(claims[0].source, "gz lidar_2d_v2")
        self.assertIsNotNone(claims[0].position)


class MapViewTests(unittest.TestCase):
    def test_a_stored_cell_reads_back_at_the_same_place(self) -> None:
        claims = map_claims_from_obstacle_observation(
            _observation([_measured(0.0, 10.0)]), VehiclePose(47.397971, 8.546164, yaw_deg=0.0), ORIGIN, NOW
        )
        memory = WorldMemory(claims)

        cells = map_view(memory.recall(NOW))

        self.assertEqual(len(cells), 1)
        self.assertAlmostEqual(cells[0].north_m, 11.0, places=6)
        self.assertEqual(cells[0].cell_size_m, 2.0)
        self.assertFalse(cells[0].disputed)

    def test_a_disputed_cell_is_drawn_as_disputed_not_dropped(self) -> None:
        claims = map_claims_from_obstacle_observation(
            _observation([_measured(0.0, 10.0)]), VehiclePose(47.397971, 8.546164, yaw_deg=0.0), ORIGIN, NOW
        )

        cells = map_view((), claims)

        self.assertTrue(cells[0].disputed)

    def test_a_non_map_claim_is_not_readable_as_a_cell(self) -> None:
        claim = load_world_claim({
            "contract_version": "v0.1",
            "subject": "marker:red-pad",
            "category": "landmark",
            "statement": "A piros jel a padlón van.",
            "evidence": {
                "source": "camera:down_rgb",
                "observed_at": NOW.isoformat(),
                "expires_at": (NOW + timedelta(minutes=5)).isoformat(),
                "confidence": 0.9,
            },
        })

        self.assertIsNone(map_cell_of_claim(claim))
        self.assertEqual(map_view((claim,)), [])

    def test_a_map_claim_with_an_unreadable_subject_is_skipped(self) -> None:
        claim = load_world_claim({
            "contract_version": "v0.1",
            "subject": "map_region:kert",
            "category": "map_region",
            "statement": "Valami a kertben.",
            "evidence": {
                "source": "kézi",
                "observed_at": NOW.isoformat(),
                "expires_at": (NOW + timedelta(minutes=5)).isoformat(),
                "confidence": 0.9,
            },
        })

        self.assertIsNone(map_cell_of_claim(claim))


class WorldMapApiTests(unittest.TestCase):
    """The map is served read-only, and it never gains a free-space layer."""

    def setUp(self) -> None:
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from fastapi.testclient import TestClient

        from apps.api.command_gateway import AgentReply, DashboardCommandGateway
        from apps.api.server import create_app
        from brain.memory.world_memory import append_claim

        self.directory = TemporaryDirectory()
        root = Path(self.directory.name)
        path = root / "world" / "claims.jsonl"
        observed_at = datetime.now(UTC)
        for claim in map_claims_from_obstacle_observation(
            _observation([_measured(0.0, 10.0)], observed_at=observed_at),
            VehiclePose(47.397971, 8.546164, yaw_deg=0.0),
            ORIGIN,
            observed_at,
            ttl_s=3_600,
        ):
            append_claim(path, claim)
        gateway = DashboardCommandGateway(
            converse=lambda _session, _text: AgentReply("Szia!", False, "skipped"),
            review=lambda _text: "plan-1",
            execute=lambda _plan: "submitted",
        )
        self.client = TestClient(
            create_app(root / "telemetry.json", world_memory_path=path, gateway=gateway)
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_the_map_serves_occupied_cells_with_their_grid_size(self) -> None:
        body = self.client.get("/api/v1/world-map").json()

        self.assertTrue(body["occupancy_only"], "the payload states it holds no free space")
        self.assertEqual(len(body["cells"]), 1)
        self.assertEqual(body["cells"][0]["cell_size_m"], 2.0)
        self.assertFalse(body["cells"][0]["disputed"])

    def test_the_map_has_no_write_endpoint(self) -> None:
        for method in (self.client.post, self.client.put, self.client.delete):
            with self.subTest(method=method.__name__):
                self.assertEqual(method("/api/v1/world-map").status_code, 405)

    def test_the_dashboard_draws_the_map_and_says_what_it_is_not(self) -> None:
        page = self.client.get("/").text

        self.assertIn('id="world-map"', page)
        self.assertIn("szabad területet ez a réteg soha nem állít", page)


if __name__ == "__main__":
    unittest.main()
