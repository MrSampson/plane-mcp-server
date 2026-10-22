"""The graph result: ordered, with everything unsound named rather than placed."""

from __future__ import annotations

from typing import Any

from plane_mcp.toolkit.graph_walk import Ref
from plane_mcp.toolkit.relation_graph import assemble_graph

Buckets = dict[str, list[dict[str, str]]]


def _detail(sequence_id: int, project: str = "p1") -> dict[str, Any]:
    return {
        "name": f"item {sequence_id}",
        "state": "In Progress",
        "priority": "high",
        "sequence_id": sequence_id,
        "project_id": project,
    }


class World:
    """Items keyed by id: sequence number, project and relations."""

    def __init__(self, items: dict[str, tuple[int, str, Buckets]], forbidden: set[str] | None = None) -> None:
        self.items: dict[str, tuple[int, str, Buckets]] = items
        self.forbidden: set[str] = forbidden or set()

    def relations(self, ref: Ref) -> Buckets | None:
        return None if ref.id in self.forbidden else self.items[ref.id][2]

    def node(self, ref: Ref) -> dict[str, Any] | None:
        if ref.id in self.forbidden:
            return None
        sequence_id, project, _ = self.items[ref.id]
        return _detail(sequence_id, project)


PROJECTS: dict[str, str] = {"p1": "PROJ", "p2": "OTHER"}


def _graph(world: World, seeds: list[Ref], **kwargs: Any) -> dict[str, Any]:
    options: dict[str, Any] = {"depth": 1, "max_items": 50, "module_id": "", "diagram": False}
    options.update(kwargs)
    return assemble_graph(seeds, world.relations, world.node, PROJECTS.get, **options)


def _chain() -> World:
    return World(
        {
            "a": (1, "p1", {"blocking": [{"id": "b", "project_id": "p1"}]}),
            "b": (2, "p1", {"blocked_by": [{"id": "a", "project_id": "p1"}], "blocking": [{"id": "c"}]}),
            "c": (3, "p1", {"blocked_by": [{"id": "b", "project_id": "p1"}]}),
        }
    )


def test_a_chain_is_ordered_by_identifier() -> None:
    result: dict[str, Any] = _graph(_chain(), [Ref("a", "p1")], depth=2)

    assert result["order"] == [["PROJ-1"], ["PROJ-2"], ["PROJ-3"]]
    assert result["cycles"] == []
    assert result["unordered"] == {}
    assert result["truncated"] is False
    assert "diagram" not in result


def test_nodes_carry_what_a_reader_needs_to_place_them() -> None:
    result: dict[str, Any] = _graph(_chain(), [Ref("a", "p1")], depth=2)

    node: dict[str, Any] = next(n for n in result["nodes"] if n["id"] == "a")
    assert node == {
        "id": "a",
        "identifier": "PROJ-1",
        "name": "item 1",
        "state": "In Progress",
        "priority": "high",
        "project_id": "p1",
        "project": "PROJ",
        "boundary": False,
        "unreadable": False,
    }


def test_edges_use_identifiers_and_a_relation_appears_once() -> None:
    result: dict[str, Any] = _graph(_chain(), [Ref("a", "p1")], depth=2)

    assert {"from": "PROJ-1", "to": "PROJ-2", "type": "blocking"} in result["edges"]
    assert len(result["edges"]) == 2


def test_a_blocker_in_another_project_is_a_node_with_its_own_project() -> None:
    world: World = World(
        {
            "a": (1, "p1", {"blocked_by": [{"id": "x", "project_id": "p2"}]}),
            "x": (9, "p2", {"blocking": [{"id": "a", "project_id": "p1"}]}),
        }
    )

    result: dict[str, Any] = _graph(world, [Ref("a", "p1")], depth=1)

    assert result["order"] == [["OTHER-9"], ["PROJ-1"]]
    other: dict[str, Any] = next(n for n in result["nodes"] if n["id"] == "x")
    assert (other["project"], other["identifier"]) == ("OTHER", "OTHER-9")


def test_a_cycle_is_reported_and_never_ordered() -> None:
    world: World = World(
        {
            "a": (1, "p1", {"blocking": [{"id": "b"}]}),
            "b": (2, "p1", {"blocking": [{"id": "a"}]}),
        }
    )

    result: dict[str, Any] = _graph(world, [Ref("a", "p1")], depth=1)

    assert result["cycles"] == [["PROJ-1", "PROJ-2", "PROJ-1"]]
    assert result["order"] == []
    assert result["unordered"] == {"PROJ-1": "in_cycle", "PROJ-2": "in_cycle"}


def test_an_item_blocked_from_beyond_the_depth_is_not_called_startable() -> None:
    result: dict[str, Any] = _graph(_chain(), [Ref("b", "p1")], depth=0)

    assert result["order"] == []
    assert result["unordered"]["PROJ-2"] == "after_unexpanded"
    boundary: list[str] = [n["identifier"] for n in result["nodes"] if n["boundary"]]
    assert sorted(boundary) == ["PROJ-1", "PROJ-3"]


def test_an_unreadable_neighbour_is_flagged_and_does_not_fail_the_graph() -> None:
    world: World = World(
        {"a": (1, "p1", {"blocked_by": [{"id": "x", "project_id": "p2"}]}), "x": (9, "p2", {})},
        forbidden={"x"},
    )

    result: dict[str, Any] = _graph(world, [Ref("a", "p1")])

    unreadable: dict[str, Any] = next(n for n in result["nodes"] if n["id"] == "x")
    assert unreadable["unreadable"] is True
    assert unreadable["identifier"] == "x"
    assert result["unordered"]["x"] == "unexpanded"
    assert result["unordered"]["PROJ-1"] == "after_unexpanded"


def test_truncation_is_reported_with_what_was_cut() -> None:
    result: dict[str, Any] = _graph(_chain(), [Ref("a", "p1")], depth=2, max_items=1)

    assert result["truncated"] is True
    assert result["cut"] == ["b"]
    assert result["order"] == [["PROJ-1"]]
    assert result["unordered"] == {"b": "unexpanded"}


def test_a_diagram_is_added_only_when_asked_for() -> None:
    result: dict[str, Any] = _graph(_chain(), [Ref("a", "p1")], depth=2, diagram=True)

    assert result["diagram"].startswith("graph TD")
    assert "PROJ-1" in result["diagram"]


def test_a_module_seed_marks_its_items_as_members() -> None:
    result: dict[str, Any] = _graph(_chain(), [Ref("a", "p1")], depth=2, module_id="m1")

    members: dict[str, Any] = {n["id"]: n.get("module_ids") for n in result["nodes"]}
    assert members == {"a": ["m1"], "b": None, "c": None}
