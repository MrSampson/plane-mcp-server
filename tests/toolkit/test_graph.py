"""The relation graph is a pure function of the relations Plane serves per item."""

from __future__ import annotations

from plane_mcp.toolkit.graph import (
    Edge,
    EdgeType,
    Ordering,
    Unordered,
    dedupe,
    edges_from_relations,
    order,
    related_projects,
)


def _buckets(**filled: list[str]) -> dict[str, list[dict[str, str]]]:
    """A relations payload with the named buckets populated and the rest empty."""
    names: tuple[str, ...] = (
        "blocking",
        "blocked_by",
        "start_before",
        "start_after",
        "finish_before",
        "finish_after",
        "relates_to",
        "duplicate",
    )
    return {name: [{"id": other} for other in filled.get(name, [])] for name in names}


# --- one item's buckets -> directed edges, source precedes target ---


def test_blocking_points_from_the_item_to_what_it_blocks() -> None:
    edges: list[Edge] = edges_from_relations("a", _buckets(blocking=["b"]))

    assert edges == [Edge("a", "b", EdgeType.BLOCKING)]


def test_blocked_by_points_from_the_blocker_to_the_item() -> None:
    edges: list[Edge] = edges_from_relations("b", _buckets(blocked_by=["a"]))

    assert edges == [Edge("a", "b", EdgeType.BLOCKING)]


def test_start_after_is_start_before_read_from_the_other_end() -> None:
    after: list[Edge] = edges_from_relations("b", _buckets(start_after=["a"]))
    before: list[Edge] = edges_from_relations("a", _buckets(start_before=["b"]))

    assert after == before == [Edge("a", "b", EdgeType.START_BEFORE)]


def test_finish_after_is_finish_before_read_from_the_other_end() -> None:
    after: list[Edge] = edges_from_relations("b", _buckets(finish_after=["a"]))
    before: list[Edge] = edges_from_relations("a", _buckets(finish_before=["b"]))

    assert after == before == [Edge("a", "b", EdgeType.FINISH_BEFORE)]


def test_an_item_with_no_relations_yields_no_edges() -> None:
    assert edges_from_relations("a", _buckets()) == []


def test_a_bucket_the_payload_omits_is_treated_as_empty() -> None:
    assert edges_from_relations("a", {"blocking": [{"id": "b"}]}) == [Edge("a", "b", EdgeType.BLOCKING)]


def test_a_plain_relation_has_no_direction_so_its_ends_are_sorted() -> None:
    from_a: list[Edge] = edges_from_relations("a", _buckets(relates_to=["b"]))
    from_b: list[Edge] = edges_from_relations("b", _buckets(relates_to=["a"]))

    assert from_a == from_b == [Edge("a", "b", EdgeType.RELATES_TO)]


def test_duplicate_is_a_plain_relation() -> None:
    assert edges_from_relations("z", _buckets(duplicate=["a"])) == [Edge("a", "z", EdgeType.DUPLICATE)]


def test_related_projects_names_the_project_of_each_far_end_that_reports_one() -> None:
    payload: dict[str, list[dict[str, str]]] = {
        "blocking": [{"id": "b", "project_id": "p2"}],
        "blocked_by": [{"id": "c"}],
    }

    assert related_projects(payload) == {"b": "p2"}


# --- the same relation read from both ends is one edge ---


def test_a_relation_read_from_both_ends_is_one_edge() -> None:
    read_from_a: list[Edge] = edges_from_relations("a", _buckets(blocking=["b"]))
    read_from_b: list[Edge] = edges_from_relations("b", _buckets(blocked_by=["a"]))

    assert dedupe(read_from_a + read_from_b) == [Edge("a", "b", EdgeType.BLOCKING)]


def test_dedupe_keeps_distinct_types_between_the_same_pair() -> None:
    edges: list[Edge] = [Edge("a", "b", EdgeType.BLOCKING), Edge("a", "b", EdgeType.RELATES_TO)]

    assert dedupe(edges) == edges


def test_dedupe_keeps_the_first_seen_order() -> None:
    edges: list[Edge] = [
        Edge("c", "d", EdgeType.BLOCKING),
        Edge("a", "b", EdgeType.BLOCKING),
        Edge("c", "d", EdgeType.BLOCKING),
    ]

    assert dedupe(edges) == [Edge("c", "d", EdgeType.BLOCKING), Edge("a", "b", EdgeType.BLOCKING)]


# --- ordering: waves, cycles, and what cannot be soundly ordered ---


def _blocks(*pairs: tuple[str, str]) -> list[Edge]:
    return [Edge(source, target, EdgeType.BLOCKING) for source, target in pairs]


