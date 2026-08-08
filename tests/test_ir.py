"""Validator coverage for pendant/ir/.

Required by Part V step 3: an empty-postconditions step and a null
timeout must both raise, and risk=irreversible must force
approval_required. The rest covers every invariant listed in
docs/IR.md §4.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from pendant.ir import (
    IR_SCHEMA_VERSION,
    Action,
    CoverageEstimate,
    Edge,
    IRGraph,
    OnFault,
    Predicate,
    ProcessEnvelope,
    Step,
    TargetVector,
)
from pendant.ir.graph import render_text, traversal_order

VECTOR = {
    "role": "button",
    "name": "Submit order",
    "testid": "submit-btn",
    "attrs": {"type": "submit"},
    "css": "form > button.primary",
    "xpath": "/html/body/form/button[1]",
    "frame_url": "https://app.example.test/orders",
    "bbox": (100.0, 200.0, 80.0, 32.0),
}

POST = {"kind": "http_status", "args": {"url_template": "/api/orders/{p0}", "status": 201}}


def step_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "s1",
        "label": "Submit the order form",
        "action": {"type": "click", "target_vector": VECTOR, "tier_preference": [1, 2]},
        "postconditions": [POST],
        "timeout_ms": 10_000,
        "on_fault": {"policy": "retry", "max_retries": 2, "backoff_ms": 500},
        "idempotency": "unsafe",
        "risk": "write",
        "provenance": ["evt-001", "evt-002"],
        "confidence": 0.9,
    }
    base.update(overrides)
    return base


def make_step(**overrides: Any) -> Step:
    return Step.model_validate(step_kwargs(**overrides))


class TestStepInvariants:
    def test_valid_step_constructs(self) -> None:
        step = make_step()
        assert step.approval_required is False

    def test_empty_postconditions_raises(self) -> None:
        with pytest.raises(ValidationError, match="postconditions"):
            make_step(postconditions=[])

    def test_null_timeout_raises(self) -> None:
        with pytest.raises(ValidationError, match="timeout_ms"):
            make_step(timeout_ms=None)

    def test_missing_timeout_raises(self) -> None:
        kwargs = step_kwargs()
        del kwargs["timeout_ms"]
        with pytest.raises(ValidationError, match="timeout_ms"):
            Step.model_validate(kwargs)

    def test_zero_and_negative_timeout_raise(self) -> None:
        for bad in (0, -1):
            with pytest.raises(ValidationError, match="timeout_ms"):
                make_step(timeout_ms=bad)

    def test_infinite_timeout_unrepresentable(self) -> None:
        with pytest.raises(ValidationError, match="timeout_ms"):
            make_step(timeout_ms=float("inf"))
        with pytest.raises(ValidationError, match="timeout_ms"):
            make_step(timeout_ms=86_400_001)  # above the 24h bound

    def test_irreversible_forces_approval_required(self) -> None:
        step = make_step(risk="irreversible")
        assert step.approval_required is True

    def test_irreversible_with_explicit_false_raises(self) -> None:
        with pytest.raises(ValidationError, match="invariant 15"):
            make_step(risk="irreversible", approval_required=False)

    def test_irreversible_survives_serialization_round_trip(self) -> None:
        step = make_step(risk="irreversible")
        again = Step.model_validate_json(step.model_dump_json())
        assert again.approval_required is True

    def test_confidence_bounds(self) -> None:
        with pytest.raises(ValidationError, match="confidence"):
            make_step(confidence=1.5)

    def test_unknown_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_step(surprise="value")


class TestTargetVector:
    def test_empty_vector_raises(self) -> None:
        with pytest.raises(ValidationError, match="locator dimensions"):
            TargetVector()

    def test_role_without_name_is_insufficient_alone(self) -> None:
        with pytest.raises(ValidationError, match="locator dimensions"):
            TargetVector(role="button")

    def test_single_dimension_suffices(self) -> None:
        assert TargetVector(testid="submit-btn").testid == "submit-btn"
        assert TargetVector(role="button", name="Submit").role == "button"


class TestPredicate:
    def test_args_validated_per_kind(self) -> None:
        with pytest.raises(ValidationError, match="url_matches"):
            Predicate(kind="url_matches", args={})
        with pytest.raises(ValidationError, match="requires args"):
            Predicate(kind="http_status", args={"url_template": "/x", "status": 200, "y": 1})
        with pytest.raises(ValidationError, match="integer"):
            Predicate(kind="http_status", args={"url_template": "/x", "status": "200"})

    def test_row_count_op_enum(self) -> None:
        with pytest.raises(ValidationError, match="op"):
            Predicate(
                kind="row_count",
                args={"target": {"testid": "grid"}, "op": "gt", "value": 3},
            )

    def test_target_arg_is_a_real_vector(self) -> None:
        with pytest.raises(ValidationError, match="locator dimensions"):
            Predicate(kind="element_visible", args={"target": {"frame_url": "https://x"}})

    def test_valid_predicates(self) -> None:
        Predicate(kind="url_matches", args={"pattern": r"/orders/\d+"})
        Predicate(kind="element_visible", args={"target": {"testid": "toast"}}, negate=True)
        Predicate(
            kind="row_count", args={"target": {"testid": "grid"}, "op": "ge", "value": 1}
        )


class TestAction:
    def test_targetless_types_reject_targets(self) -> None:
        with pytest.raises(ValidationError, match="target-less"):
            Action(type="navigate", target_vector=TargetVector(testid="x"))

    def test_targeted_types_require_targets(self) -> None:
        with pytest.raises(ValidationError, match="requires a target_vector"):
            Action(type="click")

    def test_tier_preference_validated(self) -> None:
        with pytest.raises(ValidationError, match="tier_preference"):
            Action(type="navigate", tier_preference=[5])
        with pytest.raises(ValidationError, match="unique"):
            Action(type="navigate", tier_preference=[1, 1])

    def test_unknown_action_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Action.model_validate({"type": "teleport"})


class TestOnFault:
    def test_rollback_requires_transfer_to(self) -> None:
        with pytest.raises(ValidationError, match="transfer_to"):
            OnFault(policy="rollback", max_retries=0)

    def test_retries_only_with_retry_policy(self) -> None:
        with pytest.raises(ValidationError, match="max_retries"):
            OnFault(policy="abort", max_retries=3)


def two_step_graph(**edge_overrides: Any) -> IRGraph:
    s1 = make_step(id="s1")
    s2 = make_step(id="s2", label="Verify confirmation")
    edge: dict[str, Any] = {"from_step": "s1", "to_step": "s2"}
    edge.update(edge_overrides)
    return IRGraph(entry="s1", steps=[s1, s2], edges=[Edge.model_validate(edge)])


class TestGraph:
    def test_valid_graph(self) -> None:
        g = two_step_graph()
        assert traversal_order(g) == ["s1", "s2"]

    def test_duplicate_ids_raise(self) -> None:
        with pytest.raises(ValidationError, match="duplicate"):
            IRGraph(entry="s1", steps=[make_step(id="s1"), make_step(id="s1")], edges=[])

    def test_missing_entry_raises(self) -> None:
        with pytest.raises(ValidationError, match="entry"):
            IRGraph(entry="nope", steps=[make_step(id="s1")], edges=[])

    def test_dangling_edge_raises(self) -> None:
        with pytest.raises(ValidationError, match="missing step"):
            IRGraph(
                entry="s1",
                steps=[make_step(id="s1")],
                edges=[Edge(from_step="s1", to_step="ghost")],
            )

    def test_unreachable_step_raises(self) -> None:
        with pytest.raises(ValidationError, match="unreachable"):
            IRGraph(entry="s1", steps=[make_step(id="s1"), make_step(id="s2")], edges=[])

    def test_branch_without_guard_or_question_raises(self) -> None:
        steps = [make_step(id=i) for i in ("s1", "s2", "s3")]
        edges = [Edge(from_step="s1", to_step="s2"), Edge(from_step="s1", to_step="s3")]
        with pytest.raises(ValidationError, match="guard"):
            IRGraph(entry="s1", steps=steps, edges=edges)

    def test_branch_with_question_is_valid(self) -> None:
        steps = [make_step(id=i) for i in ("s1", "s2", "s3")]
        edges = [
            Edge(
                from_step="s1",
                to_step="s2",
                guard=Predicate(kind="url_matches", args={"pattern": "/a"}),
            ),
            Edge(
                from_step="s1",
                to_step="s3",
                guard_question="When do you take the manual-review path instead?",
            ),
        ]
        g = IRGraph(entry="s1", steps=steps, edges=edges)
        assert len(g.edges) == 2

    def test_guard_and_question_mutually_exclusive(self) -> None:
        with pytest.raises(ValidationError, match="not both"):
            two_step_graph(
                guard={"kind": "url_matches", "args": {"pattern": "/a"}},
                guard_question="Which path?",
            )

    def test_transfer_to_must_exist(self) -> None:
        bad = make_step(
            id="s1",
            on_fault={"policy": "rollback", "max_retries": 0, "transfer_to": "ghost"},
        )
        with pytest.raises(ValidationError, match="transfer_to"):
            IRGraph(entry="s1", steps=[bad], edges=[])


def make_envelope(**overrides: Any) -> ProcessEnvelope:
    base: dict[str, Any] = {
        "process_id": "proc-1",
        "name": "Order entry",
        "version": 1,
        "ir_schema_version": IR_SCHEMA_VERSION,
        "graph": two_step_graph(),
    }
    base.update(overrides)
    return ProcessEnvelope.model_validate(base)


class TestEnvelope:
    def test_draft_without_coverage_is_fine(self) -> None:
        env = make_envelope()
        assert env.review_state == "draft"

    def test_promotion_without_coverage_raises(self) -> None:
        with pytest.raises(ValidationError, match="coverage_estimate"):
            make_envelope(review_state="reviewed")

    def test_promotion_above_unseen_mass_threshold_raises(self) -> None:
        cov = CoverageEstimate(
            runs=3, distinct_variants=3, singleton_variants=3, unseen_mass=1.0
        )
        with pytest.raises(ValidationError, match="invariant 9"):
            make_envelope(review_state="reviewed", coverage_estimate=cov)

    def test_promotion_below_threshold_passes(self) -> None:
        cov = CoverageEstimate(
            runs=20, distinct_variants=4, singleton_variants=1, unseen_mass=0.05
        )
        env = make_envelope(review_state="reviewed", coverage_estimate=cov)
        assert env.review_state == "reviewed"

    def test_newer_schema_version_refused(self) -> None:
        with pytest.raises(ValidationError, match="ir_schema_version"):
            make_envelope(ir_schema_version="99.0")

    def test_version_lineage(self) -> None:
        with pytest.raises(ValidationError, match="parent_version"):
            make_envelope(version=2, parent_version=2)

    def test_render_text_is_printable(self) -> None:
        text = render_text(make_envelope())
        assert "PROCESS Order entry" in text
        assert "[s1] Submit the order form" in text
        assert "timeout: 10000 ms" in text
        assert "coverage: not yet estimated" in text


class TestHttpStatusMethodArg:
    """Optional method pin on http_status (D-018, D-017 review)."""

    def test_method_accepted(self) -> None:
        p = Predicate(
            kind="http_status",
            args={"url_template": "/api/orders", "status": 201, "method": "POST"},
        )
        assert p.args["method"] == "POST"

    def test_invalid_method_rejected(self) -> None:
        with pytest.raises(ValidationError, match="method"):
            Predicate(
                kind="http_status",
                args={"url_template": "/api/orders", "status": 201, "method": "YEET"},
            )

    def test_extraneous_arg_still_rejected(self) -> None:
        with pytest.raises(ValidationError, match="requires args"):
            Predicate(
                kind="http_status",
                args={"url_template": "/x", "status": 200, "verb": "POST"},
            )

    def test_method_not_valid_on_other_kinds(self) -> None:
        with pytest.raises(ValidationError, match="requires args"):
            Predicate(kind="url_matches", args={"pattern": "x", "method": "GET"})
