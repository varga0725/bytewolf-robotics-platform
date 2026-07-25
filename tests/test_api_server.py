"""Web/mobile API must expose data and preserve the approval boundary."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from apps.api.command_gateway import AgentReply, DashboardCommandGateway
from apps.api.server import _load_project_environment, create_app


SESSION = "b3b9c777-4860-4b6d-bf59-1a4a98c31ea3"


class ApiServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.telemetry = Path(self.directory.name) / "telemetry.json"
        self.telemetry.write_text(json.dumps({"in_air": False}), encoding="utf-8")
        self.executed: list[str] = []
        gateway = DashboardCommandGateway(
            converse=lambda _session, _text: AgentReply("Tervet készítek.", True),
            review=lambda _text: "plan-1",
            execute=lambda plan: self.executed.append(plan) or "submitted",
        )
        self.client = TestClient(create_app(self.telemetry, gateway=gateway))

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_dashboard_root_and_telemetry_are_available(self) -> None:
        dashboard = self.client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("d.in_air===true", dashboard.text)
        self.assertIn("relative_altitude_m==null", dashboard.text)
        self.assertIn('id="camera-select"', dashboard.text)
        self.assertIn('id="memory-list"', dashboard.text)
        self.assertIn("/api/v1/memory", dashboard.text)
        self.assertIn("A státuszkapcsolat megszakadt; újrapróbálom.", dashboard.text)
        self.assertNotIn("A küldetés státusza nem olvasható:", dashboard.text)
        response = self.client.get("/api/v1/telemetry")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["in_air"])

    def test_the_dashboard_ships_every_navigable_page(self) -> None:
        """Each sidebar entry must have the section it claims to open."""
        page = self.client.get("/").text

        for identifier in ("page-state", "page-camera", "page-chat", "page-memory", "page-world"):
            with self.subTest(page=identifier):
                self.assertIn(f'data-target="{identifier}"', page, "the sidebar offers it")
                self.assertIn(f'id="{identifier}"', page, "the section exists")

    def test_telemetry_keeps_unknown_flight_state_and_altitude(self) -> None:
        self.telemetry.write_text(json.dumps({
            "in_air": None,
            "position": {
                "latitude_deg": 47.5,
                "longitude_deg": 19.0,
                "absolute_altitude_m": 100.0,
                "relative_altitude_m": None,
            },
        }), encoding="utf-8")

        response = self.client.get("/api/v1/telemetry")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["in_air"])
        self.assertIsNone(response.json()["position"]["relative_altitude_m"])

    def test_detections_are_available_to_the_dashboard(self) -> None:
        detections = Path(self.directory.name) / "detections.json"
        document = {"validity": "valid", "frame": {"width": 640, "height": 480}, "detections": []}
        detections.write_text(json.dumps(document), encoding="utf-8")
        client = TestClient(create_app(self.telemetry, detections_path=detections))

        response = client.get("/api/v1/detections")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), document)

    def test_serves_the_selected_down_camera_and_detections(self) -> None:
        camera = Path(self.directory.name) / "down.jpg"
        camera.write_bytes(b"\xff\xd8\xff\xd9")
        detections = Path(self.directory.name) / "down.json"
        detections.write_text(json.dumps({"validity": "valid", "detections": []}), encoding="utf-8")
        client = TestClient(create_app(
            self.telemetry, down_camera_path=camera, down_detections_path=detections
        ))

        self.assertEqual(client.get("/api/v1/cameras/down").status_code, 200)
        self.assertEqual(client.get("/api/v1/cameras/down/detections").json()["validity"], "valid")
        self.assertEqual(client.get("/api/v1/cameras/side").status_code, 404)

    def test_camera_endpoint_does_not_resend_an_unchanged_frame(self) -> None:
        camera = Path(self.directory.name) / "front.jpg"
        camera.write_bytes(b"\xff\xd8\xff\xd9")
        client = TestClient(create_app(self.telemetry, camera_path=camera))

        first = client.get("/api/v1/cameras/front")
        cached = client.get("/api/v1/cameras/front", headers={"If-None-Match": first.headers["etag"]})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(cached.status_code, 304)

    def test_planning_failure_becomes_a_safe_client_error_not_a_server_error(self) -> None:
        gateway = DashboardCommandGateway(
            converse=lambda _session, _text: AgentReply("Tervet készítek.", True),
            review=lambda _text: (_ for _ in ()).throw(RuntimeError("review failed")),
            execute=lambda _plan: "submitted",
        )
        client = TestClient(create_app(self.telemetry, gateway=gateway))

        response = client.post("/api/v1/chat", headers={"X-ByteWolf-Session": SESSION}, json={"text": "repülj"})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "review failed")

    def test_environment_loader_preserves_exported_values_and_reads_quoted_values(self) -> None:
        dotenv = Path(self.directory.name) / ".env"
        dotenv.write_text("NVIDIA_API_KEY='local-value'\nNIM_MISSION_MODEL=model-name\n", encoding="utf-8")
        environment = {"NVIDIA_API_KEY": "exported-value"}

        _load_project_environment(dotenv, environment)

        self.assertEqual(environment["NVIDIA_API_KEY"], "exported-value")
        self.assertEqual(environment["NIM_MISSION_MODEL"], "model-name")

    def test_plan_status_reports_executor_preflight_failure(self) -> None:
        artifacts = Path(self.directory.name) / "agent-missions"
        artifacts.mkdir()
        artifacts.joinpath("nim-agent-decision.json").write_text(json.dumps({
            "mission_id": "b3b9c777-4860-4b6d-bf59-1a4a98c31ea3",
            "model": "reviewed-plan",
            "outcome": "failed",
            "recorded_at": "2026-07-18T22:00:00Z",
            "failure_reason": "MissionPreflightError: Preflight rejected: health is not ready.",
        }), encoding="utf-8")
        client = TestClient(create_app(self.telemetry, agent_artifact_dir=artifacts))

        response = client.get("/api/v1/plans/b3b9c777-4860-4b6d-bf59-1a4a98c31ea3/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "failed")
        self.assertIn("előellenőrzése", response.json()["message"])

    def test_plan_status_accepts_the_dashboard_plan_filename(self) -> None:
        response = self.client.get(
            "/api/v1/plans/b3b9c777-4860-4b6d-bf59-1a4a98c31ea3.mission-spec.json/status"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "submitted")

    def test_plan_filename_reports_a_terminal_executor_status(self) -> None:
        artifacts = Path(self.directory.name) / "agent-missions"
        artifacts.mkdir()
        artifacts.joinpath("nim-agent-decision.json").write_text(json.dumps({
            "mission_id": "b3b9c777-4860-4b6d-bf59-1a4a98c31ea3",
            "model": "reviewed-plan",
            "outcome": "completed",
            "recorded_at": "2026-07-19T10:00:00Z",
        }), encoding="utf-8")
        client = TestClient(create_app(self.telemetry, agent_artifact_dir=artifacts))

        response = client.get(
            "/api/v1/plans/b3b9c777-4860-4b6d-bf59-1a4a98c31ea3.mission-spec.json/status"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "completed")

    def test_chat_requires_approval_before_execution(self) -> None:
        headers = {"X-ByteWolf-Session": SESSION}
        proposed = self.client.post("/api/v1/chat", headers=headers, json={"text": "repülj"})

        self.assertEqual(proposed.status_code, 200)
        self.assertTrue(proposed.json()["approval_required"])
        self.assertEqual(self.executed, [])
        approved = self.client.post("/api/v1/plans/approve", headers=headers, json={"plan_id": "plan-1"})
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(self.executed, ["plan-1"])

    def test_rejects_bad_session_identifier(self) -> None:
        response = self.client.post("/api/v1/chat", headers={"X-ByteWolf-Session": "no"}, json={"text": "hello"})
        self.assertEqual(response.status_code, 400)

    def test_session_memory_can_be_inspected_corrected_and_erased(self) -> None:
        memory_dir = Path(self.directory.name) / "memory"
        memory_dir.mkdir()
        memory_dir.joinpath(f"{SESSION}.json").write_text(json.dumps({"facts": [{
            "id": "fact-1", "category": "preference", "fact": "Baylands", "recorded_at": "2026-07-19T12:00:00Z",
        }]}), encoding="utf-8")
        client = TestClient(create_app(self.telemetry, memory_dir=memory_dir))
        headers = {"X-ByteWolf-Session": SESSION}

        listed = client.get("/api/v1/memory", headers=headers)
        corrected = client.put("/api/v1/memory/fact-1", headers=headers, json={
            "category": "preference", "fact": "Baylands legyen az alapértelmezett világ.",
        })
        erased = client.delete("/api/v1/memory/fact-1", headers=headers)

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["facts"][0]["fact"], "Baylands")
        self.assertEqual(corrected.status_code, 200)
        self.assertEqual(corrected.json()["facts"][0]["fact"], "Baylands legyen az alapértelmezett világ.")
        self.assertEqual(erased.status_code, 200)
        self.assertEqual(erased.json()["facts"], [])

    def test_memory_api_refuses_sensitive_or_invalid_updates(self) -> None:
        memory_dir = Path(self.directory.name) / "memory"
        memory_dir.mkdir()
        memory_dir.joinpath(f"{SESSION}.json").write_text(json.dumps({"facts": [{
            "id": "fact-1", "category": "name", "fact": "Ferenc", "recorded_at": "2026-07-19T12:00:00Z",
        }]}), encoding="utf-8")
        client = TestClient(create_app(self.telemetry, memory_dir=memory_dir))

        response = client.put("/api/v1/memory/fact-1", headers={"X-ByteWolf-Session": SESSION}, json={
            "category": "preference", "fact": "API kulcsom abc",
        })

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "Memory fact is invalid or sensitive.")


class SafetyEnvelopeApiTests(unittest.TestCase):
    """The dashboard draws the contract, never a copy of it."""

    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        telemetry = Path(self.directory.name) / "telemetry.json"
        telemetry.write_text(json.dumps({"in_air": False}), encoding="utf-8")
        self.client = TestClient(create_app(telemetry))

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_the_envelope_comes_from_the_same_file_the_gate_loads(self) -> None:
        from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile

        profile = load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH)
        body = self.client.get("/api/v1/safety-envelope").json()

        self.assertEqual(body["max_radius_m"], profile.max_radius_m)
        self.assertEqual(body["max_altitude_m"], profile.max_altitude_m)

    def test_the_geofence_is_served_so_it_can_be_seen_before_it_refuses(self) -> None:
        """The fence is the tighter bound and the map never drew it.

        A target picked outside it was reviewable-looking right up to the
        rejection, which taught the operator nothing about where they may fly.
        """
        body = self.client.get("/api/v1/safety-envelope").json()

        vertices = body["geofence_vertices_m"]
        self.assertGreaterEqual(len(vertices), 3)
        self.assertTrue(all({"north_m", "east_m"} == set(vertex) for vertex in vertices))

    def test_the_envelope_has_no_write_endpoint(self) -> None:
        for method in (self.client.post, self.client.put, self.client.delete):
            with self.subTest(method=method.__name__):
                self.assertEqual(method("/api/v1/safety-envelope").status_code, 405)

    def test_the_dashboard_stops_hardcoding_the_limits_it_draws(self) -> None:
        page = self.client.get("/").text

        self.assertIn("/api/v1/safety-envelope", page)


if __name__ == "__main__":
    unittest.main()
