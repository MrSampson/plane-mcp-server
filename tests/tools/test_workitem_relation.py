"""Self-hosted Plane CE serves relations at a different path than Cloud.

`dependencies` and `custom_relations` route to `/dependencies/` and
`/work-item-relations/`, which 404 on CE. The unified `/relations/` surface
works on both. Every dispatch branch here must try the `dependencies` /
`custom_relations` path first (so Cloud is unaffected) and fall back to
`relations` only on a 404 that means "this route does not exist here" --
never on a 404 that means "this particular id does not exist".
"""

from __future__ import annotations

import pytest
from plane.errors.errors import HttpError

ROUTE_ABSENT = HttpError("Not Found", status_code=404, response={"error": "Page not found."})
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

    result = registered["workitem_relation"].fn(action="list", project_id="proj-1", workitem_id="wi-1")

    assert "work_items.relations.list" in spy.recorder.methods
    fallback = spy.recorder.calls[spy.recorder.methods.index("work_items.relations.list")]
    assert fallback.kwargs["project_id"] == "proj-1"
    assert fallback.kwargs["work_item_id"] == "wi-1"
    assert "plain work item ids" in result["note"]


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

    result = registered["workitem_relation"].fn(action="list", project_id="proj-1", workitem_id="wi-1")

    assert "work_items.relations.list" in spy.recorder.methods
    assert result["custom"] == {}
    assert "plain work item ids" in result["note"]
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


@pytest.mark.parametrize("relation_type", ["blocking", "blocked_by"])
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
    assert result["built_in_dependencies"]
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
