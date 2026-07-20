"""Project the two memories into two graphs that may never touch.

The dashboard wants to show what the robot knows about people and what it
knows about places. Those are different stores for a reason, and drawing them
together is precisely how they would merge: one edge from a person to a
detected object turns "the camera saw a red pad" into "Ferenc's red pad", and
the store contract that forbids identity claims is bypassed by a picture.

So this module builds *two* graphs, namespaces their node ids, and the invariant
`no edge crosses the namespaces` is enforced here and asserted in the tests.
The rendering shows them side by side with the boundary named, never linked.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from brain.memory.world_memory import WorldClaim


PERSONAL_NAMESPACE = "personal:"
WORLD_NAMESPACE = "world:"

_PERSONAL_EDGE_LABEL = {
    "name": "így hívnak",
    "preference": "ezt szereti",
    "place_label": "így nevezi",
    "relationship": "kapcsolat",
}


class GraphBoundaryError(AssertionError):
    """An edge tried to link personal memory to world evidence."""


@dataclass(frozen=True)
class GraphNode:
    id: str
    label: str
    kind: str
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "kind": self.kind, "detail": self.detail}


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    label: str

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.source, "target": self.target, "label": self.label}


@dataclass(frozen=True)
class KnowledgeGraph:
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    namespace: str

    def __post_init__(self) -> None:
        ids = {node.id for node in self.nodes}
        for edge in self.edges:
            if not edge.source.startswith(self.namespace) or not edge.target.startswith(self.namespace):
                raise GraphBoundaryError(
                    "A knowledge edge may not leave its own memory; personal facts and world "
                    "evidence are separate stores and must stay separate pictures."
                )
            if edge.source not in ids or edge.target not in ids:
                raise GraphBoundaryError("A knowledge edge must connect two nodes of this graph.")

    def as_dict(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace,
            "nodes": [node.as_dict() for node in self.nodes],
            "edges": [edge.as_dict() for edge in self.edges],
        }


def personal_graph(facts: Sequence[Mapping[str, Any]]) -> KnowledgeGraph:
    """What the user told us, hung off a single 'you' node.

    Every edge starts at the user, because every personal fact is something
    they said about themselves. Nothing here is inferred from a sensor.
    """
    root = GraphNode(f"{PERSONAL_NAMESPACE}user", "Te", "person", "A beszélgetőpartner")
    nodes = [root]
    edges: list[GraphEdge] = []
    for fact in facts:
        category = str(fact.get("category", ""))
        identifier = f"{PERSONAL_NAMESPACE}fact:{fact.get('id', len(nodes))}"
        nodes.append(GraphNode(identifier, str(fact.get("fact", "")), category, str(fact.get("recorded_at", ""))))
        edges.append(GraphEdge(root.id, identifier, _PERSONAL_EDGE_LABEL.get(category, category)))
    return KnowledgeGraph(tuple(nodes), tuple(edges), PERSONAL_NAMESPACE)


def world_graph(
    claims: Iterable[WorldClaim], disputed: Iterable[WorldClaim] = ()
) -> KnowledgeGraph:
    """What a sensor measured, hung off the sensor that measured it.

    The only relation asserted is the one the evidence actually records: this
    source observed this subject. Anything richer — 'this obstacle is near that
    landmark' — would be inference the store never made.
    """
    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []
    for claim, is_disputed in [(claim, False) for claim in claims] + [(claim, True) for claim in disputed]:
        source_id = f"{WORLD_NAMESPACE}source:{claim.source}"
        subject_id = f"{WORLD_NAMESPACE}subject:{claim.subject}"
        nodes.setdefault(source_id, GraphNode(source_id, claim.source, "source", "Bizonyítékforrás"))
        detail = f"{claim.statement} · bizonyosság: {round(claim.confidence * 100)}%"
        nodes[subject_id] = GraphNode(
            subject_id,
            claim.subject,
            "disputed" if is_disputed else claim.category,
            detail if not is_disputed else f"ELLENTMONDÁSOS · {detail}",
        )
        edge = GraphEdge(source_id, subject_id, "megfigyelte")
        if edge not in edges:
            edges.append(edge)
    return KnowledgeGraph(tuple(nodes.values()), tuple(edges), WORLD_NAMESPACE)


def knowledge_view(
    facts: Sequence[Mapping[str, Any]],
    claims: Iterable[WorldClaim],
    disputed: Iterable[WorldClaim] = (),
) -> dict[str, Any]:
    """The payload the dashboard draws: two graphs and the reason they are two."""
    return {
        "personal": personal_graph(facts).as_dict(),
        "world": world_graph(claims, disputed).as_dict(),
        "boundary": (
            "A személyes memória és a világ-bizonyíték két külön tároló. Nincs köztük él: "
            "egy észlelésből soha nem lesz személyhez kötött tény."
        ),
    }
