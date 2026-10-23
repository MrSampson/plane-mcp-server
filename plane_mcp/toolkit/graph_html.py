"""The `graph` result as one self-contained HTML page: a column per wave, grouped by project.

Pure: it takes the assembled result and a link function, and loads nothing from
the network. Item names are user content, so every value that reaches the page
is escaped and nothing is passed to the script except through DOM attributes.
"""

from __future__ import annotations

import html
import os
import tempfile
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlsplit

from plane_mcp.toolkit.graph import EdgeType, Unordered

# Maps a node to the URL of its Plane page, or None when there is no public address to link to.
ItemLink = Callable[[dict[str, Any]], str | None]

LINK_SCHEMES: frozenset[str] = frozenset({"http", "https"})
UNKNOWN_PROJECT: str = "?"
PAGE_PREFIX: str = "plane-graph-"
# The page needs only its own inline style and script, and nothing from the network.
CONTENT_SECURITY_POLICY: str = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'"
CLOUD_API_URL: str = "https://api.plane.so"
CLOUD_APP_URL: str = "https://app.plane.so"

_STYLE: str = """
:root{--bg:#fff;--fg:#1b1f24;--mut:#6b7280;--line:#c9ced6;--seed:#2563eb;--dash:#9ca3af;--bad:#d33}
@media(prefers-color-scheme:dark){
:root{--bg:#14171c;--fg:#e6e8eb;--mut:#9aa3af;--line:#3a414b;--seed:#60a5fa;--dash:#6b7280;--bad:#f87171}
}
body{margin:0;background:var(--bg);color:var(--fg);font:13px system-ui}
#wrap{position:relative;padding:16px;overflow:auto}
#cols{display:flex;gap:56px;align-items:flex-start;position:relative;z-index:1}
section{min-width:230px;max-width:260px}
h1{margin:0 0 4px;font-size:18px}
h3{margin:0 0 8px}h3 small{display:block;font-weight:400;color:var(--mut);font-size:11px}
h4{margin:10px 0 4px;color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.05em}
.card{border:1px solid var(--line);border-radius:6px;padding:6px 8px;margin-bottom:6px;background:var(--bg)}
.card.seed{border:2px solid var(--seed)}.card.boundary{border-style:dashed;border-color:var(--dash)}
.card.cycle{border-color:var(--bad)}.card.settled{opacity:.55}
.card a{color:inherit;font-weight:600}.st{color:var(--mut);font-size:11px}.nm{margin-top:2px}
.card.dim{opacity:.25}.card.hot{outline:2px solid var(--seed)}
svg{position:absolute;top:0;left:0;pointer-events:none;z-index:0}
path{fill:none;stroke:var(--line);stroke-width:1.2}path.plain{stroke-dasharray:4 3}
path.hot{stroke:var(--seed);stroke-width:2}
.legend{color:var(--mut);margin:0 0 12px}
"""

_SCRIPT: str = """
const cards=[...document.querySelectorAll('.card')];
const svg=document.getElementById('svg'),wrap=document.getElementById('wrap');
const byKey=Object.fromEntries(cards.map(c=>[c.dataset.key,c]));
function draw(){
 svg.replaceChildren();
 const o=wrap.getBoundingClientRect();
 svg.setAttribute('width',wrap.scrollWidth);svg.setAttribute('height',wrap.scrollHeight);
 for(const c of cards)for(const e of (c.dataset.in||'').split(' ').filter(Boolean)){
  const [from,kind]=e.split(':'),a=byKey[from];
  if(!a)continue;
  const ra=a.getBoundingClientRect(),rb=c.getBoundingClientRect();
  const x1=ra.right-o.left+wrap.scrollLeft,y1=ra.top+ra.height/2-o.top+wrap.scrollTop;
  const x2=rb.left-o.left+wrap.scrollLeft,y2=rb.top+rb.height/2-o.top+wrap.scrollTop,m=(x1+x2)/2;
  const p=document.createElementNS('http://www.w3.org/2000/svg','path');
  p.setAttribute('d','M'+x1+' '+y1+' C'+m+' '+y1+' '+m+' '+y2+' '+x2+' '+y2);
  if(kind==='plain')p.classList.add('plain');
  p.dataset.f=from;p.dataset.t=c.dataset.key;svg.appendChild(p);
 }
}
cards.forEach(c=>{
 c.onmouseenter=()=>{
  const keep=new Set([c.dataset.key]);
  svg.querySelectorAll('path').forEach(p=>{
   const hot=p.dataset.t===c.dataset.key||p.dataset.f===c.dataset.key;
   p.classList.toggle('hot',hot);
   if(hot){keep.add(p.dataset.f);keep.add(p.dataset.t);}
  });
  cards.forEach(x=>x.classList.toggle('dim',!keep.has(x.dataset.key)));
 };
 c.onmouseleave=()=>{
  svg.querySelectorAll('path').forEach(p=>p.classList.remove('hot'));
  cards.forEach(x=>x.classList.remove('dim'));
 };
});
draw();addEventListener('resize',draw);
"""

_LEGEND: str = (
    "Blue border: a member of the module. Dashed border: relations not read. Faded: settled. "
    "Dashed line: relates_to or duplicate. Hover an item to trace its blockers and dependents."
)


def _safe_url(url: str | None) -> str | None:
    """The URL when it is plain http(s); anything else is not linked."""
    return url if url and urlsplit(url).scheme in LINK_SCHEMES else None


