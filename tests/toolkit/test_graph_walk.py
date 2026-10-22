"""The walk reads relations outward from seeds and says what it could not read."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from plane_mcp.toolkit.graph import Edge, EdgeType
from plane_mcp.toolkit.graph_walk import Ref, Walk, walk

Buckets = dict[str, list[dict[str, str]]]


class FakePlane:
    """Relations and details per item; records every read so cost can be asserted."""

    def __init__(self, relations: Mapping[str, Buckets], forbidden: set[str] | None = None) -> None:
        self.relations: Mapping[str, Buckets] = relations
        self.forbidden: set[str] = forbidden or set()
        self.relation_reads: list[str] = []
        self.node_reads: list[str] = []

    def read_relations(self, ref: Ref) -> Buckets | None:
        self.relation_reads.append(ref.id)
        if ref.id in self.forbidden:
            return None
        return dict(self.relations.get(ref.id, {}))

    def read_node(self, ref: Ref) -> dict[str, Any] | None:
        self.node_reads.append(ref.id)
        if ref.id in self.forbidden:
            return None
        return {"name": f"item {ref.id}", "project_id": ref.project_id}


def _run(plane: FakePlane, seeds: list[Ref], depth: int = 1, max_items: int = 50) -> Walk:
    return walk(seeds, plane.read_relations, plane.read_node, depth, max_items)


def _blocks(target: str, project: str = "p1") -> Buckets:
    return {"blocking": [{"id": target, "project_id": project}]}


def test_a_seed_with_no_relations_is_a_single_node() -> None:
    plane: FakePlane = FakePlane({})

    result: Walk = _run(plane, [Ref("a", "p1")])

    assert result.refs == {"a": "p1"}
    assert result.edges == []
    assert result.unexpanded == set()


def test_depth_zero_reads_the_seed_but_does_not_expand_its_neighbours() -> None:
    plane: FakePlane = FakePlane({"a": _blocks("b"), "b": _blocks("c")})

    result: Walk = _run(plane, [Ref("a", "p1")], depth=0)

    assert plane.relation_reads == ["a"]
    assert set(result.refs) == {"a", "b"}
    assert result.unexpanded == {"b"}


def test_depth_one_expands_neighbours_and_stops_at_the_next_hop() -> None:
    plane: FakePlane = FakePlane({"a": _blocks("b"), "b": _blocks("c"), "c": _blocks("d")})

    result: Walk = _run(plane, [Ref("a", "p1")], depth=1)

    assert plane.relation_reads == ["a", "b"]
    assert set(result.refs) == {"a", "b", "c"}
    assert result.unexpanded == {"c"}


def test_an_item_reached_twice_is_read_once() -> None:
    diamond: dict[str, Buckets] = {"a": {"blocking": [{"id": "b"}, {"id": "c"}]}, "b": _blocks("d"), "c": _blocks("d")}
    plane: FakePlane = FakePlane(diamond)

    _run(plane, [Ref("a", "p1")], depth=2)

    assert sorted(plane.relation_reads) == ["a", "b", "c", "d"]
    assert sorted(plane.node_reads) == ["a", "b", "c", "d"]


def test_a_relation_read_from_both_ends_is_one_edge() -> None:
    plane: FakePlane = FakePlane({"a": _blocks("b"), "b": {"blocked_by": [{"id": "a", "project_id": "p1"}]}})

    result: Walk = _run(plane, [Ref("a", "p1")], depth=1)

    assert result.edges == [Edge("a", "b", EdgeType.BLOCKING)]


def test_a_neighbour_in_another_project_is_read_in_that_project() -> None:
    plane: FakePlane = FakePlane({"a": _blocks("b", project="p2")})
    seen: list[Ref] = []
    original = plane.read_relations

    def spy(ref: Ref) -> Buckets | None:
        seen.append(ref)
        return original(ref)

    result: Walk = walk([Ref("a", "p1")], spy, plane.read_node, 1, 50)

    assert result.refs["b"] == "p2"
    assert Ref("b", "p2") in seen


def test_a_neighbour_without_a_reported_project_is_assumed_to_share_the_items() -> None:
    plane: FakePlane = FakePlane({"a": {"blocking": [{"id": "b"}]}})

    assert _run(plane, [Ref("a", "p1")]).refs["b"] == "p1"


def test_an_unreadable_neighbour_is_recorded_and_the_walk_continues() -> None:
    plane: FakePlane = FakePlane({"a": {"blocking": [{"id": "b"}, {"id": "c"}]}}, forbidden={"b"})

    result: Walk = _run(plane, [Ref("a", "p1")])

    assert result.unreadable == {"b"}
    assert "b" in result.unexpanded
    assert "c" in result.refs
    assert "b" not in result.details


def test_an_unreadable_seed_is_unexpanded_and_unreadable() -> None:
    plane: FakePlane = FakePlane({}, forbidden={"a"})

    result: Walk = _run(plane, [Ref("a", "p1")])

    assert result.unreadable == {"a"}
    assert result.unexpanded == {"a"}


def test_max_items_cuts_the_walk_and_reports_what_it_did_not_read() -> None:
    wide: dict[str, Buckets] = {"a": {"blocking": [{"id": "b"}, {"id": "c"}, {"id": "d"}]}}
    plane: FakePlane = FakePlane(wide)

    result: Walk = _run(plane, [Ref("a", "p1")], max_items=2)

    assert result.truncated
    assert len(plane.node_reads) == 2
    assert set(result.cut) == {"c", "d"}
    assert result.unexpanded >= {"c", "d"}


def test_a_cut_neighbour_keeps_its_edge_so_the_order_cannot_forget_it() -> None:
    plane: FakePlane = FakePlane({"a": {"blocked_by": [{"id": "z"}]}})

    result: Walk = _run(plane, [Ref("a", "p1")], max_items=1)

    assert result.edges == [Edge("z", "a", EdgeType.BLOCKING)]
    assert "z" in result.refs


def test_an_untruncated_walk_reports_no_cut() -> None:
    result: Walk = _run(FakePlane({"a": _blocks("b")}), [Ref("a", "p1")])

    assert not result.truncated
    assert result.cut == []


def test_a_plain_relation_is_kept_only_when_both_ends_are_in_the_graph() -> None:
    relations: dict[str, Buckets] = {
        "a": {"blocking": [{"id": "b"}], "relates_to": [{"id": "b"}, {"id": "stranger"}]},
    }
    plane: FakePlane = FakePlane(relations)

    result: Walk = _run(plane, [Ref("a", "p1")], depth=0)

    assert Edge("a", "b", EdgeType.RELATES_TO) in result.edges
    assert "stranger" not in result.refs
    assert all("stranger" not in (edge.source, edge.target) for edge in result.edges)


def test_several_seeds_are_all_walked() -> None:
    plane: FakePlane = FakePlane({"a": _blocks("c"), "b": _blocks("c")})

    result: Walk = _run(plane, [Ref("a", "p1"), Ref("b", "p1")], depth=0)

    assert set(result.refs) == {"a", "b", "c"}
    assert len(result.edges) == 2
