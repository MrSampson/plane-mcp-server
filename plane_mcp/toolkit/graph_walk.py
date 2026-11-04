"""Walk work-item relations outward from seeds, and record what could not be read.

Relations are served per item, so a graph over N items is N reads. The walk is
bounded twice: `depth` says how many hops beyond a seed are expanded, and
`max_items` caps how many items are read at all. Whatever the bounds leave
unread is recorded, never dropped: an item whose own blockers were not read
stays in the graph as `unexpanded`, so the ordering cannot call it startable.

Plane is reached only through two callables, so the walk knows nothing of the
SDK or of which instance it is talking to.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from plane.errors.errors import HttpError

from plane_mcp.toolkit.graph import Edge, dedupe, edges_from_relations, related_projects

Buckets = Mapping[str, list[Mapping[str, Any]]]

TOO_MANY_REQUESTS: int = 429


@dataclass(frozen=True)
class Ref:
    """A work item and the project it lives in; reading one needs both."""

    id: str
    project_id: str


@dataclass
class Walk:
    """What was read, and what was not.

    `unexpanded` holds every item whose own relations were not read: those past
    `depth`, those cut by `max_items` or by a rate limit, and those the caller may not
    read. `rate_limited` says the walk stopped because the instance refused further
    requests, not because a bound was reached.
    """

    refs: dict[str, str] = field(default_factory=dict)
    details: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    unexpanded: set[str] = field(default_factory=set)
    unreadable: set[str] = field(default_factory=set)
    cut: list[str] = field(default_factory=list)
    rate_limited: bool = False

    @property
    def truncated(self) -> bool:
        return bool(self.cut)


def walk(
    seeds: list[Ref],
    read_relations: Callable[[Ref], Buckets | None],
    read_node: Callable[[Ref], dict[str, Any] | None],
    depth: int,
    max_items: int,
) -> Walk:
    """Breadth-first over ordering relations; `None` from a reader means unreadable.

    Items within `depth` hops of a seed have their relations read; items at
    `depth + 1` are shown but not expanded. Only relations that order discover new
    items: following `relates_to` would pull in neighbours that cannot affect the
    order, at a call each. A plain relation is kept only if both its ends were
    reached some other way.
    """
    result: Walk = Walk()
    pending: deque[tuple[Ref, int]] = deque()
    for seed in seeds:
        if seed.id not in result.refs:
            result.refs[seed.id] = seed.project_id
            pending.append((seed, 0))

    read: int = 0
    while pending:
        ref: Ref
        hops: int
        ref, hops = pending.popleft()
        if read >= max_items:
            result.cut.append(ref.id)
            result.unexpanded.add(ref.id)
            continue
        read += 1

        try:
            detail: dict[str, Any] | None = read_node(ref)
            if detail is None:
                result.unreadable.add(ref.id)
                result.unexpanded.add(ref.id)
                continue
            result.details[ref.id] = detail

            if hops > depth:
                result.unexpanded.add(ref.id)
                continue
            buckets: Buckets | None = read_relations(ref)
            if buckets is None:
                result.unreadable.add(ref.id)
                result.unexpanded.add(ref.id)
                continue

            edges: list[Edge] = edges_from_relations(ref.id, buckets)
            result.edges.extend(edges)
            projects: dict[str, str] = related_projects(buckets)
            for edge in edges:
                if not edge.type.orders:
                    continue
                other: str = edge.target if edge.source == ref.id else edge.source
                if other not in result.refs:
                    result.refs[other] = projects.get(other, ref.project_id)
                    pending.append((Ref(other, result.refs[other]), hops + 1))
        except HttpError as exc:
            if exc.status_code != TOO_MANY_REQUESTS:
                raise
            # The client's own retries are spent: keep what was read, and report the item
            # in hand and everything still queued as unread.
            result.rate_limited = True
            for unread in [ref, *(queued for queued, _ in pending)]:
                result.cut.append(unread.id)
                result.unexpanded.add(unread.id)
            result.details.pop(ref.id, None)
            break

    result.edges = [e for e in dedupe(result.edges) if e.source in result.refs and e.target in result.refs]
    return result
