"""Column classification over the aligned matrix.

Classes (CLAUDE.md Part II):
  invariant      present in all runs, identical payload
  parameterized  present in all runs, payload varies
  conditional    present in a subset of runs, guard unknown
  unordered      present in all runs, ordered differently

Unordered detection (docs/DECISIONS.md D-008): a maximal window of
adjacent columns is an unordered region when every run contains
exactly the same duplicate-free multiset of tokens within the window
but at least two runs disagree on order. The window's partial columns
are collapsed into one full column per token, ordered as in the
lowest-numbered run, so downstream consumers see one column per step.
"""

from __future__ import annotations

from dataclasses import dataclass

from pendant.align.msa import AlignParams, Column
from pendant.align.normalizer import CanonToken

Classification = str  # "invariant" | "parameterized" | "conditional" | "unordered"


@dataclass
class ClassifiedColumn:
    column: Column
    classification: Classification


def _window_as_unordered(
    window: list[Column], run_ids: list[str]
) -> list[Column] | None:
    """Collapse a qualifying window into per-token full columns, else None."""
    per_run: dict[str, list[CanonToken]] = {r: [] for r in run_ids}
    for col in window:
        for run_id in col.cells:
            per_run[run_id].append(col.token)
    sequences = list(per_run.values())
    reference = sorted(map(str, sequences[0]))
    if len(set(reference)) != len(reference):
        return None  # duplicate tokens within a run: out of scope (D-008)
    if not reference:
        return None
    for seq in sequences[1:]:
        if sorted(map(str, seq)) != reference:
            return None  # multisets differ: not an unordered region
    if all(seq == sequences[0] for seq in sequences[1:]):
        return None  # same order everywhere: nothing to collapse
    # Qualifies. Rebuild one full column per token, ordered per the
    # lowest run id for determinism.
    order = per_run[min(run_ids)]
    collapsed: list[Column] = []
    for token in order:
        cells = {}
        for col in window:
            if col.token == token:
                cells.update(col.cells)
        collapsed.append(Column(token=token, cells=cells))
    return collapsed


def _detect_unordered(
    columns: list[Column], run_ids: list[str], max_window: int
) -> tuple[list[Column], set[int]]:
    """Return (rewritten columns, indices classified unordered)."""
    out: list[Column] = []
    unordered_idx: set[int] = set()
    i = 0
    n = len(columns)
    while i < n:
        collapsed = None
        span = 0
        for j in range(min(n, i + max_window), i + 1, -1):
            window = columns[i:j]
            # An unordered region is bounded by partial columns: a
            # full-presence column at either edge belongs to the
            # surrounding invariant context, not the region.
            if len(window[0].cells) == len(run_ids) or len(window[-1].cells) == len(run_ids):
                continue
            collapsed = _window_as_unordered(window, run_ids)
            if collapsed is not None:
                span = j - i
                break
        if collapsed is not None:
            for c in collapsed:
                unordered_idx.add(len(out))
                out.append(c)
            i += span
        else:
            out.append(columns[i])
            i += 1
    return out, unordered_idx


def classify(
    columns: list[Column], run_ids: list[str], params: AlignParams | None = None
) -> list[ClassifiedColumn]:
    p = params or AlignParams()
    rewritten, unordered_idx = _detect_unordered(columns, run_ids, p.max_unordered_window)
    result: list[ClassifiedColumn] = []
    for idx, col in enumerate(rewritten):
        if idx in unordered_idx:
            cls = "unordered"
        elif len(col.cells) < len(run_ids):
            cls = "conditional"
        else:
            payloads = {item.payload for item in col.cells.values()}
            cls = "invariant" if len(payloads) == 1 else "parameterized"
        result.append(ClassifiedColumn(column=col, classification=cls))
    return result
