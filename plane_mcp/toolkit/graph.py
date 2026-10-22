"""The shape work-item relations make, built from what Plane serves per item.

Plane reports a relation from both ends (the blocker lists the blocked item under
`blocking`, the blocked item lists the blocker under `blocked_by`), so one
relation arrives twice. Every relation is normalised here to a single edge that
points from the item that comes first to the one that comes after, then
deduplicated.

Nothing here calls Plane: the functions take already-read relation payloads.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class EdgeType(StrEnum):
    """What an edge means once both ends have been reconciled."""

    BLOCKING = "blocking"
    START_BEFORE = "start_before"
    FINISH_BEFORE = "finish_before"
    RELATES_TO = "relates_to"
    DUPLICATE = "duplicate"

    @property
    def orders(self) -> bool:
        """Whether the edge constrains which item can be started first."""
        return self not in (EdgeType.RELATES_TO, EdgeType.DUPLICATE)


@dataclass(frozen=True)
class Edge:
    """`source` comes before `target`; for a plain relation the ends are only sorted."""

    source: str
    target: str
    type: EdgeType


# bucket name -> (edge type, whether the item sits at the source end of the edge)
_BUCKETS: dict[str, tuple[EdgeType, bool]] = {
    "blocking": (EdgeType.BLOCKING, True),
    "blocked_by": (EdgeType.BLOCKING, False),
    "start_before": (EdgeType.START_BEFORE, True),
    "start_after": (EdgeType.START_BEFORE, False),
    "finish_before": (EdgeType.FINISH_BEFORE, True),
    "finish_after": (EdgeType.FINISH_BEFORE, False),
    "relates_to": (EdgeType.RELATES_TO, True),
    "duplicate": (EdgeType.DUPLICATE, True),
}


def edges_from_relations(item_id: str, buckets: Mapping[str, Iterable[Mapping[str, Any]]]) -> list[Edge]:
    """The edges one item's relations bucket payload describes.

    A bucket the payload omits is empty. A bucket name this module does not know
    (a custom relation kind, say) is skipped: it carries no ordering the graph can
    honour, and guessing a direction for it would be worse than leaving it out.
    """
    edges: list[Edge] = []
    for bucket, entries in buckets.items():
        if bucket not in _BUCKETS:
            continue
        edge_type: EdgeType
        item_is_source: bool
        edge_type, item_is_source = _BUCKETS[bucket]
        for entry in entries:
            other: str = str(entry["id"])
            if not edge_type.orders:
                source, target = sorted((item_id, other))
            elif item_is_source:
                source, target = item_id, other
            else:
                source, target = other, item_id
            edges.append(Edge(source, target, edge_type))
    return edges


def related_projects(buckets: Mapping[str, Iterable[Mapping[str, Any]]]) -> dict[str, str]:
    """Project of each related item the payload names one for.

    Following a relation into another project needs that project's id, and only
    the entry carries it.
    """
    projects: dict[str, str] = {}
    for entries in buckets.values():
        for entry in entries:
            if entry.get("project_id"):
                projects[str(entry["id"])] = str(entry["project_id"])
    return projects


def dedupe(edges: Iterable[Edge]) -> list[Edge]:
    """The edges with repeats removed, in first-seen order."""
    return list(dict.fromkeys(edges))


class Unordered(StrEnum):
    """Why an item has no place in the waves.

    An order that quietly placed these items would read as authoritative while
    being arbitrary (a cycle) or built on blockers nobody read (a partial graph).
    """

    IN_CYCLE = "in_cycle"
    AFTER_CYCLE = "after_cycle"
    UNEXPANDED = "unexpanded"
    AFTER_UNEXPANDED = "after_unexpanded"


@dataclass(frozen=True)
class Ordering:
    """`waves[0]` is startable now; each later wave waits only on earlier ones."""

    waves: list[list[str]]
    cycles: list[list[str]]
    unordered: dict[str, Unordered]


def order(nodes: Iterable[str], edges: Iterable[Edge], unexpanded: Iterable[str] = ()) -> Ordering:
    """Topological waves over the items, refusing to order what cannot be ordered soundly.

    Only edges that order (blocking, start/finish) count; an edge naming an item
    outside `nodes` is ignored. An item in a cycle, an item in `unexpanded` (whose
    own blockers were never read), and everything reachable from either is listed
    in `unordered` rather than placed in a wave. No edge is ever dropped to make
    the sort succeed.
    """
    members: set[str] = set(nodes)
    successors: dict[str, list[str]] = {node: [] for node in members}
    for edge in edges:
        if edge.type.orders and edge.source in members and edge.target in members:
            successors[edge.source].append(edge.target)
    for targets in successors.values():
        targets.sort()

    cycles: list[list[str]] = _cycles(successors)
    unordered: dict[str, Unordered] = {}
    for cycle in cycles:
        for node in cycle:
            unordered[node] = Unordered.IN_CYCLE
    for node in sorted(set(unexpanded) & members):
        unordered.setdefault(node, Unordered.UNEXPANDED)

    # Cycles first, so an item downstream of both is blamed on the cycle.
    cyclic: list[str] = [n for n, why in unordered.items() if why == Unordered.IN_CYCLE]
    open_ended: list[str] = [n for n, why in unordered.items() if why == Unordered.UNEXPANDED]
    for starts, reason in ((cyclic, Unordered.AFTER_CYCLE), (open_ended, Unordered.AFTER_UNEXPANDED)):
        for node in _reachable(successors, starts):
            unordered.setdefault(node, reason)

    return Ordering(_waves(successors, members - set(unordered)), cycles, unordered)


def _reachable(successors: Mapping[str, list[str]], starts: Iterable[str]) -> set[str]:
    """Items reachable from `starts` along at least one edge."""
    seen: set[str] = set()
    pending: list[str] = [target for start in starts for target in successors[start]]
    while pending:
        node: str = pending.pop()
        if node not in seen:
            seen.add(node)
            pending.extend(successors[node])
    return seen


def _waves(successors: Mapping[str, list[str]], remaining: set[str]) -> list[list[str]]:
    """Kahn's algorithm over `remaining`, which has no cycle and no outside blocker."""
    blockers: dict[str, int] = {node: 0 for node in remaining}
    for node in remaining:
        for target in successors[node]:
            if target in remaining:
                blockers[target] += 1
    waves: list[list[str]] = []
    wave: list[str] = sorted(node for node, count in blockers.items() if count == 0)
    while wave:
        waves.append(wave)
        following: list[str] = []
        for node in wave:
            for target in successors[node]:
                if target in remaining:
                    blockers[target] -= 1
                    if blockers[target] == 0:
                        following.append(target)
        wave = sorted(following)
    return waves


