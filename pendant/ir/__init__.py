"""IR: the reliability contract of the entire system.

Pydantic v2 models with invariant validators. Specified formally in
docs/IR.md; every violation listed there raises ValidationError at
construction time. There are no warning-level IR checks.
"""

from pendant.ir.models import (
    IR_SCHEMA_VERSION,
    Action,
    ActionType,
    CoverageEstimate,
    Edge,
    HistoryEntry,
    IdempotencyClass,
    IRGraph,
    LifecycleState,
    OnFault,
    Parameter,
    Predicate,
    PredicateKind,
    ProcessEnvelope,
    ReviewState,
    RiskClass,
    Step,
    TargetVector,
)

__all__ = [
    "IR_SCHEMA_VERSION",
    "Action",
    "ActionType",
    "CoverageEstimate",
    "Edge",
    "HistoryEntry",
    "IRGraph",
    "IdempotencyClass",
    "LifecycleState",
    "OnFault",
    "Parameter",
    "Predicate",
    "PredicateKind",
    "ProcessEnvelope",
    "ReviewState",
    "RiskClass",
    "Step",
    "TargetVector",
]
