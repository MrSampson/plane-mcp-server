"""The HTML view: a column per wave, grouped by project, safe against hostile names."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest

from plane_mcp.toolkit.graph_html import _SCRIPT, item_link, render_html, save_page


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


def test_a_module_member_is_outlined() -> None:
    page: str = render_html(_result(), _none, "t")

    assert 'class="card member"' in page
    assert page.count("card member") == 1


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
    assert "&lt;script&gt;alert(1)&lt;/script&gt;&quot;" in page
    assert page.count("<script>") == 1


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "JAVASCRIPT:alert(1)", " javascript:alert(1)", "data:text/html,x", "//evil.example/x"],
)
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


def test_no_base_url_links_to_plane_cloud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLANE_BASE_URL", raising=False)

    assert item_link("ws")(_node("PROJ-1")) == "https://app.plane.so/ws/projects/pid-PROJ/issues/id-PROJ-1"


def test_a_saved_page_is_read_back_and_private() -> None:
    path: str = save_page("<p>hello</p>")
    try:
        assert Path(path).read_text(encoding="utf-8") == "<p>hello</p>"
        assert Path(path).name.startswith("plane-graph-") and path.endswith(".html")
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    finally:
        os.unlink(path)


def test_a_quote_in_a_link_cannot_end_the_attribute() -> None:
    page: str = render_html(_result(), lambda node: 'https://x.example/"onmouseover="alert(1)', "t")

    assert 'href="https://x.example/&quot;onmouseover=&quot;alert(1)"' in page


def test_link_path_parts_are_percent_encoded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANE_BASE_URL", "https://plane.example")

    link: str | None = item_link("a b")(_node("PROJ-1", id="x/../y"))

    assert link == "https://plane.example/a%20b/projects/pid-PROJ/issues/x%2F..%2Fy"


def test_an_empty_base_url_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANE_BASE_URL", "")

    assert (item_link("ws")(_node("PROJ-1")) or "").startswith("https://app.plane.so/")


def test_an_item_with_nothing_known_about_it_still_renders() -> None:
    bare: dict[str, Any] = _node("x", unreadable=True, name=None, state=None, project=None, boundary=True)
    result: dict[str, Any] = _result(nodes=[bare], edges=[], order=[], unordered={"x": "unexpanded"})

    page: str = render_html(result, _none, "t")

    assert "<h4>?</h4>" in page and "card boundary" in page


def test_a_settled_item_is_drawn_once() -> None:
    result: dict[str, Any] = _result(
        nodes=[_node("PROJ-1", settled=True)], edges=[], order=[], unordered={"PROJ-1": "after_unexpanded"}
    )

    assert render_html(result, _none, "t").count('data-key="n0"') == 1


def test_a_failed_write_leaves_no_file_behind(monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[str] = []
    real: Any = tempfile.mkstemp

    def spy_mkstemp(*args: Any, **kwargs: Any) -> tuple[int, str]:
        descriptor, path = real(*args, **kwargs)
        made.append(path)
        return descriptor, path

    def failing_fdopen(descriptor: int, *args: Any, **kwargs: Any) -> Any:
        os.close(descriptor)
        raise OSError("disk full")

    monkeypatch.setattr(tempfile, "mkstemp", spy_mkstemp)
    monkeypatch.setattr(os, "fdopen", failing_fdopen)

    with pytest.raises(OSError, match="disk full"):
        save_page("x")

    assert made and not os.path.exists(made[0])


def test_an_edge_to_an_unlisted_item_is_left_out() -> None:
    result: dict[str, Any] = _result(edges=[{"from": "GONE-9", "to": "OTHER-2", "type": "blocking"}])

    assert 'data-in=""' in render_html(result, _none, "t")


NODE: str | None = shutil.which("node")


def _anchor(left_a: int, right_a: int, left_b: int, right_b: int) -> dict[str, float]:
    """Runs the page's `anchor` function on two boxes of height 20, the second 100 lower."""
    start: int = _SCRIPT.index("function anchor")
    end: int = _SCRIPT.index("function draw")
    program: str = (
        "const SIDE_BEND=40;"
        + _SCRIPT[start:end]
        + f"console.log(JSON.stringify(anchor({{left:{left_a},right:{right_a},top:0,height:20}},"
        + f"{{left:{left_b},right:{right_b},top:100,height:20}})));"
    )
    done: subprocess.CompletedProcess[str] = subprocess.run(
        [NODE or "node", "-e", program], capture_output=True, text=True, check=True
    )
    return json.loads(done.stdout)


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's script")
class TestAnchoring:
    def test_a_forward_edge_leaves_the_right_side_and_enters_the_left(self) -> None:
        assert _anchor(0, 200, 300, 500) == {"x1": 200, "y1": 10, "x2": 300, "y2": 110, "bend": 250}

    def test_a_backward_edge_leaves_the_left_side_and_enters_the_right(self) -> None:
        assert _anchor(300, 500, 0, 200) == {"x1": 300, "y1": 10, "x2": 200, "y2": 110, "bend": 250}

    def test_an_edge_within_a_column_bows_out_past_the_right_side(self) -> None:
        assert _anchor(0, 200, 0, 220) == {"x1": 200, "y1": 10, "x2": 220, "y2": 110, "bend": 260}


def test_faded_cards_are_muted_by_colour_so_lines_cannot_show_through() -> None:
    page: str = render_html(_result(), _none, "t")

    assert "opacity" not in page
    assert ".card.dim,.card.dim .st{color:var(--faint)}" in page


def test_the_svg_may_draw_past_its_box_so_a_bow_in_the_last_column_is_not_clipped() -> None:
    assert "overflow:visible" in render_html(_result(), _none, "t")


def test_a_dependency_edge_ends_in_an_arrowhead_and_a_plain_relation_does_not() -> None:
    page: str = render_html(_result(), _none, "t")

    assert 'id="arrow"' in page and 'id="arrow-hot"' in page
    assert "if(p.dataset.k==='order')p.setAttribute('marker-end'" in page
    assert "An arrow points at the item that waits" in page
