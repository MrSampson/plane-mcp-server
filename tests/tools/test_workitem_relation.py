"""Self-hosted Plane CE serves relations at a different path than Cloud.

`dependencies` and `custom_relations` route to `/dependencies/` and
`/work-item-relations/`, which 404 on CE. The unified `/relations/` surface
works on both. Every dispatch branch here must try the `dependencies` /
`custom_relations` path first (so Cloud is unaffected) and fall back to
`relations` only on a 404 that means "this route does not exist here" --
never on a 404 that means "this particular id does not exist".
"""

from __future__ import annotations

from typing import Any, get_args

import pytest
from plane.errors.errors import HttpError
from plane.models.enums import WorkItemRelationTypeEnum

from plane_mcp.toolkit.governance import ROUTE_ABSENT_ERROR
from plane_mcp.tools.workitem_relation import (
    _CREATE_PLAIN_UNAVAILABLE,
    DEPENDENCY_TYPES,
    FOOTER,
    PLAIN_TYPES,
)

ROUTE_ABSENT = HttpError("Not Found", status_code=404, response={"error": ROUTE_ABSENT_ERROR})
ID_NOT_FOUND = HttpError("Not Found", status_code=404, response={"detail": "Not found."})
# A 404 from something upstream of Plane itself (a reverse proxy's own error page,
# say) carries no JSON body at all -- it must not be mistaken for Plane's own
# routing signal.
NON_JSON_404 = HttpError("Not Found", status_code=404, response="<html>404 Not Found</html>")


def test_route_absent_requires_a_dict_body(registered, spy):
    spy.returns["work_items.dependencies.list"] = NON_JSON_404

    with pytest.raises(HttpError):
        registered["workitem_relation"].fn(action="list", project_id="proj-1", workitem_id="wi-1")


# --- list ---


def test_list_uses_dependencies_when_the_route_exists(registered, spy):
    registered["workitem_relation"].fn(action="list", project_id="proj-1", workitem_id="wi-1")

    assert spy.recorder.methods == ["work_items.dependencies.list", "work_items.custom_relations.list"]


def test_list_falls_back_to_relations_when_dependencies_route_is_absent(registered, spy):
    """The fallback's shape genuinely differs from the primary path -- bare id
    strings under 8 keys (incl. duplicate/relates_to) instead of rich objects
    under 6 -- so a caller needs telling, not just a silently different dict."""
    spy.returns["work_items.dependencies.list"] = ROUTE_ABSENT

    spy.returns["work_items.relations._get"] = CE_RELATIONS
    result = registered["workitem_relation"].fn(action="list", project_id="proj-1", workitem_id="wi-1")

    assert "work_items.relations._get" in spy.recorder.methods
    fallback = spy.recorder.calls[spy.recorder.methods.index("work_items.relations._get")]
    assert fallback.kwargs["endpoint"] == "acme/projects/proj-1/work-items/wi-1/relations"
    assert "related work item's id" in result["note"]


# What a self-hosted CE 1.4 `/relations/` answers: each bucket holds objects, not ids.
CE_RELATIONS = {
    "blocking": [{"project_id": "proj-2", "issue_id": "wi-2"}],
    "blocked_by": [],
    "duplicate": [],
    "relates_to": [{"project_id": "proj-1", "issue_id": "wi-3"}, {"project_id": "proj-3", "issue_id": "wi-4"}],
    "start_after": [],
    "start_before": [],
    "finish_after": [],
    "finish_before": [],
}


def _list_on_ce(registered: dict[str, Any], spy: Any, relations: Any) -> dict[str, Any]:
    spy.returns["work_items.dependencies.list"] = ROUTE_ABSENT
    spy.returns["work_items.relations._get"] = relations
    return registered["workitem_relation"].fn(action="list", project_id="proj-1", workitem_id="wi-1")


