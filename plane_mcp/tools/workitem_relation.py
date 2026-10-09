"""Relations between work items, and the workspace definitions that type them.

Two systems behind one tool: built-in dependencies (six fixed directional types)
and custom relations (workspace-defined, each with an outward and inward label).
`create` routes between them by which arguments are supplied.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, get_args

from fastmcp import FastMCP
from plane.errors.errors import HttpError
from plane.models.enums import WorkItemRelationTypeEnum
from plane.models.work_item_relation_definitions import (
    CreateWorkItemRelationDefinition,
    PaginatedWorkItemRelationDefinitionResponse,
    UpdateWorkItemRelationDefinition,
    WorkItemRelationDefinition,
)
from plane.models.work_items import (
    CreateWorkItemCustomRelation,
    CreateWorkItemDependency,
    CreateWorkItemRelation,
    DependencyTypeEnum,
    RemoveWorkItemRelation,
)

from plane_mcp.client import get_plane_client_context
from plane_mcp.toolkit import (
    Action,
    build_annotations,
    build_description,
    coerce_list,
    missing,
    needs,
    one_of,
    opt,
    or_fallback,
    route_absent,
)

NAME = "workitem_relation"
TITLE = "Work item relations"

DEPENDENCY_TYPES: tuple[str, ...] = get_args(DependencyTypeEnum)

# Symmetric relations the unified `/relations/` endpoint carries but the dependency
# endpoint (and its request model) does not: "related to" and "duplicate". Derived from
# the SDK's full enum so a type the SDK adds shows up here instead of being refused.
PLAIN_TYPES: tuple[str, ...] = tuple(t for t in get_args(WorkItemRelationTypeEnum) if t not in DEPENDENCY_TYPES)
RELATION_TYPES: tuple[str, ...] = (*DEPENDENCY_TYPES, *PLAIN_TYPES)

_OTHER_RELATIONS = (
    "For any other relationship pass relation_definition_id and "
    "relation_definition_label from the list_definitions action."
)

_DEFINITIONS_UNAVAILABLE = (
    "Error: custom relation definitions are not available on this instance; pass a built-in relation_type instead."
)

_DEPENDENCY_FALLBACK_NOTE = (
    "Served via this instance's unified relations endpoint: each entry is the related work item's "
    "id (with its project_id when the instance reports one), not the full work item, and the "
    "buckets may include duplicate/relates_to alongside the built-in dependency types."
)


_CUSTOM_LIST_UNAVAILABLE_NOTE = (
    "Custom relation definitions are not available on this instance, so 'custom' is empty "
    "because that capability is absent here -- not because no custom relations exist."
)

# Read-shaped counterpart to _DEFINITIONS_UNAVAILABLE: this call succeeds (it still
# answers built_in_dependencies), so its note must not read like an "Error:" -- and
# it has no relation_type parameter of its own to "pass instead" the way create does.
_DEFINITIONS_ABSENT_NOTE = (
    "Custom relation definitions are not available on this instance, so custom_definitions is "
    "empty because that capability is absent here -- not because none are defined. Use a "
    "built_in_dependencies or plain_relations value in relation_type."
)

# The instance serves neither the built-in nor the unified surface for this
# operation -- distinct from _DEFINITIONS_UNAVAILABLE, which means only the
# custom-relation half is missing while a fallback still exists.
_CREATE_UNAVAILABLE_EVERYWHERE = (
    "Error: this instance serves neither the built-in dependency endpoint nor the unified "
    "relations endpoint; the relation could not be created."
)

_DELETE_UNAVAILABLE_EVERYWHERE = (
    "Error: this instance serves neither the expected removal endpoint nor the unified "
    "relations endpoint; the relation could not be removed."
)

ACTIONS = (
    Action("list", ("project_id", "workitem_id"), read=True),
    Action(
        "create",
        ("project_id", "workitem_id", "workitem_ids"),
        ("relation_type", "relation_definition_id", "relation_definition_label"),
        note="pass relation_type for a dependency or a plain relation (relates_to, duplicate), or "
        "definition id + label for a custom relation; a relation created by mistake may not be "
        "removable through this tool on a self-hosted instance; "
        "workitem_ids may name work items in any project of the workspace, not just the one "
        "project_id names",
    ),
    Action(
        "delete",
        ("project_id", "workitem_id", "related_workitem_id"),
        ("is_dependency",),
        note="removes one relation; dependencies and custom relations are independent, so "
        "is_dependency must match the kind that was created (default false) -- moot on an "
        "instance with no custom-relation surface, where either value succeeds",
        destructive=True,
    ),
    Action("list_definitions", optional=("is_default", "is_active"), read=True),
    Action("create_definition", ("name",), ("outward", "inward", "is_active", "color")),
    Action("update_definition", ("definition_id",), ("name", "outward", "inward", "is_active", "color")),
    Action("delete_definition", ("definition_id",), destructive=True),
)

FOOTER = (
    "Call list_definitions first and match the user's wording to an entry. A "
    f"built_in_dependencies value ({', '.join(DEPENDENCY_TYPES)}) or plain_relations value "
    f"({', '.join(PLAIN_TYPES)}) goes in relation_type; a "
    "custom definition needs its id in relation_definition_id and the matched outward or "
    "inward label in relation_definition_label, which sets direction."
)

LEGACY = {
    "list_work_item_relations": "list",
    "create_work_item_relation": "create",
    "remove_work_item_relation": "delete",
    "list_work_item_relation_definitions": "list_definitions",
    "create_work_item_relation_definition": "create_definition",
    "update_work_item_relation_definition": "update_definition",
    "delete_work_item_relation_definition": "delete_definition",
}


def _or_unavailable(call: Callable[[], Any]) -> Any:
    """Run `call`; a route-absent 404 answers the standard message instead of raising."""
    return or_fallback(call, lambda: _DEFINITIONS_UNAVAILABLE)


def _related_item(entry: Any) -> Any:
    """One relations-bucket entry as `{id, project_id}`, the shape of the dependency path.

    The unified endpoint answers bare ids on some builds and `{project_id, issue_id}`
    objects on others (self-hosted CE 1.4). The project is kept because following a
    relation into another project needs it. Only `issue_id` names the related work
    item -- a bare `id` could be the relation row's own -- so an object without one is
    returned whole rather than guessed at or dropped.
    """
    if isinstance(entry, str):
        return {"id": entry}
    if isinstance(entry, dict) and entry.get("issue_id"):
        related: dict[str, str] = {"id": str(entry["issue_id"])}
        if entry.get("project_id"):
            related["project_id"] = str(entry["project_id"])
        return related
    return entry


def _unified_relations(client: Any, workspace_slug: str, project_id: str, work_item_id: str) -> dict[str, list[Any]]:
    """Every relation bucket of a work item from the unified `/relations/` endpoint.

    Goes around `client.work_items.relations.list`: that method validates the reply
    against a model typing every bucket `list[str]`, which rejects the object entries
    CE returns as soon as any bucket is populated. The private `_get` is the only
    way to the raw body; see #31 for returning to the public call.
    """
    raw: Any = client.work_items.relations._get(
        endpoint=f"{workspace_slug}/projects/{project_id}/work-items/{work_item_id}/relations"
    )
    if not isinstance(raw, dict):
        raise ValueError(f"the relations endpoint answered {type(raw).__name__}, not an object of buckets")
    return {bucket: [_related_item(entry) for entry in entries or []] for bucket, entries in raw.items()}


def _all_definitions(client, workspace_slug: str, is_default, is_active) -> list[WorkItemRelationDefinition]:
    """Definitions are a small set an agent must see whole, so page through them."""
    results: list[WorkItemRelationDefinition] = []
    cursor: str | None = None
    while True:
        page: PaginatedWorkItemRelationDefinitionResponse = client.work_item_relation_definitions.list(
            workspace_slug=workspace_slug,
            is_default=is_default,
            is_active=is_active,
            per_page=100,
            cursor=cursor,
        )
        results.extend(page.results)
        cursor = page.next_cursor
        if not page.next_page_results or not cursor:
            return results


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name=NAME,
        description=build_description(
            "Relations between work items, and the definitions that type them.", ACTIONS, FOOTER
        ),
        annotations=build_annotations(TITLE, ACTIONS),
    )
    def workitem_relation(
        action: Literal[
            "list",
            "create",
            "delete",
            "list_definitions",
            "create_definition",
            "update_definition",
            "delete_definition",
        ],
        project_id: str = "",
        workitem_id: str = "",
        workitem_ids: list[str] | None = None,
        related_workitem_id: str = "",
        relation_type: str = "",
        relation_definition_id: str = "",
        relation_definition_label: str = "",
        definition_id: str = "",
        name: str = "",
        outward: str = "",
        inward: str = "",
        color: str = "",
        # Tri-state: False is a real filter value, distinct from "no filter".
        is_default: bool | None = None,
        is_active: bool | None = None,
        is_dependency: bool = False,
    ) -> Any:
        client, workspace_slug = get_plane_client_context()

        if action == "list_definitions":
            try:
                custom_definitions = [
                    d.model_dump() for d in _all_definitions(client, workspace_slug, is_default, is_active)
                ]
            except HttpError as exc:
                if not route_absent(exc):
                    raise
                return {
                    "built_in_dependencies": list(DEPENDENCY_TYPES),
                    "plain_relations": list(PLAIN_TYPES),
                    "custom_definitions": [],
                    "note": _DEFINITIONS_ABSENT_NOTE,
                }
            return {
                "built_in_dependencies": list(DEPENDENCY_TYPES),
                "plain_relations": list(PLAIN_TYPES),
                "custom_definitions": custom_definitions,
            }

        if action == "create_definition":
            if not name:
                return missing(action, "name")
            return _or_unavailable(
                lambda: client.work_item_relation_definitions.create(
                    workspace_slug=workspace_slug,
                    data=CreateWorkItemRelationDefinition(
                        name=name,
                        outward=opt(outward),
                        inward=opt(inward),
                        is_active=is_active,
                        color=opt(color),
                    ),
                )
            )

        if action in ("update_definition", "delete_definition"):
            if not definition_id:
                return missing(action, "definition_id")

            def _write_definition() -> Any:
                if action == "update_definition":
                    return client.work_item_relation_definitions.update(
                        workspace_slug=workspace_slug,
                        definition_id=definition_id,
                        data=UpdateWorkItemRelationDefinition(
                            name=opt(name),
                            outward=opt(outward),
                            inward=opt(inward),
                            is_active=is_active,
                            color=opt(color),
                        ),
                    )
                client.work_item_relation_definitions.delete(workspace_slug=workspace_slug, definition_id=definition_id)
                return None

            return _or_unavailable(_write_definition)

        if error := needs(action, project_id=project_id, workitem_id=workitem_id):
            return error

        if action == "list":
            notes: list[str] = []

            def _dependencies_fallback() -> dict[str, Any]:
                notes.append(_DEPENDENCY_FALLBACK_NOTE)
                return _unified_relations(client, workspace_slug, project_id, workitem_id)

            dependencies = or_fallback(
                lambda: client.work_items.dependencies.list(
                    workspace_slug=workspace_slug, project_id=project_id, work_item_id=workitem_id
                ).model_dump(),
                _dependencies_fallback,
            )

            def _custom_fallback() -> dict[str, list[dict[str, Any]]]:
                notes.append(_CUSTOM_LIST_UNAVAILABLE_NOTE)
                return {}

            custom = or_fallback(
                lambda: {
                    label: [item.model_dump() for item in items]
                    for label, items in client.work_items.custom_relations.list(
                        workspace_slug=workspace_slug, project_id=project_id, work_item_id=workitem_id
                    ).items()
                },
                _custom_fallback,
            )
            result: dict[str, Any] = {"dependencies": dependencies, "custom": custom}
            if notes:
                result["note"] = " ".join(notes)
            return result

        if action == "create":
            targets = coerce_list(workitem_ids)
            if not targets:
                return missing(action, "workitem_ids")
            if relation_type:
                if error := one_of("relation_type", relation_type, RELATION_TYPES, _OTHER_RELATIONS):
                    return error

                def _create_relations() -> None:
                    # relations.create forwards an unvalidated raw response body (the SDK
                    # types it None but its body is `return self._post(...)`) -- discard it
                    # so a successful create answers the same way regardless of which path
                    # served it, rather than sometimes structured, sometimes not.
                    client.work_items.relations.create(
                        workspace_slug=workspace_slug,
                        project_id=project_id,
                        work_item_id=workitem_id,
                        data=CreateWorkItemRelation(
                            relation_type=relation_type,  # type: ignore[arg-type]
                            issues=targets,
                        ),
                    )
                    return None

                def _create_fallback() -> None | str:
                    return or_fallback(_create_relations, lambda: _CREATE_UNAVAILABLE_EVERYWHERE)

                if relation_type in PLAIN_TYPES:
                    # The dependency endpoint has no such direction, so go straight to
                    # the unified one rather than send it a type it would reject.
                    return _create_fallback()

                return or_fallback(
                    lambda: client.work_items.dependencies.create(
                        workspace_slug=workspace_slug,
                        project_id=project_id,
                        work_item_id=workitem_id,
                        data=CreateWorkItemDependency(
                            relation_type=relation_type,  # type: ignore[arg-type]
                            work_item_ids=targets,
                        ),
                    ),
                    _create_fallback,
                )
            if relation_definition_id and relation_definition_label:
                # create's note advertises cross-project targets for workitem_ids
                # unqualified, but that claim rests on the probe recorded in issue #1,
                # which only exercised the dependency branch above. Nothing has
                # confirmed the same acceptance here, on the custom-relation path --
                # only that this call carries the id through unchanged.
                return _or_unavailable(
                    lambda: client.work_items.custom_relations.create(
                        workspace_slug=workspace_slug,
                        project_id=project_id,
                        work_item_id=workitem_id,
                        data=CreateWorkItemCustomRelation(
                            relation_definition_id=relation_definition_id,
                            relation_definition_type=relation_definition_label,
                            work_item_ids=targets,
                        ),
                    )
                )
            return (
                "Error: provide relation_type for a built-in dependency, or both "
                "relation_definition_id and relation_definition_label for a custom relation. "
                "Call the list_definitions action to find one."
            )

        if not related_workitem_id:
            return missing(action, "related_workitem_id")

        # Whichever kind is missing on this instance -- dependencies (is_dependency=True)
        # or custom relations (the default) -- the unified relations surface is the only
        # other place a deletable relation could be; that is true for either value of
        # is_dependency on an instance that lacks that kind's own removal endpoint.
        primary = client.work_items.dependencies.remove if is_dependency else client.work_items.custom_relations.remove

        def _delete_relations() -> None:
            client.work_items.relations.delete(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=workitem_id,
                data=RemoveWorkItemRelation(related_issue=related_workitem_id),
            )
            return None

        return or_fallback(
            lambda: primary(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=workitem_id,
                related_work_item_id=related_workitem_id,
            ),
            lambda: or_fallback(_delete_relations, lambda: _DELETE_UNAVAILABLE_EVERYWHERE),
        )
