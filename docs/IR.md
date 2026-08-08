# PENDANT IR — Formal Specification

Status: normative for Phase 0. Implemented by `pendant/ir/`. Every
change to this document requires a corresponding entry in
`docs/DECISIONS.md` and a version bump of `IR_SCHEMA_VERSION`.

The IR is the reliability contract of the entire system. Everything
upstream (capture, align, induce) exists to produce it; everything
downstream (review, compile, run, reliability) exists to consume it.
It is a serializable, human-readable JSON document validated by
Pydantic v2 models. If PENDANT disappears, an IR document must still
read as an unambiguous, auditable description of a process.

## 1. Design rationale

### 1.1 Why postconditions are mandatory (schema-level, not lint-level)

A step without a postcondition is a pick with no part-present sensor.
The automation "succeeds" whenever its actions ran, regardless of
whether the world changed the way the operator intended. Every
downstream reliability mechanism — fault classification, quarantine
transitions, first-pass yield, shadow-mode outcome comparison —
consumes postcondition results as its input signal. A step with an
empty postconditions list emits no signal, so its failures are
*silent by construction* and surface later, in someone else's process,
without attribution. That failure mode (silent success) carries the
top RPN in `docs/FMEA.md`.

Making the empty list a validation *error* rather than a warning is
deliberate: warnings accumulate and get suppressed under schedule
pressure; schema failures cannot be merged. Where no meaningful
postcondition exists, the inductor must say so explicitly (proposed
postcondition `null` with a stated reason) and the reviewer must
author one before approval. The escape valve is human judgment
recorded in review, never a silently empty list.

### 1.2 Why timeout_ms must be finite

An unbounded wait produces a hang, and a hang is worse than a fault
because it does not alarm. A fault produces a structured record, a
lifecycle transition, and an operator page. A hang produces nothing:
no MTBF datapoint, no p-chart increment, no quarantine. It consumes
the fleet silently. Therefore `timeout_ms` is a required positive
finite integer on every step. There is no "no timeout" sentinel (no
0, no -1, no null): if a step legitimately needs a long wait, it gets
a long *number*, chosen consciously and visible in review.

### 1.3 Other schema-enforced invariants

- `risk = irreversible` forces `approval_required = true`
  (invariant 15). The model coerces on construction; an explicit
  `approval_required = false` on an irreversible step is a validation
  error, not a coercion, so the contradiction is surfaced rather than
  papered over. Clearing the flag later is a review-surface operation
  requiring an attributed override in process history (Phase 1).
- `action.target_vector` is the full identity vector (invariant 4);
  single-selector targets are unrepresentable.
- Every step carries `provenance` (source event ids) and `confidence`.
  Steps invented without evidence must say so with an empty provenance
  list and appropriately low confidence; review poka-yoke keys off
  these fields.
- The process envelope carries lifecycle state, review state, coverage
  estimate, and version lineage. Review-state transitions are
  monotonic per version: `draft -> reviewed -> approved`.

## 2. Types

All models are Pydantic v2, `model_config = ConfigDict(extra="forbid")`
throughout. Unknown fields are rejected: the IR is a contract, not a
property bag.

### 2.1 Predicate

```
Predicate = {
  kind:  "url_matches" | "element_visible" | "text_matches" |
         "http_status" | "row_count" | "value_equals" | "file_exists",
  args:  object            # kind-specific, see table
  negate: bool = false
}
```

| kind            | required args                             | evaluates against |
|-----------------|-------------------------------------------|-------------------|
| url_matches     | `pattern` (regex, str)                    | page URL          |
| element_visible | `target` (TargetVector)                   | DOM               |
| text_matches    | `target` (TargetVector), `pattern` (str)  | DOM               |
| http_status     | `url_template` (str), `status` (int)      | network (Tier 1)  |
| row_count       | `target` (TargetVector), `op` in {eq,ge,le}, `value` (int) | DOM |
| value_equals    | `target` (TargetVector), `value` (str)    | DOM               |
| file_exists     | `path_template` (str)                     | filesystem        |

`args` is validated per-kind: missing or extraneous keys are schema
errors. Per invariant 11, prefer `http_status` (system-of-record
evidence) over `element_visible`/`text_matches` (the interface's
opinion) whenever Tier 1 evidence makes one reachable; the inductor
must flag UI-only postconditions as `weak`.

### 2.2 TargetVector (identity vector, invariant 4)