def test_list_reads_the_objects_ce_returns_instead_of_failing_validation(registered, spy) -> None:
    """CE answers {project_id, issue_id} objects; the SDK model types every bucket
    list[str], so going through it raised a ValidationError on any populated bucket."""
    result = _list_on_ce(registered, spy, CE_RELATIONS)

    assert result["dependencies"]["blocking"] == [{"id": "wi-2", "project_id": "proj-2"}]
    assert result["dependencies"]["relates_to"] == [
        {"id": "wi-3", "project_id": "proj-1"},
        {"id": "wi-4", "project_id": "proj-3"},
    ]


def test_list_keeps_the_project_of_a_related_item_in_another_project(registered, spy) -> None:
    """`workitem retrieve` needs a project_id, so an id alone cannot be followed across projects."""
    result = _list_on_ce(registered, spy, CE_RELATIONS)

    assert result["dependencies"]["blocking"][0]["project_id"] != "proj-1"


def test_list_keeps_every_bucket_the_endpoint_returned(registered, spy) -> None:
    result = _list_on_ce(registered, spy, CE_RELATIONS)

    assert set(result["dependencies"]) == set(CE_RELATIONS)
    assert result["dependencies"]["duplicate"] == []


def test_list_gives_bare_ids_the_same_shape(registered, spy) -> None:
    result = _list_on_ce(registered, spy, {**CE_RELATIONS, "blocking": ["wi-9"]})

    assert result["dependencies"]["blocking"] == [{"id": "wi-9"}]


def test_list_does_not_drop_an_entry_it_cannot_read(registered, spy) -> None:
    """An object with no recognisable id must surface, not vanish into a shorter list."""
    odd = {"project_id": "proj-2", "something": "else"}

    result = _list_on_ce(registered, spy, {**CE_RELATIONS, "blocking": [odd]})

    assert result["dependencies"]["blocking"] == [odd]


def test_list_does_not_mistake_a_relation_row_id_for_a_work_item_id(registered, spy) -> None:
    """Only `issue_id` names the related work item; a bare `id` could be the relation row's own."""
    row = {"id": "relation-row-1", "project_id": "proj-2"}

    result = _list_on_ce(registered, spy, {**CE_RELATIONS, "blocking": [row]})

    assert result["dependencies"]["blocking"] == [row]


@pytest.mark.parametrize("body", [None, "<html>ok</html>", []])
def test_list_refuses_a_body_that_is_not_a_relations_object(body, registered, spy) -> None:
    """Answering empty here would read as "no relations" -- a proxy page or an empty
    reply must fail loudly instead."""
    with pytest.raises(ValueError, match="relations"):
        _list_on_ce(registered, spy, body)


def test_list_tolerates_a_bucket_that_is_null(registered, spy) -> None:
    result = _list_on_ce(registered, spy, {**CE_RELATIONS, "blocking": None})

    assert result["dependencies"]["blocking"] == []


def test_list_does_not_note_anything_when_dependencies_route_exists(registered, spy):
    result = registered["workitem_relation"].fn(action="list", project_id="proj-1", workitem_id="wi-1")

    assert "note" not in result


def test_list_propagates_a_genuine_dependencies_error(registered, spy):
    spy.returns["work_items.dependencies.list"] = ID_NOT_FOUND

    with pytest.raises(HttpError):
        registered["workitem_relation"].fn(action="list", project_id="proj-1", workitem_id="wi-1")


def test_list_custom_returns_empty_when_its_route_is_absent(registered, spy):
    """An empty `{}` here is indistinguishable from "this item genuinely has no
    custom relations" unless a note says otherwise -- `list_definitions` already
    notes the identical gap; `list` must too."""
    spy.returns["work_items.custom_relations.list"] = ROUTE_ABSENT

    result = registered["workitem_relation"].fn(action="list", project_id="proj-1", workitem_id="wi-1")

    assert result["custom"] == {}
    assert "not available on this instance" in result["note"]