def test_a_chain_orders_into_one_item_per_wave() -> None:
    ordering: Ordering = order(["a", "b", "c"], _blocks(("a", "b"), ("b", "c")))

    assert ordering.waves == [["a"], ["b"], ["c"]]
    assert ordering.cycles == []
    assert ordering.unordered == {}


def test_independent_items_share_wave_zero() -> None:
    ordering: Ordering = order(["a", "b", "c"], _blocks(("a", "c")))

    assert ordering.waves == [["a", "b"], ["c"]]


def test_an_item_waits_for_its_latest_blocker() -> None:
    ordering: Ordering = order(["a", "b", "c", "d"], _blocks(("a", "b"), ("b", "d"), ("c", "d")))

    assert ordering.waves == [["a", "c"], ["b"], ["d"]]


def test_an_item_with_no_relations_is_startable() -> None:
    assert order(["a"], []).waves == [["a"]]


def test_a_plain_relation_does_not_hold_an_item_back() -> None:
    ordering: Ordering = order(["a", "b"], [Edge("a", "b", EdgeType.RELATES_TO)])

    assert ordering.waves == [["a", "b"]]


def test_start_and_finish_edges_order_like_blocking() -> None:
    edges: list[Edge] = [Edge("a", "b", EdgeType.START_BEFORE), Edge("b", "c", EdgeType.FINISH_BEFORE)]

    assert order(["a", "b", "c"], edges).waves == [["a"], ["b"], ["c"]]


def test_waves_are_sorted_so_the_answer_is_stable() -> None:
    assert order(["c", "a", "b"], []).waves == [["a", "b", "c"]]


# --- a cycle is reported, never ordered around ---


def test_a_cycle_is_reported_and_its_members_are_not_ordered() -> None:
    ordering: Ordering = order(["a", "b", "c"], _blocks(("a", "b"), ("b", "a")))

    assert ordering.cycles == [["a", "b", "a"]]
    assert ordering.waves == [["c"]]
    assert ordering.unordered == {"a": Unordered.IN_CYCLE, "b": Unordered.IN_CYCLE}


def test_an_item_downstream_of_a_cycle_is_unordered_not_startable_later() -> None:
    ordering: Ordering = order(["a", "b", "c"], _blocks(("a", "b"), ("b", "a"), ("b", "c")))

    assert ordering.waves == []
    assert ordering.unordered["c"] == Unordered.AFTER_CYCLE


def test_a_self_blocking_item_is_a_cycle() -> None:
    ordering: Ordering = order(["a"], _blocks(("a", "a")))

    assert ordering.cycles == [["a", "a"]]
    assert ordering.unordered == {"a": Unordered.IN_CYCLE}


def test_a_longer_cycle_is_reported_as_a_closed_path() -> None:
    ordering: Ordering = order(["a", "b", "c"], _blocks(("a", "b"), ("b", "c"), ("c", "a")))

    assert ordering.cycles == [["a", "b", "c", "a"]]


def test_each_separate_cycle_is_reported() -> None:
    ordering: Ordering = order(["a", "b", "c", "d"], _blocks(("a", "b"), ("b", "a"), ("c", "d"), ("d", "c")))

    assert sorted(ordering.cycles) == [["a", "b", "a"], ["c", "d", "c"]]


def test_no_edge_is_dropped_to_make_the_sort_succeed() -> None:
    ordering: Ordering = order(["a", "b"], _blocks(("a", "b"), ("b", "a")))

    assert ordering.waves == []
    assert set(ordering.unordered) == {"a", "b"}


# --- unexpanded blockers: an order built on a partial graph is not authoritative ---


def test_an_unexpanded_item_and_what_depends_on_it_are_unordered() -> None:
    ordering: Ordering = order(["a", "b", "c"], _blocks(("a", "b"), ("b", "c")), unexpanded={"a"})

    assert ordering.waves == []
    assert ordering.unordered == {
        "a": Unordered.UNEXPANDED,
        "b": Unordered.AFTER_UNEXPANDED,
        "c": Unordered.AFTER_UNEXPANDED,
    }


def test_an_item_with_no_path_from_an_unexpanded_item_is_still_ordered() -> None:
    ordering: Ordering = order(["a", "b", "x"], _blocks(("a", "b")), unexpanded={"a"})

    assert ordering.waves == [["x"]]


def test_an_unexpanded_item_inside_a_cycle_is_reported_as_the_cycle() -> None:
    ordering: Ordering = order(["a", "b"], _blocks(("a", "b"), ("b", "a")), unexpanded={"a"})

    assert ordering.cycles == [["a", "b", "a"]]
    assert ordering.unordered["b"] == Unordered.IN_CYCLE
