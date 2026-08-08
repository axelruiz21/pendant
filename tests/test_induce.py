"""Induction engine: schema gate, reject-and-retry, instrumentation.

The engine is tested against a deterministic ReplayProvider simulating
model outputs (good, malformed, and evidence-violating); no network,
no mocks of internal logic.
"""

import itertools
import json
from pathlib import Path
from typing import Any

import pytest

from pendant.align import align_runs
from pendant.align.report import AlignmentReport
from pendant.capture.schema import load_run_trace
from pendant.induce import InductionFailed, ReplayProvider, induce
from pendant.induce.schema import InducedProcess, PostconditionProposal

FIXTURES = Path(__file__).parent / "fixtures" / "traces"


@pytest.fixture(scope="module")
def report() -> AlignmentReport:
    d = FIXTURES / "s2_conditional"
    return align_runs([load_run_trace(p) for p in sorted(d.glob("*.ndjson"))])


def build_valid_payload(report: AlignmentReport) -> dict[str, Any]:
    """Simulate a perfect model: derive an induced process from evidence."""
    steps: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    conditional_cols: list[int] = []
    for col in report.columns:
        if col.classification == "conditional":
            conditional_cols.append(col.index)
            continue
        if col.kind not in ("navigate", "click", "input", "key"):
            continue  # network/dialog columns are evidence, not operator actions
        provenance = [cell.event_id for cell in col.cells]
        step_id = f"s{col.index}"
        action: dict[str, Any]
        if col.kind == "navigate":
            action = {"type": "navigate", "params": {"url": col.url}}
        elif col.kind == "key":
            action = {"type": "press", "params": {"keys": col.name}}
        elif col.kind == "input":
            action = {
                "type": "fill",
                "target_vector": {"role": col.role, "name": col.name},
                "params": {},
            }
        else:
            action = {
                "type": "click",
                "target_vector": {"role": col.role, "name": col.name},
            }
        steps.append(
            {
                "id": step_id,
                "label": f"{col.kind} {col.name or col.url}",
                "action": action,
                "proposed_postconditions": [
                    {
                        "predicate": {
                            "kind": "http_status",
                            "args": {"url_template": "/api/orders", "status": 201},
                        },
                        "strength": "strong",
                    }
                ],
                "timeout_ms": 10000,
                "on_fault": {"policy": "escalate", "max_retries": 0},
                "idempotency": "safe",
                "risk": "read",
                "provenance": provenance,
                "confidence": 0.9,
            }
        )
    if conditional_cols:
        questions.append(
            {
                "column_indices": conditional_cols,
                "question": "When quantity exceeds a limit an approval dialog appears - "
                "what rule decides whether approval is required?",
            }
        )
    edges = [
        {"from_step": a["id"], "to_step": b["id"]} for a, b in itertools.pairwise(steps)
    ]
    return {
        "name": "Order entry",
        "entry": steps[0]["id"],
        "steps": steps,
        "edges": edges,
        "questions": questions,
    }


class TestInduce:
    def test_valid_first_pass(self, report: AlignmentReport, tmp_path: Path) -> None:
        payload = build_valid_payload(report)
        provider = ReplayProvider([json.dumps(payload)])
        metrics_path = tmp_path / "metrics.jsonl"
        process, metrics = induce(report, [], provider, metrics_path=metrics_path)
        assert metrics.schema_first_pass is True
        assert metrics.attempts == 1
        assert metrics.postcondition_proposal_rate == 1.0
        assert metrics.conditional_regions == 1
        assert metrics.questions_emitted == 1
        assert metrics.well_formed_question_rate == 1.0
        assert len(process.steps) == metrics.n_steps
        logged = json.loads(metrics_path.read_text().splitlines()[0])
        assert logged["provider"] == "replay"

    def test_reject_and_retry_on_schema_failure(self, report: AlignmentReport) -> None:
        payload = build_valid_payload(report)
        provider = ReplayProvider(["this is not json at all", json.dumps(payload)])
        _, metrics = induce(report, [], provider)
        assert metrics.attempts == 2
        assert metrics.schema_first_pass is False

    def test_phantom_provenance_rejected(self, report: AlignmentReport) -> None:
        payload = build_valid_payload(report)
        payload["steps"][0]["provenance"] = ["evt-that-never-happened"]
        provider = ReplayProvider([json.dumps(payload)] * 3)
        with pytest.raises(InductionFailed, match="provenance"):
            induce(report, [], provider)

    def test_missing_conditional_question_rejected(self, report: AlignmentReport) -> None:
        payload = build_valid_payload(report)
        payload["questions"] = []
        provider = ReplayProvider([json.dumps(payload)] * 3)
        with pytest.raises(InductionFailed, match="conditional region"):
            induce(report, [], provider)

    def test_null_postcondition_requires_substantive_reason(self) -> None:
        with pytest.raises(ValueError, match="substantive"):
            PostconditionProposal(null_reason="n/a")
        proposal = PostconditionProposal(
            null_reason="No Tier 1 evidence exists for this read-only focus step."
        )
        assert proposal.predicate is None

    def test_to_envelope_requires_resolved_postconditions(
        self, report: AlignmentReport
    ) -> None:
        payload = build_valid_payload(report)
        process = InducedProcess.model_validate(payload)
        envelope = process.to_envelope("proc-test")
        assert envelope.review_state == "draft"
        assert all(s.postconditions for s in envelope.graph.steps)

        payload["steps"][0]["proposed_postconditions"] = [
            {"null_reason": "No system-of-record evidence for this navigation step."}
        ]
        unresolved = InducedProcess.model_validate(payload)
        with pytest.raises(ValueError, match="without resolved postconditions"):
            unresolved.to_envelope("proc-test")


class TestPlaceholderValidation:
    """Placeholders must name declared parameters (D-018)."""

    def test_undeclared_placeholder_rejected(self, report: AlignmentReport) -> None:
        payload = build_valid_payload(report)
        payload["steps"][0]["action"]["params"] = {"url": "https://x/orders/{order_id}"}
        provider = ReplayProvider([json.dumps(payload)] * 3)
        with pytest.raises(InductionFailed, match="placeholder"):
            induce(report, [], provider)

    def test_declared_placeholder_accepted(self, report: AlignmentReport) -> None:
        payload = build_valid_payload(report)
        payload["steps"][0]["action"]["params"] = {"url": "https://x/orders/{order_id}"}
        payload["parameters"] = [{"name": "order_id", "value_class": "string"}]
        provider = ReplayProvider([json.dumps(payload)])
        process, metrics = induce(report, [], provider)
        assert metrics.schema_first_pass is True
        assert process.parameters[0].name == "order_id"