def test_list_custom_propagates_a_genuine_error(registered, spy):
    spy.returns["work_items.custom_relations.list"] = ID_NOT_FOUND

    with pytest.raises(HttpError):
        registered["workitem_relation"].fn(action="list", project_id="proj-1", workitem_id="wi-1")


def test_list_notes_both_gaps_when_the_whole_ce_scenario_fires_at_once(registered, spy):
    """The realistic CE case: neither `dependencies` nor `custom_relations`
    exist, only the unified `relations` endpoint does."""
    spy.returns["work_items.dependencies.list"] = ROUTE_ABSENT
    spy.returns["work_items.custom_relations.list"] = ROUTE_ABSENT

    spy.returns["work_items.relations._get"] = CE_RELATIONS
    result = registered["workitem_relation"].fn(action="list", project_id="proj-1", workitem_id="wi-1")

    assert "work_items.relations._get" in spy.recorder.methods
    assert result["custom"] == {}
    assert "related work item's id" in result["note"]
    assert "not available on this instance" in result["note"]
    assert "note" in result


# --- create: built-in dependency ---


def test_create_uses_dependencies_when_the_route_exists(registered, spy):
    registered["workitem_relation"].fn(
        action="create",
        project_id="proj-1",
        workitem_id="wi-1",
        workitem_ids=["wi-2"],
        relation_type="blocking",
    )

    assert spy.recorder.only().method == "work_items.dependencies.create"


def test_create_falls_back_to_relations_when_dependencies_route_is_absent(registered, spy):
    """`relations.create` forwards an unvalidated raw response body (the SDK
    types it `None` but its body is `return self._post(...)`), unlike
    `dependencies.create`'s parsed `list[WorkItemWithRelationType]`. Passing
    that raw body straight through would make a successful create's return
    shape silently path-dependent, so the fallback discards it and always
    answers None -- one defined shape for a create that cannot report back
    what it created, rather than sometimes-structured, sometimes-not."""
    spy.returns["work_items.dependencies.create"] = ROUTE_ABSENT

    result = registered["workitem_relation"].fn(
        action="create",
        project_id="proj-1",
        workitem_id="wi-1",
        workitem_ids=["wi-2"],
        relation_type="blocking",
    )

    assert spy.recorder.methods[-1] == "work_items.relations.create"
    fallback = spy.recorder.calls[-1]
    data = fallback.kwargs["data"]
    assert data.relation_type == "blocking"
    assert data.issues == ["wi-2"]
    assert result is None


def test_create_propagates_a_genuine_dependencies_error(registered, spy):
    spy.returns["work_items.dependencies.create"] = ID_NOT_FOUND

    with pytest.raises(HttpError):
        registered["workitem_relation"].fn(
            action="create",
            project_id="proj-1",
            workitem_id="wi-1",
            workitem_ids=["wi-2"],
            relation_type="blocking",
        )


def test_create_reports_when_neither_dependency_nor_relations_route_exists(registered, spy):
    """If the unified surface turns out not to exist on some CE version either,
    a bare 404 propagated from the fallback would read exactly like issue #1's
    original, unfixed symptom -- indistinguishable from "the fix didn't work"."""
    spy.returns["work_items.dependencies.create"] = ROUTE_ABSENT
    spy.returns["work_items.relations.create"] = ROUTE_ABSENT

    result = registered["workitem_relation"].fn(
        action="create",
        project_id="proj-1",
        workitem_id="wi-1",
        workitem_ids=["wi-2"],
        relation_type="blocking",
    )

    assert isinstance(result, str) and result.startswith("Error:")


def test_create_propagates_a_genuine_error_from_the_relations_fallback(registered, spy):
    spy.returns["work_items.dependencies.create"] = ROUTE_ABSENT
    spy.returns["work_items.relations.create"] = ID_NOT_FOUND

    with pytest.raises(HttpError):
        registered["workitem_relation"].fn(
            action="create",
            project_id="proj-1",
            workitem_id="wi-1",
            workitem_ids=["wi-2"],
            relation_type="blocking",
        )