```
TargetVector = {
  role:      str | null,     # ARIA role
  name:      str | null,     # accessible name
  testid:    str | null,     # data-testid
  attrs:     {str: str},     # stable attributes (id, name, type, ...)
  css:       str | null,     # CSS path
  xpath:     str | null,     # XPath
  frame_url: str | null,     # owning frame URL (templatized)
  bbox:      [x, y, w, h] | null
}
```

Validation: at least one of `role+name`, `testid`, `attrs`, `css`,
`xpath` must be populated. A vector that is empty in all locator
dimensions is a validation error.

### 2.3 Action

```
Action = {
  type:            str,           # from the action-type catalog
  target_vector:   TargetVector | null,   # null only for target-less types
  params:          {str: JSON},   # type-specific parameters
  tier_preference: [int]          # ordered, subset of {1,2,3,4}
}
```

Action-type catalog (Phase 0, closed set): `navigate`, `click`,
`fill`, `select`, `press`, `upload`, `download`, `http_call`,
`extract`, `assert_only`. `navigate` and `http_call` are the only
target-less types; all others require a `target_vector`.

### 2.4 FaultPolicy

```
OnFault = {
  policy:      "retry" | "escalate" | "rollback" | "abort",
  max_retries: int >= 0,          # required, > 0 only when policy=retry
  backoff_ms:  int >= 0,
  transfer_to: str | null         # step id, required when policy=rollback
}
```

### 2.5 Step

```
Step = {
  id:                str (unique in process),
  label:             str (non-empty, human-readable),
  preconditions:     [Predicate],        # MAY be empty
  action:            Action,
  postconditions:    [Predicate],        # MUST be non-empty  (§1.1)
  postcondition_strength: "strong" | "weak" | null,
  timeout_ms:        int, 0 < timeout_ms <= 86_400_000   (§1.2)
  on_fault:          OnFault,
  idempotency:       "safe" | "unsafe" | "compensable",
  risk:              "read" | "write" | "irreversible",
  approval_required: bool,               # forced true if irreversible
  provenance:        [event_id: str],
  confidence:        float in [0, 1]
}
```

### 2.6 Graph

```
Edge = { from_step: str, to_step: str, guard: Predicate | null,
         guard_question: str | null }

IRGraph = {
  entry:  str,                 # step id
  steps:  [Step],              # ids unique
  edges:  [Edge]               # endpoints must reference existing steps
}
```

Graph validation: `entry` exists; all edge endpoints exist; every step
reachable from `entry`; a step with two or more outgoing edges must
have a `guard` or a `guard_question` on each of them (an unguarded
branch is a decision the operator never made). `guard_question` is the
inductor's explicit representation of uncertainty (invariant 6): a
conditional region whose guard is unknown ships as a question, never
as a guessed predicate.

### 2.7 Process envelope

```
Parameter = { name: str, value_class: str, example_redacted: str | null }

ProcessEnvelope = {
  process_id:       str,
  name:             str,
  version:          int >= 1,
  parent_version:   int | null,          # version lineage
  ir_schema_version: str,
  parameter_signature: [Parameter],
  lifecycle_state:  "active" | "degraded" | "quarantined" | "retired",
  review_state:     "draft" | "reviewed" | "approved",
  coverage_estimate: { runs: int, distinct_variants: int,
                       singleton_variants: int, unseen_mass: float } | null,
  graph:            IRGraph,
  history:          [HistoryEntry]       # append-only, attributed
}

HistoryEntry = { at: iso8601, actor: str, event: str, detail: {str: JSON} }
```

Lifecycle transitions are fault-code-bound (invariant 13); the
transition table lives with the runner (Phase 1+), but the states and
the `history` audit trail are schema now so Phase 0 artifacts carry
them from birth. Promotion past `draft` is refused while
`coverage_estimate.unseen_mass` exceeds the configured threshold
(default 0.10) — enforced in `store/`, mirrored by an envelope
validator so a hand-edited document cannot claim `reviewed` with
insufficient coverage evidence.

## 3. Serialization

- Canonical form: JSON, UTF-8, sorted keys, one document per process
  version. NDJSON is used only for event traces, not IR.
- `IR_SCHEMA_VERSION` is embedded in every document
  (`ir_schema_version`); loaders refuse documents from a newer schema.

## 4. Validation failure policy

All violations listed in this specification raise
`pydantic.ValidationError` at construction/parse time. There are no
warning-level IR checks. Callers (the inductor, the store) must treat
a validation failure as reject-and-retry (invariant 6), never
fix-up-and-continue.
