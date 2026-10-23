"""The HTML view: a column per wave, grouped by project, safe against hostile names."""

from __future__ import annotations

from typing import Any

import pytest

from plane_mcp.toolkit.graph_html import item_link, render_html


def _node(identifier: str, project: str = "PROJ", **overrides: Any) -> dict[str, Any]:
    node: dict[str, Any] = {
        "id": f"id-{identifier}",
        "identifier": identifier,
        "name": f"name {identifier}",
        "state": "Todo",
        "settled": False,
        "priority": "none",
        "project_id": f"pid-{project}",
        "project": project,
        "boundary": False,
        "unreadable": False,
    }
    node.update(overrides)
    return node


def _result(**overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "nodes": [_node("PROJ-1", module_ids=["m"]), _node("OTHER-2", "OTHER")],
        "edges": [{"from": "PROJ-1", "to": "OTHER-2", "type": "blocking"}],
        "order": [["PROJ-1"], ["OTHER-2"]],
        "unordered": {},
        "cycles": [],
        "truncated": False,
        "cut": [],
    }
    result.update(overrides)
    return result


def _none(node: dict[str, Any]) -> str | None:
    return None


def test_the_title_heads_the_page() -> None:
    page: str = render_html(_result(), _none, "Platform / Launch")

    assert "<title>Platform / Launch</title>" in page
    assert "<h1>Platform / Launch</h1>" in page


def test_each_wave_is_a_column_and_items_are_grouped_by_project() -> None:
    page: str = render_html(_result(), _none, "t")

    assert "Wave 0<small>can start now · 1</small>" in page
    assert "Wave 1<small>after wave 0 · 1</small>" in page
    assert "<h4>OTHER</h4>" in page and "<h4>PROJ</h4>" in page


def test_a_module_member_is_marked_as_a_seed() -> None:
    page: str = render_html(_result(), _none, "t")

    assert 'class="card seed"' in page
    assert page.count("card seed") == 1


def test_unplaced_items_get_their_own_columns() -> None:
    result: dict[str, Any] = _result(
        nodes=[_node("A-1", boundary=True), _node("A-2"), _node("A-3"), _node("A-4")],
        edges=[],
        order=[],
        unordered={"A-1": "unexpanded", "A-2": "after_unexpanded", "A-3": "in_cycle", "A-4": "after_cycle"},
        cycles=[["A-3", "A-3"]],
    )

    page: str = render_html(result, _none, "t")

    assert "Not expanded<small>relations not read · 1" in page
    assert "Held<small>waits on an item that is unread or in a cycle · 2" in page
    assert "Cycle<small>cannot be ordered · 1" in page
    assert "card cycle" in page and "card boundary" in page


def test_a_settled_item_has_a_column_of_its_own() -> None:
    result: dict[str, Any] = _result(nodes=[_node("PROJ-1", settled=True)], edges=[], order=[])

    page: str = render_html(result, _none, "t")

    assert "Settled<small>" in page and "card settled" in page


def test_a_plain_relation_is_drawn_apart_from_a_dependency() -> None:
    result: dict[str, Any] = _result(edges=[{"from": "PROJ-1", "to": "OTHER-2", "type": "relates_to"}])

    assert ":plain" in render_html(result, _none, "t")
    assert ":order" in render_html(_result(), _none, "t")


def test_names_are_escaped_and_never_reach_the_script() -> None:
    hostile: str = '<script>alert(1)</script>"'
    result: dict[str, Any] = _result(nodes=[_node("PROJ-1", name=hostile, state=hostile)], edges=[], order=[["PROJ-1"]])

    page: str = render_html(result, _none, hostile)

    assert hostile not in page
    assert page.count("<script>") == 1


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,x", "//evil.example/x"])
def test_only_http_links_are_followed(url: str) -> None:
    page: str = render_html(_result(), lambda node: url, "t")

    assert "<a href" not in page


def test_an_item_links_to_its_page() -> None:
    page: str = render_html(_result(), lambda node: f"https://plane.example/ws/{node['id']}", "t")

    assert 'href="https://plane.example/ws/id-PROJ-1"' in page


def test_truncation_is_stated_on_the_page() -> None:
    page: str = render_html(_result(truncated=True, cut=["X-1", "X-2"]), _none, "t")

    assert "Truncated: 2 item(s)" in page


def test_links_use_the_public_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    node: dict[str, Any] = _node("PROJ-1")
    monkeypatch.setenv("PLANE_BASE_URL", "https://plane.example/")
    monkeypatch.setenv("PLANE_INTERNAL_BASE_URL", "http://plane-api:8000")

    assert item_link("ws")(node) == "https://plane.example/ws/projects/pid-PROJ/issues/id-PROJ-1"


def test_plane_cloud_links_go_to_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANE_BASE_URL", "https://api.plane.so")

    assert (item_link("ws")(_node("PROJ-1")) or "").startswith("https://app.plane.so/ws/")


def test_no_base_url_means_no_links(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLANE_BASE_URL", raising=False)

    assert item_link("ws")(_node("PROJ-1")) is None
