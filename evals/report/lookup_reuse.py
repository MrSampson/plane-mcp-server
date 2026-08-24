"""How often an agent re-hunts an entity whose identifier it already holds.

The sharpest single finding of the 2026-08-24 cross-harness pair was
``workitem.search`` 111 vs 26 against ``workitem.retrieve`` 4 vs 20. One arm carried
resolved ids across turns; the other went looking again each time. That is a
property of the **surface** as much as of the agent -- identifiers that stayed
sticky would close the gap without either agent changing -- and nothing measured it.

Read from recorded call arguments, so it is only answerable on runs that have them.
A run that does not is reported as not measured, never as zero: "no redundant
lookups" and "we could not tell" are opposite conclusions.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field

from evals.core.results import CallRecord, TaskResult

from .load import ResultRow, is_infra_error_row, is_meta_row, read_result

#: Actions that go looking for something rather than addressing it directly.
_SEARCH_ACTIONS = frozenset({"search", "list"})

#: Argument names that carry an identifier this run already resolved. Matched by
#: suffix so ``workitem_id``, ``parent_id`` and ``id`` all count.
_ID_SUFFIX = "_id"


def _args_of(call: CallRecord) -> dict | None:
    if not call.args_json:
        return None
    try:
        parsed = json.loads(call.args_json)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _ids_in(args: dict) -> set[str]:
    found: set[str] = set()
    for name, value in args.items():
        if not isinstance(value, str) or not value:
            continue
        if name == "id" or name.endswith(_ID_SUFFIX):
            found.add(value)
    return found


@dataclass(frozen=True, slots=True)
class LookupReuseMeasurement:
    """Redundant lookups, and whether the question could be asked at all."""

    total: int = 0
    by_resource: dict[str, int] = field(default_factory=dict)
    rows_measured: int = 0
    rows_without_args: int = 0

    @property
    def measurable(self) -> bool:
        return self.rows_measured > 0

    def statement(self) -> str:
        if not self.measurable:
            return f"redundant lookups: not measured — {self.rows_without_args} row(s) carry no recorded call arguments"
        detail = ", ".join(f"{resource}={count}" for resource, count in sorted(self.by_resource.items()))
        line = f"redundant lookups: {self.total} (a search or list on a resource whose id was already in hand)"
        if detail:
            line += f" [{detail}]"
        if self.rows_without_args:
            line += f"; {self.rows_without_args} row(s) not measured for want of arguments"
        return line


def measure_lookup_reuse(rows: list[ResultRow]) -> LookupReuseMeasurement:
    """Count searches issued after the same resource's id was already resolved.

    Scoped to one row. Each repetition is a fresh conversation, so an id learned in
    one tells the agent in another nothing.
    """
    total = 0
    by_resource: dict[str, int] = defaultdict(int)
    measured = 0
    without_args = 0
    for raw_row in rows:
        row: TaskResult = read_result(raw_row)
        if is_meta_row(row) or is_infra_error_row(row) or row.error or row.skipped:
            continue
        if not any(call.args_json for call in row.calls):
            without_args += 1
            continue
        measured += 1
        known: dict[str, set[str]] = defaultdict(set)
        for call in row.calls:
            args = _args_of(call)
            if args is None:
                continue
            resource = call.tool
            action = (call.action or "").lower()
            if action in _SEARCH_ACTIONS and known[resource]:
                total += 1
                by_resource[resource] += 1
            # Learned after the check, so the call that first resolves an id is never
            # charged for the lookup that produced it.
            known[resource].update(_ids_in(args))
    return LookupReuseMeasurement(
        total=total,
        by_resource=dict(by_resource),
        rows_measured=measured,
        rows_without_args=without_args,
    )


__all__ = ["LookupReuseMeasurement", "measure_lookup_reuse"]
