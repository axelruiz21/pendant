"""Schema-constrained induction engine with instrumentation.

Provider-independent: all validation, retry, and metrics logic is
tested against a deterministic ReplayProvider; Gate 2 runs against a
real provider (docs/DECISIONS.md D-009). Provider implementations —
Anthropic, any OpenAI-compatible endpoint, manual file exchange —
live in pendant.induce.providers (D-016).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from pendant.align.report import AlignmentReport
from pendant.induce.prompt import build_prompt, build_retry_prompt
from pendant.induce.providers import LLMProvider
from pendant.induce.schema import InducedProcess
from pendant.ir.models import placeholder_names


class InductionFailed(Exception):
    pass


@dataclass
class InductionMetrics:
    """Invariant 10 instrumentation, logged on every invocation."""

    at: str
    provider: str
    attempts: int
    schema_first_pass: bool
    n_steps: int
    postcondition_proposal_rate: float
    conditional_regions: int
    questions_emitted: int
    well_formed_question_rate: float
    # Operator corrections are counted during review and appended
    # separately via log_corrections; Gate 2 reads both records.


def _conditional_region_count(report: AlignmentReport) -> int:
    """Adjacent conditional columns sharing a presence set form one region."""
    regions = 0
    previous_presence: tuple[str, ...] | None = None
    for column in report.columns:
        if column.classification == "conditional":
            presence = tuple(column.runs_present)
            if presence != previous_presence:
                regions += 1
            previous_presence = presence
        else:
            previous_presence = None
    return regions


def _cross_validate(process: InducedProcess, report: AlignmentReport) -> None:
    """Checks beyond the schema: evidence discipline (invariant 6)."""
    declared = {p.name for p in process.parameters}
    for step in process.steps:
        used = placeholder_names(step.action.params)
        for proposal in step.proposed_postconditions:
            if proposal.predicate is not None:
                used |= placeholder_names(proposal.predicate.args)
        undeclared = sorted(used - declared)
        if undeclared:
            raise ValueError(
                f"step {step.id!r} uses placeholder(s) {undeclared} that are not "
                "declared process parameters; declare each as a parameter with "
                "that exact name (rename machine tokens like {p0} to the "
                "operator-meaningful parameter they correspond to)"
            )
    known_events = {cell.event_id for col in report.columns for cell in col.cells}
    for step in process.steps:
        phantom = [e for e in step.provenance if e not in known_events]
        if phantom:
            raise ValueError(
                f"step {step.id!r} cites provenance not present in the evidence: {phantom}"
            )
    regions = _conditional_region_count(report)
    if regions and len(process.questions) < regions:
        raise ValueError(
            f"alignment report contains {regions} conditional region(s) but only "
            f"{len(process.questions)} clarifying question(s) were emitted"
        )
    conditional_indices = {
        c.index for c in report.columns if c.classification == "conditional"
    }
    for q in process.questions:
        outside = [i for i in q.column_indices if i not in conditional_indices]
        if outside:
            raise ValueError(
                f"question references non-conditional columns {outside}"
            )


def _parse(text: str) -> InducedProcess:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
    return InducedProcess.model_validate_json(cleaned)


def induce(
    report: AlignmentReport,
    narration: list[str],
    provider: LLMProvider,
    *,
    max_attempts: int = 3,
    metrics_path: Path | None = None,
) -> tuple[InducedProcess, InductionMetrics]:
    base_prompt = build_prompt(report, narration)
    prompt = base_prompt
    last_error = ""
    process: InducedProcess | None = None
    attempts = 0
    while attempts < max_attempts:
        attempts += 1
        output = provider.complete(prompt)
        try:
            candidate = _parse(output)
            _cross_validate(candidate, report)
            process = candidate
            break
        except (ValidationError, ValueError) as exc:
            last_error = str(exc)
            prompt = build_retry_prompt(base_prompt, output, last_error)
    if process is None:
        raise InductionFailed(
            f"no schema-valid induction after {max_attempts} attempts; last error: "
            f"{last_error}"
        )
    proposal_rate = (
        sum(1 for s in process.steps if s.has_proposed_postcondition) / len(process.steps)
    )
    well_formed = (
        sum(1 for q in process.questions if q.well_formed) / len(process.questions)
        if process.questions
        else 1.0
    )
    metrics = InductionMetrics(
        at=datetime.now(UTC).isoformat(),
        provider=provider.name,
        attempts=attempts,
        schema_first_pass=attempts == 1,
        n_steps=len(process.steps),
        postcondition_proposal_rate=proposal_rate,
        conditional_regions=_conditional_region_count(report),
        questions_emitted=len(process.questions),
        well_formed_question_rate=well_formed,
    )
    if metrics_path is not None:
        log_metrics(metrics_path, asdict(metrics))
    return process, metrics


def log_metrics(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def log_corrections(path: Path, process_id: str, corrections: int, nodes: int) -> float:
    """Record operator corrections counted during review (Gate 2 input)."""
    per_10 = corrections / nodes * 10 if nodes else 0.0
    log_metrics(
        path,
        {
            "at": datetime.now(UTC).isoformat(),
            "kind": "corrections",
            "process_id": process_id,
            "corrections": corrections,
            "nodes": nodes,
            "corrections_per_10_nodes": per_10,
        },
    )
    return per_10
