"""Say plainly when the per-task numbers cannot carry a verdict.

A run's aggregate and its per-task rows have very different power, and the report
prints both in the same table. At 2 repetitions a task that passed once is
``1/2 UNSTABLE`` with a 95% interval of roughly [0.09, 0.91] -- compatible with
almost any true success rate -- while the paired aggregate across 35 tasks resolved
a call difference at p=0.0018. Reading a per-task row as a finding is therefore
wrong in exactly the runs where the aggregate is most convincing.

No new machinery: ``--tasks`` already allows a focused high-rep subset, and at
roughly $0.50 an arm that is affordable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, summary imports this module
    from .summary import Summary

#: Repetitions per task below which a per-task pass rate is not worth reading.
UNDERPOWERED_REPS = 5


def power_statement(summary: Summary) -> str | None:
    """Return the guardrail line, or None when at least one task is well powered.

    The claim is deliberately about the best-covered task: if any task reaches the
    threshold, "no per-task verdict is supported" would be false, and a caveat that
    overstates its own scope gets ignored along with the ones that do not.
    """
    counts = [task.n for task in summary.tasks.values() if task.n]
    if not counts:
        return None
    best = max(counts)
    if best >= UNDERPOWERED_REPS:
        return None
    return (
        f"POWER: {best} repetition(s) per task at most — per-task pass rates and UNSTABLE flags "
        f"are not verdicts at this depth; read the aggregate and paired deltas instead. "
        f"Use --tasks with --reps {UNDERPOWERED_REPS}+ for a per-task claim."
    )


__all__ = ["UNDERPOWERED_REPS", "power_statement"]
