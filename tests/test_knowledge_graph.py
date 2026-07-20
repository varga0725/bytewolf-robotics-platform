"""Two memories, two graphs, and no edge between them.

Drawing personal facts and sensor evidence in one picture is how a detection
becomes a person's property. The boundary is enforced in the projection, not
left to the renderer.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from brain.memory.graph import (
    GraphBoundaryError,
    GraphEdge,
    GraphNode,
    KnowledgeGraph,
    knowledge_view,
    personal_graph,
    world_graph,
)
from brain.memory.world_memory import load_world_claim


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


def _claim(subject: str = "marker:red-pad", *, source: str = "camera:down_rgb", confidence: float = 0.9):
    return load_world_claim({
        "contract_version": "v0.1",
        "subject": subject,
        "category": "landmark",
        "statement": f"{subject} látható.",
        "evidence": {
            "source": source,
            "observed_at": NOW.isoformat(),
            "expires_at": (NOW + timedelta(minutes=10)).isoformat(),
            "confidence": confidence,
        },
    })


class PersonalGraphTests(unittest.TestCase):
    def test_every_personal_fact_hangs_off_the_user(self) -> None:
        graph = personal_graph([
            {"id": "a", "category": "name", "fact": "Ferenc", "recorded_at": "2026-07-20"},
            {"id": "b", "category": "preference", "fact": "Baylands világ", "recorded_at": "2026-07-20"},
        ])

        self.assertEqual(len(graph.nodes), 3)
        self.assertEqual({edge.source for edge in graph.edges}, {"personal:user"})
        self.assertIn("így hívnak", [edge.label for edge in graph.edges])

    def test_an_empty_memory_is_still_a_user(self) -> None:
        graph = personal_graph([])

        self.assertEqual([node.label for node in graph.nodes], ["Te"])
        self.assertEqual(graph.edges, ())


class WorldGraphTests(unittest.TestCase):
    def test_the_only_relation_is_the_one_the_evidence_records(self) -> None:
        graph = world_graph([_claim(), _claim("obstacle:north-wall")])

        self.assertEqual({edge.label for edge in graph.edges}, {"megfigyelte"})
        self.assertEqual(len([node for node in graph.nodes if node.kind == "source"]), 1)

    def test_a_disputed_subject_is_marked_rather_than_hidden(self) -> None:
        graph = world_graph((), [_claim("obstacle:north-wall")])

        disputed = [node for node in graph.nodes if node.kind == "disputed"]
        self.assertEqual(len(disputed), 1)
        self.assertIn("ELLENTMONDÁSOS", disputed[0].detail)

    def test_two_scans_from_one_sensor_share_its_node(self) -> None:
        graph = world_graph([_claim("a"), _claim("b"), _claim("c")])

        self.assertEqual(len([node for node in graph.nodes if node.kind == "source"]), 1)
        self.assertEqual(len(graph.edges), 3)


class GraphBoundaryTests(unittest.TestCase):
    def test_an_edge_may_not_cross_between_the_two_memories(self) -> None:
        with self.assertRaisesRegex(GraphBoundaryError, "separate"):
            KnowledgeGraph(
                (
                    GraphNode("personal:user", "Te", "person"),
                    GraphNode("world:subject:marker", "marker", "landmark"),
                ),
                (GraphEdge("personal:user", "world:subject:marker", "birtokolja"),),
                "personal:",
            )

    def test_an_edge_must_connect_nodes_of_its_own_graph(self) -> None:
        with self.assertRaises(GraphBoundaryError):
            KnowledgeGraph(
                (GraphNode("personal:user", "Te", "person"),),
                (GraphEdge("personal:user", "personal:missing", "kapcsolat"),),
                "personal:",
            )

    def test_the_dashboard_view_keeps_the_namespaces_disjoint(self) -> None:
        view = knowledge_view(
            [{"id": "a", "category": "name", "fact": "Ferenc", "recorded_at": "2026-07-20"}],
            [_claim()],
            [_claim("obstacle:north-wall")],
        )

        personal_ids = {node["id"] for node in view["personal"]["nodes"]}
        world_ids = {node["id"] for node in view["world"]["nodes"]}

        self.assertEqual(personal_ids & world_ids, set())
        self.assertTrue(all(node.startswith("personal:") for node in personal_ids))
        self.assertTrue(all(node.startswith("world:") for node in world_ids))
        self.assertIn("két külön tároló", view["boundary"])

    def test_a_remembered_name_never_appears_in_the_world_graph(self) -> None:
        view = knowledge_view(
            [{"id": "a", "category": "name", "fact": "Ferenc", "recorded_at": "2026-07-20"}], [_claim()]
        )

        world_text = str(view["world"])

        self.assertNotIn("Ferenc", world_text)


class KnowledgeApiTests(unittest.TestCase):
    """The dashboard receives two graphs and the reason they are two."""

    def setUp(self) -> None:
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from fastapi.testclient import TestClient

        from apps.api.command_gateway import AgentReply, DashboardCommandGateway
        from apps.api.server import create_app
        from brain.memory.world_memory import append_claim

        self.session = "b3b9c777-4860-4b6d-bf59-1a4a98c31ea3"
        self.directory = TemporaryDirectory()
        root = Path(self.directory.name)
        self.memory_dir = root / "memory"
        self.memory_dir.mkdir()
        (self.memory_dir / f"{self.session}.json").write_text(
            '{"facts": [{"id": "t1:0", "category": "name", "fact": "Ferenc", '
            '"recorded_at": "2026-07-20T09:00:00+00:00"}]}',
            encoding="utf-8",
        )
        world_path = root / "world" / "claims.jsonl"
        append_claim(world_path, _claim())
        gateway = DashboardCommandGateway(
            converse=lambda _session, _text: AgentReply("Szia!", False, "skipped"),
            review=lambda _text: "plan-1",
            execute=lambda _plan: "submitted",
        )
        self.client = TestClient(
            create_app(
                root / "telemetry.json",
                memory_dir=self.memory_dir,
                world_memory_path=world_path,
                gateway=gateway,
            )
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_both_graphs_are_served_with_disjoint_namespaces(self) -> None:
        body = self.client.get(
            "/api/v1/knowledge", headers={"X-ByteWolf-Session": self.session}
        ).json()

        personal = {node["id"] for node in body["personal"]["nodes"]}
        world = {node["id"] for node in body["world"]["nodes"]}

        self.assertIn("personal:user", personal)
        self.assertEqual(personal & world, set())
        self.assertNotIn("Ferenc", str(body["world"]))

    def test_the_knowledge_view_is_session_scoped(self) -> None:
        other = self.client.get(
            "/api/v1/knowledge", headers={"X-ByteWolf-Session": "0f7b2c62-1a1a-4c2f-9a55-2f9e4a6c1b33"}
        ).json()

        self.assertEqual([node["label"] for node in other["personal"]["nodes"]], ["Te"])
        self.assertTrue(other["world"]["nodes"], "world evidence is shared, not per person")

    def test_the_dashboard_names_the_boundary_it_draws(self) -> None:
        page = self.client.get("/").text

        self.assertIn('id="personal-graph"', page)
        self.assertIn('id="world-graph"', page)
        self.assertIn("Nincs köztük él", page)


if __name__ == "__main__":
    unittest.main()
