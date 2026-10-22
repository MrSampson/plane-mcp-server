"""The SDK readers behind the relation graph, each exercised with one call."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from plane.errors.errors import HttpError

from plane_mcp.toolkit.graph_walk import Ref
from plane_mcp.toolkit.relation_graph import module_seeds, node_reader, project_identifier_reader

FORBIDDEN = HttpError("Forbidden", status_code=403, response={"error": "forbidden"})


def test_the_node_reader_asks_for_the_state_expanded_and_flattens_the_answer(spy: Any) -> None:
    spy.returns["work_items.retrieve"] = SimpleNamespace(
        name="Do it",
        priority="high",
        sequence_id=7,
        project="p1",
        state=SimpleNamespace(name="Done", group="completed"),
    )

    detail: dict[str, Any] | None = node_reader(spy, "acme")(Ref("wi-1", "p1"))

    assert detail == {
        "name": "Do it",
        "state": "Done",
        "state_group": "completed",
        "priority": "high",
        "sequence_id": 7,
        "project_id": "p1",
    }
    call = spy.recorder.calls[-1]
    assert call.kwargs["work_item_id"] == "wi-1"
    assert call.kwargs["params"].expand == "state"


def test_the_node_reader_answers_none_for_a_forbidden_item(spy: Any) -> None:
    spy.returns["work_items.retrieve"] = FORBIDDEN

    assert node_reader(spy, "acme")(Ref("wi-1", "p1")) is None


def test_a_missing_item_is_not_mistaken_for_a_forbidden_one(spy: Any) -> None:
    spy.returns["work_items.retrieve"] = HttpError("Not Found", status_code=404, response={"detail": "Not found."})

    with pytest.raises(HttpError) as raised:
        node_reader(spy, "acme")(Ref("wi-1", "p1"))

    assert raised.value.status_code == 404


def test_the_project_identifier_is_fetched_once_per_project(spy: Any) -> None:
    spy.returns["projects.retrieve"] = SimpleNamespace(identifier="PROJ")
    lookup = project_identifier_reader(spy, "acme")

    assert [lookup("p1"), lookup("p1")] == ["PROJ", "PROJ"]
    assert [c.method for c in spy.recorder.calls].count("projects.retrieve") == 1


def test_an_unreadable_project_has_no_identifier(spy: Any) -> None:
    spy.returns["projects.retrieve"] = FORBIDDEN

    assert project_identifier_reader(spy, "acme")("p1") is None


def test_module_seeds_page_through_the_module_and_stop_past_the_cap(spy: Any) -> None:
    page: SimpleNamespace = SimpleNamespace(
        results=[SimpleNamespace(id=f"wi-{n}", project="p1") for n in range(3)],
        next_page_results=True,
        next_cursor="2:1:0",
    )
    spy.returns["modules.list_work_items"] = page

    seeds: list[Ref] = module_seeds(spy, "acme", "p1", "m1", limit=4)

    assert seeds[:3] == [Ref("wi-0", "p1"), Ref("wi-1", "p1"), Ref("wi-2", "p1")]
    assert len(seeds) == 5
    assert [c.method for c in spy.recorder.calls].count("modules.list_work_items") == 2
