"""AlignmentReport: the serialized align -> induce boundary.

The model downstream never aligns anything (invariant 2); it receives
this report as evidence, with per-column provenance back to source
event ids.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pendant.align.classifier import classify
from pendant.align.msa import AlignParams, SeqItem, progressive_align
from pendant.align.normalizer import NormalizerRules, canonical_token, payload_key
from pendant.capture.schema import RunTrace

ColumnClass = Literal["invariant", "parameterized", "conditional", "unordered"]


class AlignedCell(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    event_id: str
    payload_repr: str


class AlignedColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    token: str
    kind: str
    role: str
    name: str
    url: str
    classification: ColumnClass
    runs_present: list[str]
    payload_variants: int
    sample_values: list[str] = Field(default_factory=list)
    cells: list[AlignedCell]


class AlignmentReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_ids: list[str]
    columns: list[AlignedColumn]
    params: dict[str, float | int]


def align_runs(
    traces: list[RunTrace],
    rules: NormalizerRules | None = None,
    params: AlignParams | None = None,
) -> AlignmentReport:
    """Canonicalize, align, classify: RunTraces in, AlignmentReport out."""
    rules = rules or NormalizerRules()
    p = params or AlignParams()
    sequences: dict[str, list[SeqItem]] = {}
    for trace in traces:
        items: list[SeqItem] = []
        for event in trace.events:
            token = canonical_token(event, rules)
            if token is None:
                continue
            items.append(
                SeqItem(token=token, event_id=event.event_id, payload=payload_key(event, rules))
            )
        sequences[trace.run_id] = items
    run_ids = sorted(sequences)
    matrix = progressive_align(sequences, p)
    classified = classify(matrix, run_ids, p)
    columns: list[AlignedColumn] = []
    for idx, cc in enumerate(classified):
        col = cc.column
        cells = [
            AlignedCell(
                run_id=run_id,
                event_id=item.event_id,
                payload_repr=repr(item.payload),
            )
            for run_id, item in sorted(col.cells.items())
        ]
        distinct_payloads = sorted({repr(item.payload) for item in col.cells.values()})
        columns.append(
            AlignedColumn(
                index=idx,
                token=str(col.token),
                kind=col.token.kind,
                role=col.token.role,
                name=col.token.name,
                url=col.token.url,
                classification=cc.classification,  # type: ignore[arg-type]
                runs_present=sorted(col.cells),
                payload_variants=len(distinct_payloads),
                sample_values=distinct_payloads[:10],
                cells=cells,
            )
        )
    return AlignmentReport(
        run_ids=run_ids,
        columns=columns,
        params={
            "match": p.match,
            "gap_open": p.gap_open,
            "gap_extend": p.gap_extend,
            "max_unordered_window": p.max_unordered_window,
        },
    )
