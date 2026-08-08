"""Output schema for the inductor (docs/DECISIONS.md D-013).

This is deliberately NOT the IR itself: the IR requires a non-empty
postconditions list on every step (invariant 5), while the inductor is
required to say "no postcondition proposed, here is why" explicitly
rather than fabricate one (invariant 6). The induced form therefore
carries postcondition *proposals* — predicate or null-with-reason —
and converts to a schema-valid IR envelope only once every step has a
resolved postcondition (model-proposed or reviewer-authored).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pendant.ir.models import (
    IR_SCHEMA_VERSION,
    Action,
    Edge,
    IRGraph,
    OnFault,
    Parameter,
    Predicate,
    ProcessEnvelope,
    Step,
)


class PostconditionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    predicate: Predicate | None = None
    strength: Literal["strong", "weak"] | None = None
    null_reason: str | None = None

    @model_validator(mode="after")
    def _predicate_xor_reason(self) -> PostconditionProposal:
        if self.predicate is not None:
            if self.null_reason is not None:
                raise ValueError("a proposal carries a predicate or a null_reason, not both")
            if self.strength is None:
                raise ValueError("a proposed predicate must be rated strong or weak")
        else:
            if self.null_reason is None or len(self.null_reason.strip()) < 15:
                raise ValueError(
                    "a null postcondition requires a substantive stated reason, "
                    "never a placeholder"
                )
        return self


class InducedStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    preconditions: list[Predicate] = Field(default_factory=list)
    action: Action
    proposed_postconditions: list[PostconditionProposal] = Field(min_length=1)
    timeout_ms: int = Field(gt=0, le=86_400_000)
    on_fault: OnFault
    idempotency: Literal["safe", "unsafe", "compensable"]
    risk: Literal["read", "write", "irreversible"]
    approval_required: bool = False
    provenance: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _irreversible_forces_approval(self) -> InducedStep:
        # Mirror IR invariant 15 / D-005: do not paper over an explicit false.
        if self.risk == "irreversible" and not self.approval_required:
            if "approval_required" in self.model_fields_set:
                raise ValueError(
                    "risk='irreversible' requires approval_required=true; "
                    "explicit false is a contradiction (invariant 15)"
                )
            self.approval_required = True
        return self

    @property
    def has_proposed_postcondition(self) -> bool:
        return any(p.predicate is not None for p in self.proposed_postconditions)


class ConditionalQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column_indices: list[int] = Field(min_length=1)
    question: str = Field(min_length=10)

    @property
    def well_formed(self) -> bool:
        return self.question.strip().endswith("?")


class InducedProcess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    parameters: list[Parameter] = Field(default_factory=list)
    entry: str
    steps: list[InducedStep] = Field(min_length=1)
    edges: list[Edge] = Field(default_factory=list)
    questions: list[ConditionalQuestion] = Field(default_factory=list)

    def to_envelope(self, process_id: str, version: int = 1) -> ProcessEnvelope:
        """Build a schema-valid draft IR envelope.

        Raises ValueError while any step still lacks a resolved
        postcondition: the IR contract (invariant 5) is not negotiable,
        so unresolved steps must be settled in review first.
        """
        unresolved = [s.id for s in self.steps if not s.has_proposed_postcondition]
        if unresolved:
            raise ValueError(
                f"steps without resolved postconditions cannot enter the IR: {unresolved}"
            )
        ir_steps = []
        for s in self.steps:
            predicates = [p.predicate for p in self.steps_postconditions(s.id)]
            strengths = {p.strength for p in self.steps_postconditions(s.id)}
            ir_steps.append(
                Step(
                    id=s.id,
                    label=s.label,
                    preconditions=s.preconditions,
                    action=s.action,
                    postconditions=[p for p in predicates if p is not None],
                    postcondition_strength="weak" if "weak" in strengths else "strong",
                    timeout_ms=s.timeout_ms,
                    on_fault=s.on_fault,
                    idempotency=s.idempotency,
                    risk=s.risk,
                    approval_required=s.approval_required or s.risk == "irreversible",
                    provenance=s.provenance,
                    confidence=s.confidence,
                )
            )
        return ProcessEnvelope(
            process_id=process_id,
            name=self.name,
            version=version,
            ir_schema_version=IR_SCHEMA_VERSION,
            parameter_signature=self.parameters,
            graph=IRGraph(entry=self.entry, steps=ir_steps, edges=self.edges),
        )

    def steps_postconditions(self, step_id: str) -> list[PostconditionProposal]:
        for s in self.steps:
            if s.id == step_id:
                return [p for p in s.proposed_postconditions if p.predicate is not None]
        raise KeyError(step_id)