@pytest.mark.parametrize("relation_type", DEPENDENCY_TYPES)
def test_create_dependency_target_is_not_restricted_to_the_source_project(relation_type, registered, spy):
    """This tool applies no client-side restriction on which project a target
    work item belongs to, or on which relation_type is used to link them --
    `project_id` is always the source item's, and the create call routes the
    same way regardless of direction. Whether the live API accepts a
    cross-project target this way is established by the probe recorded in
    issue #1, not by this test."""
    registered["workitem_relation"].fn(
        action="create",
        project_id="proj-1",
        workitem_id="wi-1",
        workitem_ids=["other-project-wi-9"],
        relation_type=relation_type,
    )

    call = spy.recorder.only()
    assert call.kwargs["project_id"] == "proj-1"
    assert call.kwargs["data"].work_item_ids == ["other-project-wi-9"]
    assert call.kwargs["data"].relation_type == relation_type


# --- create: plain relations (relates_to, duplicate) ---


def test_plain_types_cover_every_sdk_type_the_dependency_endpoint_lacks() -> None:
    """A type the SDK adds must be decided on (plain or directional), not silently routed."""
    assert set(get_args(WorkItemRelationTypeEnum)) == set(DEPENDENCY_TYPES) | set(PLAIN_TYPES)
    assert set(PLAIN_TYPES) == {"relates_to", "duplicate"}


def test_footer_advertises_the_plain_types() -> None:
    for relation_type in PLAIN_TYPES:
        assert relation_type in FOOTER


@pytest.mark.parametrize("relation_type", PLAIN_TYPES)
def test_create_plain_relation_goes_to_the_unified_endpoint_only(
    relation_type: str, registered: dict[str, Any], spy: Any
) -> None:
    result = registered["workitem_relation"].fn(
        action="create",
        project_id="proj-1",
        workitem_id="wi-1",
        workitem_ids=["other-project-wi-9"],
        relation_type=relation_type,
    )

    call = spy.recorder.only()
    assert call.method == "work_items.relations.create"
    assert call.kwargs["data"].relation_type == relation_type
    assert call.kwargs["data"].issues == ["other-project-wi-9"]
    assert result is None


def test_create_plain_relation_reports_when_the_unified_route_is_absent(registered: dict[str, Any], spy: Any) -> None:
    spy.returns["work_items.relations.create"] = ROUTE_ABSENT

    result = registered["workitem_relation"].fn(
        action="create", project_id="proj-1", workitem_id="wi-1", workitem_ids=["wi-2"], relation_type="relates_to"
    )

    assert result == _CREATE_PLAIN_UNAVAILABLE
    assert "dependency endpoint" not in result


def test_create_plain_relation_propagates_a_genuine_error(registered: dict[str, Any], spy: Any) -> None:
    spy.returns["work_items.relations.create"] = ID_NOT_FOUND

    with pytest.raises(HttpError):
        registered["workitem_relation"].fn(
            action="create", project_id="proj-1", workitem_id="wi-1", workitem_ids=["wi-2"], relation_type="duplicate"
        )


def test_create_still_refuses_an_unknown_relation_type(registered: dict[str, Any], spy: Any) -> None:
    result = registered["workitem_relation"].fn(
        action="create", project_id="proj-1", workitem_id="wi-1", workitem_ids=["wi-2"], relation_type="parent_of"
    )

    assert isinstance(result, str) and result.startswith("Error:")
    assert all(t in result for t in (*DEPENDENCY_TYPES, *PLAIN_TYPES))
    assert spy.recorder.calls == []


def test_create_with_neither_type_nor_definition_names_plain_relations(registered: dict[str, Any], spy: Any) -> None:
    result = registered["workitem_relation"].fn(
        action="create", project_id="proj-1", workitem_id="wi-1", workitem_ids=["wi-2"]
    )

    assert "plain relation" in result
    assert spy.recorder.calls == []


