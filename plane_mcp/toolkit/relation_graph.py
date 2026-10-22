"""The `graph` result: the walk, ordered and named for a reader.

Everything Plane-specific is read through the three reader factories at the
bottom; `assemble_graph` takes the readers as arguments so the result's shape
(identifiers, flags, what counts as unordered) is testable without a client.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from plane.errors.errors import HttpError
from plane.models.query_params import RetrieveQueryParams, WorkItemQueryParams

from plane_mcp.toolkit.graph import Edge, Ordering, mermaid, order
from plane_mcp.toolkit.graph_walk import Buckets, Ref, Walk, walk

FORBIDDEN: int = 403
MODULE_PAGE_SIZE: int = 100

# Whether a project's identifier can be read; None when the caller may not read it.
IdentifierLookup = Callable[[str], str | None]


def assemble_graph(
    seeds: list[Ref],
    read_relations: Callable[[Ref], Buckets | None],
    read_node: Callable[[Ref], dict[str, Any] | None],
    project_identifier: IdentifierLookup,
    *,
    depth: int,
    max_items: int,
    module_id: str,
    diagram: bool,
) -> dict[str, Any]:
    """Walk from `seeds` and answer nodes, edges, waves, cycles and what could not be ordered.

    Everything that identifies an item uses its `PROJ-N` identifier, falling back
    to the raw id for an item that could not be read (its sequence number is
    unknown). `module_ids` is set only for the seeds of a module walk: no payload
    Plane serves says which module a neighbour belongs to.
    """
    found: Walk = walk(seeds, read_relations, read_node, depth, max_items)
    ordering: Ordering = order(found.refs, found.edges, found.unexpanded)

    names: dict[str, str] = {item_id: _identifier(item_id, found, project_identifier) for item_id in found.refs}
    seed_ids: set[str] = {seed.id for seed in seeds}
    nodes: list[dict[str, Any]] = []
    for item_id, project_id in found.refs.items():
        detail: dict[str, Any] = found.details.get(item_id, {})
        node: dict[str, Any] = {
            "id": item_id,
            "identifier": names[item_id],
            "name": detail.get("name"),
            "state": detail.get("state"),
            "priority": detail.get("priority"),
            "project_id": project_id,
            "project": project_identifier(project_id),
            "boundary": item_id in found.unexpanded,
            "unreadable": item_id in found.unreadable,
        }
        if module_id and item_id in seed_ids:
            node["module_ids"] = [module_id]
        nodes.append(node)

    result: dict[str, Any] = {
        "nodes": nodes,
        "edges": [{"from": names[e.source], "to": names[e.target], "type": str(e.type)} for e in found.edges],
        "order": [[names[item_id] for item_id in wave] for wave in ordering.waves],
        "unordered": {names[item_id]: str(why) for item_id, why in ordering.unordered.items()},
        "cycles": [[names[item_id] for item_id in cycle] for cycle in ordering.cycles],
        "truncated": found.truncated,
        "cut": [names[item_id] for item_id in found.cut],
    }
    if diagram:
        result["diagram"] = mermaid(
            names,
            [Edge(e.source, e.target, e.type) for e in found.edges],
            flagged={item_id for item_id in ordering.unordered},
        )
    return result


def _identifier(item_id: str, found: Walk, project_identifier: IdentifierLookup) -> str:
    sequence_id: Any = found.details.get(item_id, {}).get("sequence_id")
    prefix: str | None = project_identifier(found.refs[item_id])
    return f"{prefix}-{sequence_id}" if prefix and sequence_id is not None else item_id


def _forbidden(exc: HttpError) -> bool:
    return exc.status_code == FORBIDDEN


def node_reader(client: Any, workspace_slug: str) -> Callable[[Ref], dict[str, Any] | None]:
    """Reads an item's name, state, priority and sequence number; None when forbidden.

    The state is requested expanded because a listing carries only its id. Any
    other failure -- a 404 for an id that does not exist, say -- propagates:
    only "you may not read this" is a fact about the graph rather than an error.
    """

    def read(ref: Ref) -> dict[str, Any] | None:
        try:
            item: Any = client.work_items.retrieve(
                workspace_slug=workspace_slug,
                project_id=ref.project_id,
                work_item_id=ref.id,
                params=RetrieveQueryParams(expand="state"),
            )
        except HttpError as exc:
            if _forbidden(exc):
                return None
            raise
        state: Any = getattr(item, "state", None)
        return {
            "name": getattr(item, "name", None),
            "state": getattr(state, "name", state),
            "priority": getattr(item, "priority", None),
            "sequence_id": getattr(item, "sequence_id", None),
            "project_id": ref.project_id,
        }

    return read


def project_identifier_reader(client: Any, workspace_slug: str) -> IdentifierLookup:
    """A project's identifier (`PROJ`), fetched once per project; None when unreadable."""
    cache: dict[str, str | None] = {}

    def lookup(project_id: str) -> str | None:
        if project_id not in cache:
            try:
                cache[project_id] = client.projects.retrieve(
                    workspace_slug=workspace_slug, project_id=project_id
                ).identifier
            except HttpError as exc:
                if not _forbidden(exc):
                    raise
                cache[project_id] = None
        return cache[project_id]

    return lookup


def relations_reader(
    fetch: Callable[[Ref], Mapping[str, list[Any]]],
) -> Callable[[Ref], Buckets | None]:
    """Wraps a relations fetch so a forbidden item answers None instead of raising."""

    def read(ref: Ref) -> Buckets | None:
        try:
            return fetch(ref)  # type: ignore[return-value]
        except HttpError as exc:
            if _forbidden(exc):
                return None
            raise

    return read


def module_seeds(client: Any, workspace_slug: str, project_id: str, module_id: str, limit: int) -> list[Ref]:
    """The items of a module, read page by page until one more than `limit` is in hand.

    One extra is enough to know the module is larger than the cap; the walk then
    cuts it and reports the truncation.
    """
    seeds: list[Ref] = []
    cursor: str | None = None
    while len(seeds) <= limit:
        page: Any = client.modules.list_work_items(
            workspace_slug=workspace_slug,
            project_id=project_id,
            module_id=module_id,
            params=WorkItemQueryParams(per_page=MODULE_PAGE_SIZE, cursor=cursor),
        )
        seeds.extend(Ref(str(item.id), str(getattr(item, "project", None) or project_id)) for item in page.results)
        if not page.next_page_results or not page.next_cursor:
            break
        cursor = page.next_cursor
    return seeds[: limit + 1]
