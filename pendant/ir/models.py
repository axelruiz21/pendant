"""Pydantic v2 models for the PENDANT IR.

Normative specification: docs/IR.md. Invariants enforced here at
schema level (validation errors, never warnings):

- postconditions non-empty on every step (invariant 5);
- timeout_ms a positive finite integer, no "no timeout" sentinel
  (invariant 5);
- risk=irreversible forces approval_required=true; an explicit false
  is a contradiction and raises (invariant 15);
- target vectors must populate at least one locator dimension
  (invariant 4);
- unknown fields rejected everywhere (extra="forbid").
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

IR_SCHEMA_VERSION = "0.1"

# Mirror of the store's default Good-Turing promotion threshold
# (invariant 9). The store enforces its configured value at promotion
# time; this constant lets a hand-edited envelope be rejected on load.
DEFAULT_UNSEEN_MASS_THRESHOLD = 0.10

MAX_TIMEOUT_MS = 86_400_000  # 24h; a long wait is a long *number*

PredicateKind = Literal[
    "url_matches",
    "element_visible",
    "text_matches",
    "http_status",
    "row_count",
    "value_equals",
    "file_exists",
]

ActionType = Literal[
    "navigate",
    "click",
    "fill",
    "select",
    "press",
    "upload",
    "download",
    "http_call",
    "extract",
    "assert_only",
]

TARGETLESS_ACTION_TYPES: frozenset[str] = frozenset({"navigate", "http_call"})

FaultPolicy = Literal["retry", "escalate", "rollback", "abort"]
IdempotencyClass = Literal["safe", "unsafe", "compensable"]
RiskClass = Literal["read", "write", "irreversible"]
LifecycleState = Literal["active", "degraded", "quarantined", "retired"]
ReviewState = Literal["draft", "reviewed", "approved"]


class _IRModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TargetVector(_IRModel):
    """Identity vector per invariant 4. Never a single selector."""

    role: str | None = None
    name: str | None = None
    testid: str | None = None
    attrs: dict[str, str] = Field(default_factory=dict)
    css: str | None = None
    xpath: str | None = None
    frame_url: str | None = None
    bbox: tuple[float, float, float, float] | None = None

    @model_validator(mode="after")
    def _at_least_one_locator_dimension(self) -> TargetVector:
        if (
            (self.role is None or self.name is None)
            and self.testid is None
            and not self.attrs
            and self.css is None
            and self.xpath is None
        ):
            raise ValueError(
                "TargetVector empty in all locator dimensions: need one of "
                "role+name, testid, attrs, css, xpath"
            )
        return self


def _require_str(args: dict[str, Any], key: str, kind: str) -> None:
    if not isinstance(args.get(key), str) or not args[key]:
        raise ValueError(f"predicate kind={kind!r} requires string arg {key!r}")


def _require_int(args: dict[str, Any], key: str, kind: str) -> None:
    if not isinstance(args.get(key), int) or isinstance(args.get(key), bool):
        raise ValueError(f"predicate kind={kind!r} requires integer arg {key!r}")


def _require_target(args: dict[str, Any], kind: str) -> None:
    raw = args.get("target")
    if not isinstance(raw, dict | TargetVector):
        raise ValueError(f"predicate kind={kind!r} requires arg 'target' (TargetVector)")
    if isinstance(raw, dict):
        TargetVector.model_validate(raw)


_PREDICATE_ARG_KEYS: dict[str, frozenset[str]] = {
    "url_matches": frozenset({"pattern"}),
    "element_visible": frozenset({"target"}),
    "text_matches": frozenset({"target", "pattern"}),
    "http_status": frozenset({"url_template", "status"}),
    "row_count": frozenset({"target", "op", "value"}),
    "value_equals": frozenset({"target", "value"}),
    "file_exists": frozenset({"path_template"}),
}


class Predicate(_IRModel):
    """A verifiable assertion; args validated per kind (docs/IR.md §2.1)."""

    kind: PredicateKind
    args: dict[str, Any] = Field(default_factory=dict)
    negate: bool = False

    @model_validator(mode="after")
    def _validate_args_for_kind(self) -> Predicate:
        required = _PREDICATE_ARG_KEYS[self.kind]
        got = set(self.args)
        if got != required:
            raise ValueError(
                f"predicate kind={self.kind!r} requires args {sorted(required)}, "
                f"got {sorted(got)}"
            )
        match self.kind:
            case "url_matches":
                _require_str(self.args, "pattern", self.kind)
            case "element_visible":
                _require_target(self.args, self.kind)
            case "text_matches":
                _require_target(self.args, self.kind)
                _require_str(self.args, "pattern", self.kind)
            case "http_status":
                _require_str(self.args, "url_template", self.kind)
                _require_int(self.args, "status", self.kind)
            case "row_count":
                _require_target(self.args, self.kind)
                _require_int(self.args, "value", self.kind)
                if self.args.get("op") not in ("eq", "ge", "le"):
                    raise ValueError("row_count arg 'op' must be one of: eq, ge, le")
            case "value_equals":
                _require_target(self.args, self.kind)
                _require_str(self.args, "value", self.kind)
            case "file_exists":
                _require_str(self.args, "path_template", self.kind)
        return self


class Action(_IRModel):
    type: ActionType
    target_vector: TargetVector | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    tier_preference: list[int] = Field(default_factory=list)

    @field_validator("tier_preference")
    @classmethod
    def _tiers_valid(cls, v: list[int]) -> list[int]:
        if any(t not in (1, 2, 3, 4) for t in v):
            raise ValueError("tier_preference entries must be in {1, 2, 3, 4}")
        if len(set(v)) != len(v):
            raise ValueError("tier_preference entries must be unique")
        return v

    @model_validator(mode="after")
    def _target_required_by_type(self) -> Action:
        if self.type in TARGETLESS_ACTION_TYPES:
            if self.target_vector is not None:
                raise ValueError(f"action type {self.type!r} is target-less")
        elif self.target_vector is None:
            raise ValueError(f"action type {self.type!r} requires a target_vector")
        return self


class OnFault(_IRModel):
    policy: FaultPolicy
    max_retries: int = Field(ge=0)
    backoff_ms: int = Field(default=0, ge=0)
    transfer_to: str | None = None

    @model_validator(mode="after")
    def _policy_consistency(self) -> OnFault:
        if self.policy != "retry" and self.max_retries > 0:
            raise ValueError("max_retries > 0 is only meaningful with policy='retry'")
        if self.policy == "rollback" and self.transfer_to is None:
            raise ValueError("policy='rollback' requires transfer_to")
        if self.policy != "rollback" and self.transfer_to is not None:
            raise ValueError("transfer_to is only meaningful with policy='rollback'")
        return self


class Step(_IRModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    preconditions: list[Predicate] = Field(default_factory=list)
    action: Action
    # Invariant 5: a step without verification is a pick with no
    # part-present sensor. Schema failure, not a warning.
    postconditions: list[Predicate] = Field(min_length=1)
    postcondition_strength: Literal["strong", "weak"] | None = None
    # Invariant 5: finite, positive, bounded. No sentinel for "none".
    timeout_ms: int = Field(gt=0, le=MAX_TIMEOUT_MS)
    on_fault: OnFault
    idempotency: IdempotencyClass
    risk: RiskClass
    approval_required: bool = False
    provenance: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _irreversible_forces_approval(self) -> Step:
        # Invariant 15. An explicit false on an irreversible step is a
        # contradiction the operator must see, not a value to coerce.
        if self.risk == "irreversible" and not self.approval_required:
            if "approval_required" in self.model_fields_set:
                raise ValueError(
                    "risk='irreversible' requires approval_required=true; "
                    "explicit false is a contradiction (invariant 15)"
                )
            self.approval_required = True
        return self


class Edge(_IRModel):
    from_step: str
    to_step: str
    guard: Predicate | None = None
    # Invariant 6: an unknown guard ships as an explicit operator
    # question, never as a plausible guess.
    guard_question: str | None = None

    @model_validator(mode="after")
    def _guard_xor_question(self) -> Edge:
        if self.guard is not None and self.guard_question is not None:
            raise ValueError("edge carries either a guard or a guard_question, not both")
        return self


class IRGraph(_IRModel):
    entry: str
    steps: list[Step] = Field(min_length=1)
    edges: list[Edge] = Field(default_factory=list)

    @model_validator(mode="after")
    def _structural_validity(self) -> IRGraph:
        ids = [s.id for s in self.steps]
        id_set = set(ids)
        if len(ids) != len(id_set):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"duplicate step ids: {dupes}")
        if self.entry not in id_set:
            raise ValueError(f"entry step {self.entry!r} not present in steps")
        for e in self.edges:
            if e.from_step not in id_set or e.to_step not in id_set:
                raise ValueError(
                    f"edge {e.from_step!r} -> {e.to_step!r} references a missing step"
                )
        for s in self.steps:
            if s.on_fault.transfer_to is not None and s.on_fault.transfer_to not in id_set:
                raise ValueError(
                    f"step {s.id!r} on_fault.transfer_to references missing step "
                    f"{s.on_fault.transfer_to!r}"
                )
        # Reachability from entry.
        adjacency: dict[str, list[str]] = {i: [] for i in id_set}
        for e in self.edges:
            adjacency[e.from_step].append(e.to_step)
        seen: set[str] = set()
        stack = [self.entry]
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            stack.extend(adjacency[node])
        unreachable = sorted(id_set - seen)
        if unreachable:
            raise ValueError(f"steps unreachable from entry: {unreachable}")
        # A branch without a guard or a question is a decision the
        # operator never made.
        out_edges: dict[str, list[Edge]] = {}
        for e in self.edges:
            out_edges.setdefault(e.from_step, []).append(e)
        for step_id, outs in out_edges.items():
            if len(outs) >= 2:
                for e in outs:
                    if e.guard is None and e.guard_question is None:
                        raise ValueError(
                            f"step {step_id!r} branches; every outgoing edge needs "
                            "a guard or a guard_question"
                        )
        return self

    def step(self, step_id: str) -> Step:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise KeyError(step_id)


class Parameter(_IRModel):
    name: str = Field(min_length=1)
    value_class: str = Field(min_length=1)
    example_redacted: str | None = None


class CoverageEstimate(_IRModel):
    """Good-Turing coverage over observed variants (invariant 9)."""

    runs: int = Field(ge=0)
    distinct_variants: int = Field(ge=0)
    singleton_variants: int = Field(ge=0)
    unseen_mass: float = Field(ge=0.0, le=1.0)


class HistoryEntry(_IRModel):
    at: str  # ISO 8601
    actor: str = Field(min_length=1)
    event: str = Field(min_length=1)
    detail: dict[str, Any] = Field(default_factory=dict)


class ProcessEnvelope(_IRModel):
    process_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: int = Field(ge=1)
    parent_version: int | None = None
    ir_schema_version: str
    parameter_signature: list[Parameter] = Field(default_factory=list)
    lifecycle_state: LifecycleState = "active"
    review_state: ReviewState = "draft"
    coverage_estimate: CoverageEstimate | None = None
    graph: IRGraph
    history: list[HistoryEntry] = Field(default_factory=list)

    @field_validator("ir_schema_version")
    @classmethod
    def _schema_version_supported(cls, v: str) -> str:
        if v != IR_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported ir_schema_version {v!r}; this build reads {IR_SCHEMA_VERSION!r}"
            )
        return v

    @model_validator(mode="after")
    def _promotion_requires_coverage(self) -> ProcessEnvelope:
        # Mirror of the store's Good-Turing promotion gate (invariant
        # 9): a hand-edited document cannot claim review progress
        # without coverage evidence below threshold.
        if self.review_state != "draft":
            if self.coverage_estimate is None:
                raise ValueError(
                    f"review_state={self.review_state!r} requires a coverage_estimate"
                )
            if self.coverage_estimate.unseen_mass > DEFAULT_UNSEEN_MASS_THRESHOLD:
                raise ValueError(
                    "review_state beyond draft with estimated unseen variant mass "
                    f"{self.coverage_estimate.unseen_mass:.3f} > "
                    f"{DEFAULT_UNSEEN_MASS_THRESHOLD} (invariant 9)"
                )
        if self.parent_version is not None and self.parent_version >= self.version:
            raise ValueError("parent_version must be < version")
        return self
