"""Self-hosted Plane CE does not serve the dedicated project-features endpoint.

`get_features`/`update_features` route to `/projects/{id}/features`, which 404s on
CE with Plane's generic routing 404. The six toggles CE does support live as
fields on the project resource itself, under older names, so both actions fall
back to reading/writing the project directly. `epics`/`workflows`/
`parallel_cycles`/`project_updates` have no equivalent on CE at all and must
error rather than being silently dropped.
"""

from __future__ import annotations

import pytest
from plane.errors.errors import HttpError
from plane.models.projects import Project

from plane_mcp.toolkit.governance import ROUTE_ABSENT_ERROR

ROUTE_ABSENT = HttpError("Not Found", status_code=404, response={"error": ROUTE_ABSENT_ERROR})
ID_NOT_FOUND = HttpError("Not Found", status_code=404, response={"detail": "Not found."})

_CE_PROJECT = Project(
    name="Acme",
    identifier="ACME",
    module_view=True,
    cycle_view=False,
    issue_views_view=True,
    page_view=False,
    intake_view=True,
    is_issue_type_enabled=True,
)


def test_get_features_uses_the_dedicated_endpoint_when_it_exists(registered, spy):
    registered["project"].fn(action="get_features", project_id="proj-1")

    assert spy.recorder.methods == ["projects.get_features"]


def test_get_features_propagates_a_genuine_id_404(registered, spy):
    spy.returns["projects.get_features"] = ID_NOT_FOUND

    with pytest.raises(HttpError):
        registered["project"].fn(action="get_features", project_id="proj-1")


def test_get_features_falls_back_to_the_project_resource_on_ce(registered, spy):
    spy.returns["projects.get_features"] = ROUTE_ABSENT
    spy.returns["projects.retrieve"] = _CE_PROJECT

    result = registered["project"].fn(action="get_features", project_id="proj-1")

    assert spy.recorder.methods == ["projects.get_features", "projects.retrieve"]
    assert result.modules is True
    assert result.cycles is False
    assert result.views is True
    assert result.pages is False
    assert result.intakes is True
    assert result.work_item_types is True


def test_update_features_uses_the_dedicated_endpoint_when_it_exists(registered, spy):
    registered["project"].fn(action="update_features", project_id="proj-1", modules=True)

    assert spy.recorder.methods == ["projects.update_features"]


def test_update_features_propagates_a_genuine_id_404(registered, spy):
    spy.returns["projects.update_features"] = ID_NOT_FOUND

    with pytest.raises(HttpError):
        registered["project"].fn(action="update_features", project_id="proj-1", modules=True)


def test_update_features_falls_back_to_updating_the_project_on_ce(registered, spy):
    spy.returns["projects.update_features"] = ROUTE_ABSENT
    spy.returns["projects.update"] = _CE_PROJECT

    result = registered["project"].fn(
        action="update_features", project_id="proj-1", modules=True, cycles=False, workitem_types=True
    )

    assert spy.recorder.methods == ["projects.update_features", "projects.update"]
    fallback = spy.recorder.calls[-1]
    assert fallback.kwargs["data"].module_view is True
    assert fallback.kwargs["data"].cycle_view is False
    assert fallback.kwargs["data"].is_issue_type_enabled is True
    assert result.modules is True
    assert result.cycles is False
    assert result.work_item_types is True


def test_update_features_leaves_omitted_toggles_unset_in_the_fallback(registered, spy):
    """`data.model_dump(exclude_none=True)` is what makes 'omitted ones are left as they
    are' true -- pin that the fallback payload actually carries None for what wasn't passed,
    not just that the toggles under test made it through."""
    spy.returns["projects.update_features"] = ROUTE_ABSENT
    spy.returns["projects.update"] = _CE_PROJECT

    registered["project"].fn(action="update_features", project_id="proj-1", modules=True)

    fallback_data = spy.recorder.calls[-1].kwargs["data"]
    assert fallback_data.cycle_view is None
    assert fallback_data.issue_views_view is None
    assert fallback_data.page_view is None
    assert fallback_data.intake_view is None
    assert fallback_data.is_issue_type_enabled is None


@pytest.mark.parametrize("toggle", ["epics", "workflows", "parallel_cycles", "project_updates"])
@pytest.mark.parametrize("value", [True, False])
def test_update_features_rejects_cloud_only_toggles_on_ce(registered, spy, toggle, value):
    spy.returns["projects.update_features"] = ROUTE_ABSENT

    result = registered["project"].fn(action="update_features", project_id="proj-1", **{toggle: value})

    assert spy.recorder.methods == ["projects.update_features"]
    assert result.startswith("Error:")
    assert toggle in result


def test_update_features_rejects_a_mixed_request_without_writing_anything(registered, spy):
    """A CE toggle alongside a Cloud-only one must refuse whole, not partially apply."""
    spy.returns["projects.update_features"] = ROUTE_ABSENT

    result = registered["project"].fn(action="update_features", project_id="proj-1", modules=True, epics=True)

    assert spy.recorder.methods == ["projects.update_features"]
    assert result.startswith("Error:")
    assert "epics" in result


def test_get_features_propagates_an_error_from_the_fallback_itself(registered, spy):
    spy.returns["projects.get_features"] = ROUTE_ABSENT
    spy.returns["projects.retrieve"] = ID_NOT_FOUND

    with pytest.raises(HttpError):
        registered["project"].fn(action="get_features", project_id="proj-1")