def test_list_definitions_offers_the_plain_relations(registered: dict[str, Any], spy: Any) -> None:
    result = registered["workitem_relation"].fn(action="list_definitions")

    assert result["plain_relations"] == list(PLAIN_TYPES)
    assert result["built_in_dependencies"] == list(DEPENDENCY_TYPES)


def test_create_advertises_cross_project_targets(resource_modules):
    """The capability above is silent to a calling agent unless the tool's own
    description says so -- an agent has no reason to assume workitem_ids can
    cross projects unless told."""
    workitem_relation = next(m for m in resource_modules if m.NAME == "workitem_relation")
    create = next(a for a in workitem_relation.ACTIONS if a.name == "create")

    assert "any project" in create.note


# --- create: custom relation ---


def test_create_custom_relation_reports_when_its_route_is_absent(registered, spy):
    spy.returns["work_items.custom_relations.create"] = ROUTE_ABSENT

    result = registered["workitem_relation"].fn(
        action="create",
        project_id="proj-1",
        workitem_id="wi-1",
        workitem_ids=["wi-2"],
        relation_definition_id="def-1",
        relation_definition_label="implements",
    )

    assert isinstance(result, str) and result.startswith("Error:")
    assert "not available on this instance" in result


def test_create_custom_relation_propagates_a_genuine_error(registered, spy):
    spy.returns["work_items.custom_relations.create"] = ID_NOT_FOUND

    with pytest.raises(HttpError):
        registered["workitem_relation"].fn(
            action="create",
            project_id="proj-1",
            workitem_id="wi-1",
            workitem_ids=["wi-2"],
            relation_definition_id="def-1",
            relation_definition_label="implements",
        )


# --- delete ---


def test_delete_dependency_uses_dependencies_when_the_route_exists(registered, spy):
    registered["workitem_relation"].fn(
        action="delete",
        project_id="proj-1",
        workitem_id="wi-1",
        related_workitem_id="wi-2",
        is_dependency=True,
    )

    assert spy.recorder.only().method == "work_items.dependencies.remove"


def test_delete_dependency_falls_back_to_relations_delete_when_route_is_absent(registered, spy):
    spy.returns["work_items.dependencies.remove"] = ROUTE_ABSENT

    result = registered["workitem_relation"].fn(
        action="delete",
        project_id="proj-1",
        workitem_id="wi-1",
        related_workitem_id="wi-2",
        is_dependency=True,
    )

    assert spy.recorder.methods[-1] == "work_items.relations.delete"
    fallback = spy.recorder.calls[-1]
    assert fallback.kwargs["data"].related_issue == "wi-2"
    assert result is None


def test_delete_dependency_propagates_a_genuine_error(registered, spy):
    spy.returns["work_items.dependencies.remove"] = ID_NOT_FOUND

    with pytest.raises(HttpError):
        registered["workitem_relation"].fn(
            action="delete",
            project_id="proj-1",
            workitem_id="wi-1",
            related_workitem_id="wi-2",
            is_dependency=True,
        )


def test_delete_falls_back_to_relations_delete_by_default_when_custom_route_is_absent(registered, spy):
    """`is_dependency` defaults to False, routing here to `custom_relations.remove`.
    On CE that 404s route-absent -- but CE has no custom-relation surface at all,
    so that 404 does not mean "removal is unsupported"; it means the only thing
    that could exist to remove is a unified relation. Answering with the
    definitions-unavailable message here (as create's custom branch does) would
    be wrong: `delete` has no `relation_type` to "pass instead", and the default
    call from a caller that never guessed `is_dependency=True` must still work."""
    spy.returns["work_items.custom_relations.remove"] = ROUTE_ABSENT

    result = registered["workitem_relation"].fn(
        action="delete",
        project_id="proj-1",
        workitem_id="wi-1",
        related_workitem_id="wi-2",
    )

    assert spy.recorder.methods[-1] == "work_items.relations.delete"
    fallback = spy.recorder.calls[-1]
    assert fallback.kwargs["data"].related_issue == "wi-2"
    assert result is None