def _columns(result: dict[str, Any]) -> list[tuple[str, str, list[str]]]:
    """Title, subtitle and member identifiers of each column, left to right."""
    nodes: list[dict[str, Any]] = result["nodes"]
    unordered: dict[str, str] = result["unordered"]
    settled: list[str] = [n["identifier"] for n in nodes if n["settled"]]
    columns: list[tuple[str, str, list[str]]] = []
    if settled:
        columns.append(("Settled", "completed or cancelled; blocks nothing", settled))
    for index, wave in enumerate(result["order"]):
        subtitle: str = "can start now" if index == 0 else f"after wave {index - 1}"
        columns.append((f"Wave {index}", subtitle, wave))
    waiting: tuple[str, ...] = (Unordered.AFTER_UNEXPANDED, Unordered.AFTER_CYCLE)
    held: list[str] = [i for i, why in unordered.items() if why in waiting and i not in settled]
    unread: list[str] = [i for i, why in unordered.items() if why == Unordered.UNEXPANDED and i not in settled]
    cyclic: list[str] = [i for i, why in unordered.items() if why == Unordered.IN_CYCLE and i not in settled]
    if held:
        columns.append(("Held", "waits on an item that is unread or in a cycle", held))
    if unread:
        columns.append(("Not expanded", "relations not read", unread))
    if cyclic:
        columns.append(("Cycle", "cannot be ordered", cyclic))
    return columns


def _card(key: str, node: dict[str, Any], incoming: str, cyclic: bool, link: ItemLink) -> str:
    classes: list[str] = ["card"]
    if node.get("module_ids"):
        classes.append("seed")
    if node["settled"]:
        classes.append("settled")
    if node["boundary"]:
        classes.append("boundary")
    if cyclic:
        classes.append("cycle")
    label: str = html.escape(node["identifier"])
    url: str | None = _safe_url(link(node))
    title: str = (
        f'<a href="{html.escape(url)}" target="_blank" rel="noopener noreferrer">{label}</a>'
        if url
        else f"<b>{label}</b>"
    )
    state: str = html.escape(str(node["state"])) if node["state"] else ""
    return (
        f'<div class="{" ".join(classes)}" data-key="{key}" data-in="{incoming}">'
        f'{title} <span class="st">{state}</span><div class="nm">{html.escape(str(node["name"] or ""))}</div></div>'
    )


def item_link(workspace_slug: str) -> ItemLink:
    """Addresses an item's page in the web app, or nothing when no public base URL is configured.

    Only `PLANE_BASE_URL` is used: `PLANE_INTERNAL_BASE_URL` is reachable from the server, not
    necessarily from the reader's browser. Plane Cloud serves its API from `api.` and the app from `app.`.
    """
    base: str = os.getenv("PLANE_BASE_URL", "").rstrip("/")
    if base == CLOUD_API_URL:
        base = CLOUD_APP_URL

    def link(node: dict[str, Any]) -> str | None:
        if not base:
            return None
        project: str = quote(str(node["project_id"]), safe="")
        return f"{base}/{quote(workspace_slug, safe='')}/projects/{project}/issues/{quote(str(node['id']), safe='')}"

    return link


def render_html(result: dict[str, Any], link: ItemLink, title: str) -> str:
    """The page for an assembled `graph` result; `link` addresses an item's Plane page, `title` names the scope."""
    nodes: dict[str, dict[str, Any]] = {n["identifier"]: n for n in result["nodes"]}
    keys: dict[str, str] = {identifier: f"n{index}" for index, identifier in enumerate(nodes)}
    incoming: dict[str, list[str]] = {}
    for edge in result["edges"]:
        if edge["from"] in keys and edge["to"] in keys:
            kind: str = "order" if EdgeType(edge["type"]).orders else "plain"
            incoming.setdefault(edge["to"], []).append(f"{keys[edge['from']]}:{kind}")
    cyclic: set[str] = {i for cycle in result["cycles"] for i in cycle}

    body: list[str] = []
    for column, subtitle, members in _columns(result):
        by_project: dict[str, list[str]] = {}
        for identifier in sorted(members, key=lambda i: (nodes[i]["project"] or "", i)):
            by_project.setdefault(nodes[identifier]["project"] or UNKNOWN_PROJECT, []).append(identifier)
        groups: str = "".join(
            f"<div><h4>{html.escape(project)}</h4>"
            + "".join(_card(keys[i], nodes[i], " ".join(incoming.get(i, [])), i in cyclic, link) for i in identifiers)
            + "</div>"
            for project, identifiers in by_project.items()
        )
        heading: str = f"{html.escape(column)}<small>{html.escape(subtitle)} · {len(members)}</small>"
        body.append(f"<section><h3>{heading}</h3>{groups}</section>")

    truncated: str = (
        f'<p class="legend">Truncated: {len(result["cut"])} item(s) were not read.</p>' if result["truncated"] else ""
    )
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        f'<meta http-equiv="Content-Security-Policy" content="{CONTENT_SECURITY_POLICY}">'
        f'<title>{html.escape(title)}</title><style>{_STYLE}</style><body><div id="wrap">'
        f'<h1>{html.escape(title)}</h1><p class="legend">{_LEGEND}</p>{truncated}'
        f'<svg id="svg"></svg><div id="cols">{"".join(body)}</div></div>'
        f"<script>{_SCRIPT}</script></body></html>"
    )


def save_page(page: str) -> str:
    """Writes the page to a new file in the temp directory and returns its path.

    `mkstemp` creates it readable by the owner only: the page holds item names. Files are not
    removed afterwards; the temp directory's own clean-up handles them.
    """
    descriptor, path = tempfile.mkstemp(prefix=PAGE_PREFIX, suffix=".html")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(page)
    except OSError:
        os.unlink(path)
        raise
    return path
