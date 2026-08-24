"""What a run cost and how much it moved, in the view where two arms are compared.

Every number here was already recorded. Result tokens even reached ``--table``. None
of it reached the two-file A/B view, which is where a two-arm question is actually
asked -- so an arm making 32% fewer tool calls while burning 3.1x the input tokens
read as the efficient one until someone totalled the tokens by hand.

Two populations, deliberately:

  arm totals   every executed row, because cost was incurred whether or not the
               task passed.
  per task     the successful, trace-intact rows that call deltas already use, so a
               paired delta compares like with like.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from evals.core.pricing import PRICED, PRICES_AS_OF, UNMEASURED, UNPRICED, price_usage
from evals.core.results import TaskResult
from evals.core.token_accounting import normalize_usage

from .load import ResultRow, is_infra_error_row, is_meta_row, read_result
from .schema_friction import successful_trace_rows
from .statistics import median, percentile

COST_LIMITATION = (
    "limitation: cost is computed from a static price table; a model absent from it reports "
    "unpriced, and a row whose driver recorded no usage at all reports unmeasured. Neither is $0"
)


@dataclass(frozen=True, slots=True)
class TaskEconomics:
    """One task's resource footprint across its eligible repetitions."""

    task_id: str
    repetitions: int
    med_total_input: float | None
    med_result_tokens: float | None
    med_wall_time_s: float | None
    med_call_latency_ms: float | None
    cost_usd: float | None


@dataclass(frozen=True, slots=True)
class EconomicsMeasurement:
    """Arm-level totals plus the per-task values a paired delta needs."""

    tasks: dict[str, TaskEconomics]
    total_input_tokens: int | None
    total_result_tokens: int
    total_wall_time_s: float
    cost_usd: float | None
    vendor_cost_usd: float | None
    cost_outcome: str
    priced_rows: int
    unpriced_rows: int
    unmeasured_rows: int
    med_call_latency_ms: float | None
    p95_call_latency_ms: float | None
    prices_as_of: str = PRICES_AS_OF

    @property
    def cost_text(self) -> str:
        """Never render an unknown cost as a number."""
        if self.cost_outcome == UNMEASURED or self.cost_usd is None:
            return UNMEASURED if self.cost_outcome == UNMEASURED else UNPRICED
        text = f"${self.cost_usd:,.3f}"
        if self.unpriced_rows or self.unmeasured_rows:
            text += f" (+{self.unpriced_rows} unpriced, {self.unmeasured_rows} unmeasured rows)"
        return text


def _executed_rows(rows: list[ResultRow]) -> list[TaskResult]:
    executed: list[TaskResult] = []
    for raw_row in rows:
        row = read_result(raw_row)
        if is_meta_row(row) or is_infra_error_row(row) or row.error or row.skipped:
            continue
        executed.append(row)
    return executed


def _row_input_tokens(row: TaskResult) -> int | None:
    accounting = normalize_usage(row.usage_total, model=row.model)
    return accounting.total_input if accounting else None


def _call_latencies(rows: list[TaskResult]) -> list[float]:
    return [float(call.duration_ms) for row in rows for call in row.calls if call.duration_ms is not None]


def measure_economics(rows: list[ResultRow]) -> EconomicsMeasurement:
    """Total an arm's cost and volume, and break it down per task."""
    executed = _executed_rows(rows)

    total_input = 0
    saw_input = False
    cost_total = 0.0
    vendor_total = 0.0
    saw_vendor = False
    priced = unpriced = unmeasured = 0
    for row in executed:
        tokens = _row_input_tokens(row)
        if tokens is not None:
            total_input += tokens
            saw_input = True
        cost = price_usage(row.usage_total, model=row.model)
        if cost.outcome == PRICED:
            priced += 1
            if cost.billed_usd is not None:
                cost_total += cost.billed_usd
        elif cost.outcome == UNPRICED:
            unpriced += 1
        else:
            unmeasured += 1
        if cost.vendor_usd is not None:
            vendor_total += cost.vendor_usd
            saw_vendor = True

    if priced == 0:
        # No priced row at all: say which kind of nothing this is.
        outcome = UNMEASURED if unpriced == 0 else UNPRICED
    elif unpriced or unmeasured:
        outcome = PRICED  # partial, and the counts are carried alongside
    else:
        outcome = PRICED

    by_task: dict[str, list[TaskResult]] = defaultdict(list)
    for row in successful_trace_rows(rows):
        by_task[row.task_id].append(row)

    tasks: dict[str, TaskEconomics] = {}
    for task_id in sorted(by_task):
        task_rows = by_task[task_id]
        inputs = [float(value) for value in (_row_input_tokens(row) for row in task_rows) if value is not None]
        costs = [price_usage(row.usage_total, model=row.model).billed_usd for row in task_rows]
        known_costs = [value for value in costs if value is not None]
        tasks[task_id] = TaskEconomics(
            task_id=task_id,
            repetitions=len(task_rows),
            med_total_input=median(inputs),
            med_result_tokens=median([float(row.total_result_tokens) for row in task_rows]),
            med_wall_time_s=median([float(row.wall_time_s) for row in task_rows]),
            med_call_latency_ms=median(_call_latencies(task_rows)),
            cost_usd=(sum(known_costs) / len(known_costs) if known_costs else None),
        )

    latencies = _call_latencies(executed)
    return EconomicsMeasurement(
        tasks=tasks,
        total_input_tokens=total_input if saw_input else None,
        total_result_tokens=sum(row.total_result_tokens for row in executed),
        total_wall_time_s=sum(float(row.wall_time_s) for row in executed),
        cost_usd=cost_total if priced else None,
        vendor_cost_usd=vendor_total if saw_vendor else None,
        cost_outcome=outcome,
        priced_rows=priced,
        unpriced_rows=unpriced,
        unmeasured_rows=unmeasured,
        med_call_latency_ms=median(latencies),
        p95_call_latency_ms=percentile(latencies, 0.95),
    )


def economics_statement(measurement: EconomicsMeasurement) -> str:
    """One block naming cost, volume and latency, with unknowns named as unknowns."""
    input_text = f"{measurement.total_input_tokens:,}" if measurement.total_input_tokens is not None else UNMEASURED
    latency = measurement.med_call_latency_ms
    p95 = measurement.p95_call_latency_ms
    latency_text = f"{latency:,.0f}ms median / {p95:,.0f}ms p95" if latency is not None and p95 is not None else "n/a"
    lines = [
        f"economics: cost={measurement.cost_text} (prices as of {measurement.prices_as_of}); "
        f"input tokens={input_text}; result tokens={measurement.total_result_tokens:,}",
        f"  wall time={measurement.total_wall_time_s:,.0f}s; call latency {latency_text}",
    ]
    if measurement.vendor_cost_usd is not None and measurement.cost_usd is not None:
        # The only standing check that the price table has not gone stale.
        drift = measurement.cost_usd - measurement.vendor_cost_usd
        lines.append(f"  vendor-reported cost=${measurement.vendor_cost_usd:,.3f}; table differs by ${drift:+,.3f}")
    lines.append(f"  {COST_LIMITATION}")
    return "\n".join(lines)


__all__ = [
    "COST_LIMITATION",
    "EconomicsMeasurement",
    "TaskEconomics",
    "economics_statement",
    "measure_economics",
]