def _cycles(successors: Mapping[str, list[str]]) -> list[list[str]]:
    """One closed path (`[a, b, a]`) per strongly connected component that loops."""
    cycles: list[list[str]] = []
    for component in _components(successors):
        start: str = min(component)
        if len(component) == 1 and start not in successors[start]:
            continue
        cycles.append(_path_back(successors, component, start))
    return sorted(cycles)


def _path_back(successors: Mapping[str, list[str]], component: set[str], start: str) -> list[str]:
    """A path from `start` that returns to it, staying inside `component`."""
    path: list[str] = [start]
    visited: set[str] = set()

    def walk(node: str) -> bool:
        for target in successors[node]:
            if target == start:
                path.append(start)
                return True
            if target in component and target not in visited:
                visited.add(target)
                path.append(target)
                if walk(target):
                    return True
                path.pop()
        return False

    walk(start)
    return path


def _components(successors: Mapping[str, list[str]]) -> list[set[str]]:
    """Strongly connected components (Tarjan), recursion kept shallow by the item cap."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    found: list[set[str]] = []

    def visit(node: str) -> None:
        index[node] = low[node] = len(index)
        stack.append(node)
        on_stack.add(node)
        for target in successors[node]:
            if target not in index:
                visit(target)
                low[node] = min(low[node], low[target])
            elif target in on_stack:
                low[node] = min(low[node], index[target])
        if low[node] == index[node]:
            component: set[str] = set()
            while True:
                member: str = stack.pop()
                on_stack.discard(member)
                component.add(member)
                if member == node:
                    break
            found.append(component)

    for node in sorted(successors):
        if node not in index:
            visit(node)
    return found