def test_delete_custom_propagates_a_genuine_error(registered, spy):
    spy.returns["work_items.custom_relations.remove"] = ID_NOT_FOUND

    with pytest.raises(HttpError):
        registered["workitem_relation"].fn(
            action="delete",
            project_id="proj-1",
            workitem_id="wi-1",
            related_workitem_id="wi-2",
        )


def test_delete_reports_when_neither_removal_route_nor_relations_route_exists(registered, spy):
    """Same reasoning as create's equivalent case: this is the one endpoint (the
    unified /relations/remove) issue #1 flags as unverified against a live CE
    instance, so a bare 404 here is the most likely fallback failure in practice."""
    spy.returns["work_items.custom_relations.remove"] = ROUTE_ABSENT
    spy.returns["work_items.relations.delete"] = ROUTE_ABSENT

    result = registered["workitem_relation"].fn(
        action="delete",
        project_id="proj-1",
        workitem_id="wi-1",
        related_workitem_id="wi-2",
    )

    assert isinstance(result, str) and result.startswith("Error:")


def test_delete_propagates_a_genuine_error_from_the_relations_fallback(registered, spy):
    spy.returns["work_items.custom_relations.remove"] = ROUTE_ABSENT
    spy.returns["work_items.relations.delete"] = ID_NOT_FOUND

    with pytest.raises(HttpError):
        registered["workitem_relation"].fn(
            action="delete",
            project_id="proj-1",
            workitem_id="wi-1",
            related_workitem_id="wi-2",
        )


# --- definitions ---


def test_list_definitions_reports_when_the_route_is_absent(registered, spy):
    """This call succeeds (it still answers `built_in_dependencies`), so its
    `note` must not read like the whole call failed -- the same objection that
    justified a read-shaped note for `list`'s custom branch applies here too."""
    spy.returns["work_item_relation_definitions.list"] = ROUTE_ABSENT

    result = registered["workitem_relation"].fn(action="list_definitions")

    assert result["custom_definitions"] == []
    assert result["built_in_dependencies"] == list(DEPENDENCY_TYPES)
    assert result["plain_relations"] == list(PLAIN_TYPES)
    assert "not available on this instance" in result["note"]
    assert not result["note"].startswith("Error:")


def test_list_definitions_propagates_a_genuine_error(registered, spy):
    spy.returns["work_item_relation_definitions.list"] = ID_NOT_FOUND

    with pytest.raises(HttpError):
        registered["workitem_relation"].fn(action="list_definitions")


@pytest.mark.parametrize(
    ("action", "args"),
    [
        ("create_definition", {"name": "Implements"}),
        ("update_definition", {"definition_id": "def-1", "name": "Implements"}),
        ("delete_definition", {"definition_id": "def-1"}),
    ],
)
def test_definition_writes_report_when_the_route_is_absent(action, args, registered, spy):
    method = {
        "create_definition": "work_item_relation_definitions.create",
        "update_definition": "work_item_relation_definitions.update",
        "delete_definition": "work_item_relation_definitions.delete",
    }[action]
    spy.returns[method] = ROUTE_ABSENT

    result = registered["workitem_relation"].fn(action=action, **args)

    assert isinstance(result, str) and result.startswith("Error:")
    assert "not available on this instance" in result


@pytest.mark.parametrize(
    ("action", "args"),
    [
        ("update_definition", {"definition_id": "def-1", "name": "Implements"}),
        ("delete_definition", {"definition_id": "def-1"}),
    ],
)
def test_definition_writes_propagate_a_genuine_not_found(action, args, registered, spy):
    method = {
        "update_definition": "work_item_relation_definitions.update",
        "delete_definition": "work_item_relation_definitions.delete",
    }[action]
    spy.returns[method] = ID_NOT_FOUND

    with pytest.raises(HttpError):
        registered["workitem_relation"].fn(action=action, **args)
