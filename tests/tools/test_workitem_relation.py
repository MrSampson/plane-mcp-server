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


# --- list ---


def test_list_uses_dependencies_when_the_route_exists(registered, spy):
    registered["workitem_relation"].fn(action="list", project_id="proj-1", workitem_id="wi-1")

    assert spy.recorder.methods == ["work_items.dependencies.list", "work_items.custom_relations.list"]


def test_list_falls_back_to_relations_when_dependencies_route_is_absent(registered, spy):
    spy.returns["work_items.dependencies.list"] = ROUTE_ABSENT

    registered["workitem_relation"].fn(action="list", project_id="proj-1", workitem_id="wi-1")

    assert "work_items.relations.list" in spy.recorder.methods
    fallback = spy.recorder.calls[spy.recorder.methods.index("work_items.relations.list")]
    assert fallback.kwargs["project_id"] == "proj-1"
    assert fallback.kwargs["work_item_id"] == "wi-1"


def test_list_propagates_a_genuine_dependencies_error(registered, spy):
    spy.returns["work_items.dependencies.list"] = ID_NOT_FOUND

    with pytest.raises(HttpError):
        registered["workitem_relation"].fn(action="list", project_id="proj-1", workitem_id="wi-1")


def test_list_custom_returns_empty_when_its_route_is_absent(registered, spy):
    spy.returns["work_items.custom_relations.list"] = ROUTE_ABSENT

    result = registered["workitem_relation"].fn(action="list", project_id="proj-1", workitem_id="wi-1")

    assert result["custom"] == {}


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
    spy.returns["work_items.dependencies.create"] = ROUTE_ABSENT

    registered["workitem_relation"].fn(
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


def test_create_dependency_target_is_not_restricted_to_the_source_project(registered, spy):
    """`project_id` in the URL names the source item only -- the SDK does not
    reject a target from a different project of the same workspace."""
    registered["workitem_relation"].fn(
        action="create",
        project_id="proj-1",
        workitem_id="wi-1",
        workitem_ids=["other-project-wi-9"],
        relation_type="blocking",
    )

    call = spy.recorder.only()
    assert call.kwargs["project_id"] == "proj-1"
    assert call.kwargs["data"].work_item_ids == ["other-project-wi-9"]


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


def test_delete_custom_reports_when_its_route_is_absent(registered, spy):
    spy.returns["work_items.custom_relations.remove"] = ROUTE_ABSENT

    result = registered["workitem_relation"].fn(
        action="delete",
        project_id="proj-1",
        workitem_id="wi-1",
        related_workitem_id="wi-2",
    )

    assert isinstance(result, str) and result.startswith("Error:")
    assert "not available on this instance" in result


# --- definitions ---


def test_list_definitions_reports_when_the_route_is_absent(registered, spy):
    spy.returns["work_item_relation_definitions.list"] = ROUTE_ABSENT

    result = registered["workitem_relation"].fn(action="list_definitions")

    assert result["custom_definitions"] == []
    assert result["built_in_dependencies"]
    assert "not available on this instance" in result["note"]


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
