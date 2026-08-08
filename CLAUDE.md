# PENDANT — Phase 0 Bootstrap and Governing Document

## Mission

PENDANT is a programming-by-demonstration system for capturing repetitive
computer processes and compiling them into maintainable, instrumented
automations. The governing analogy is robot lead-through teaching: a
demonstrated trajectory is the least valuable part of a taught program.
The value lives in the frames, guard conditions, timeouts, and fault
routines that let the program survive variance the demonstration never
showed. PENDANT is therefore optimized for extracting structure from
multiple demonstrations and eliciting decision logic from the operator,
not for replaying a single recording.

Phase 0 scope: browser-only capture through validated IR generation.
We do NOT build a compiler or runner in this phase. We stop at a
printable, schema-valid IR graph, produced from real multi-run evidence,
and we do not proceed past any gate defined below without its numeric
criterion being met and shown to me.

## Part I — Architectural invariants (binding, never relax under schedule pressure)

1. Evidence is immutable. Traces are append-only, content-addressed,
   and never mutated by downstream stages. Every module boundary is a
   serializable schema so any stage can be re-run against archived
   inputs from the stage before it.
2. Sequence alignment is DETERMINISTIC. Multiple sequence alignment via
   progressive pairwise Needleman-Wunsch with affine gap penalties.
   Never ask an LLM to align traces. The model receives an
   AlignmentReport as evidence and reasons over it.
3. Redaction executes inside the collector, before any write to disk.
   Never post-hoc. Password-type inputs, Authorization and Cookie
   headers, a configurable PII field registry, and an operator hotkey
   that blanks the last N seconds. Screenshots pass through masking
   driven by the same registry. A trace corpus containing a live
   credential cannot be un-leaked.
4. Locators are captured as identity VECTORS (ARIA role, accessible
   name, data-testid, stable attributes, CSS path, XPath, frame URL,
   bounding box), never a single selector. Drift detection and compiler
   tier selection both depend on the full vector.
5. Every IR step MUST carry a non-empty postconditions list and a
   finite timeout_ms. Both are schema-level validation failures, not
   warnings. A step without verification is a pick with no part-present
   sensor; an unbounded wait produces a hang, and a hang is worse than
   a fault because it does not alarm.
6. LLM output is schema-constrained and validated before persistence.
   Uncertainty is represented explicitly as a clarifying question for
   the operator, never as a plausible guess. Reject and retry on
   validation failure.
7. The recorder is a measurement system and must be qualified before
   the aligner is trusted. See Gate 0. Until the capture fidelity study
   passes, alignment accuracy numbers are unfalsifiable because a
   classification error cannot be distinguished from a capture artifact.
8. The fixture corpus is a seeded defect library, not a smoke test.
   See Gate 1. Asymmetric misclassification costs demand asymmetric
   acceptance criteria.
9. Demonstration sufficiency is computed, not assumed. Implement a
   Good-Turing coverage estimator over observed variants (unseen
   variant mass approximated by the fraction of variants observed
   exactly once). The store refuses to promote a process past `draft`
   while estimated unseen mass exceeds a configurable threshold
   (default 10%). The review surface displays estimated coverage,
   never raw run count.
10. The inductor is instrumented from day one: schema first-pass
    yield, operator corrections per 10 IR nodes, postcondition
    proposal rate, and the fraction of conditional regions producing a
    well-formed question. These metrics feed Gate 2.
11. Postconditions assert against the system of record wherever Tier 1
    network evidence makes one reachable. A UI toast is the
    interface's opinion of success, not evidence of it.
12. Review poka-yoke: nodes below the confidence threshold, and nodes
    whose postcondition the model flagged as weak, cannot be approved
    until their evidence replay has been opened. Enforce this in the
    API, not the client.
13. Processes carry a lifecycle state machine (active, degraded,
    quarantined, retired) in the IR envelope, with fault-code-bound
    transitions. Two consecutive POST-FAIL results quarantine an
    automation to step-through mode rather than disabling it. The
    runner enforces state; the operator does not remember it.
14. Baselines precede improvement. A manual cycle-time study
    (n >= 10) for the pilot process lives in docs/BASELINE.md before
    any automation of it is compiled. The primary program metric is
    NET minutes recovered per week, inclusive of supervision, triage,
    and maintenance time. The guardrail metric is undetected defect
    escapes, target zero, audited by sampling.
15. Irreversible-risk steps automatically inherit approval_required
    and cannot have it cleared without an explicit, attributed
    override recorded in the process history.

## Part II — System architecture

Data flows strictly left to right. Each arrow is a schema.

    capture -> store -> align -> induce -> ir -> review
    (Phase 1+: -> compile -> run -> reliability)

### capture/  (Phase 0)

CDP-based recorder using Playwright's Python API with a raw CDP
session. Subscribe to Network, DOM, Runtime, Page domains. Inject a
content script reporting user-initiated events with the full identity
vector. Four concurrent channels into one time-correlated stream:

- Network (Tier 1): method, templatized URL, request/response body
  hashes, status. Templatize at capture time:
  /api/orders/48219 -> /api/orders/{p0}, literal preserved separately.
- Structure (Tier 2): identity vector per invariant 4, snapshotted at
  every user-initiated event.
- Events (Tier 3): clicks, keystrokes, navigation, focus, clipboard,
  dialogs, downloads. Monotonic and wall-clock timestamps on every
  record.
- Pixels (Tier 4): screenshot per event, masked per invariant 3,
  retained as reviewer evidence and terminal-fallback locator.
- Narration (Tier 5): push-to-talk audio, transcribed at session
  close, anchored to nearest events. Narration is the only channel
  carrying intent.

Output: NDJSON, one RunTrace per demonstration.

    Event = {
      event_id, run_id, seq, t_mono_ms, t_wall,
      tier: 1|2|3|4,
      kind: navigate|click|input|key|network|focus|clipboard|dialog|
            download|narration,
      target: { role, name, testid, attrs{}, css, xpath, frame_url, bbox },
      payload: { value_redacted, value_class, keys[] },
      network: { method, url_template, url_params{}, status,
                 req_sha, resp_sha },
      screenshot_ref, redactions[]
    }

### store/  (Phase 0)

SQLite for metadata and indices, content-addressed blob directory for
screenshots and bodies. Process -> Runs -> Events, append-only,
versioned, migrations from day one. Enforces the Good-Turing promotion
gate from invariant 9.

### align/  (Phase 0)

Deterministic, zero network dependencies, fully unit-testable.

1. Canonicalize each event to a comparison token:
   (kind, role, normalized_name, url_template), with a configurable
   normalizer ruleset stripping volatile substrings, timestamps, and
   generated identifiers.
2. Progressive pairwise MSA (Needleman-Wunsch, affine gaps) collapsing
   N runs into an aligned column matrix.
3. Classify each column:
   - present in all runs, identical payload  -> invariant step
   - present in all runs, payload varies     -> parameterized step
   - present in a subset                     -> conditional region,
                                                guard unknown
   - present in all, ordered differently     -> unordered region

Output: AlignmentReport with column matrix, classifications, and
per-column provenance to source event ids.

### induce/  (Phase 0)

Input: AlignmentReport + narration transcript + IR JSON schema +
action-type catalog. Required output, schema-validated before
persistence:

- draft IR graph;
- one proposed postcondition per step, or an explicit null WITH a
  stated reason (never a placeholder);
- one natural-language clarifying question per conditional region;
- confidence score and provenance references per node.

Log the instrumentation metrics from invariant 10 on every invocation.

### ir/  (Phase 0, built FIRST)

Pydantic v2 models. The reliability contract of the entire system.

    Step = {
      id, label,
      preconditions: [Predicate],          # may be empty
      action: { type, target_vector, params, tier_preference[] },
      postconditions: [Predicate],         # MUST be non-empty (validator)
      timeout_ms,                          # MUST be finite (validator)
      on_fault: { policy: retry|escalate|rollback|abort,
                  max_retries, backoff_ms, transfer_to },
      idempotency: safe|unsafe|compensable,
      risk: read|write|irreversible,
      approval_required: bool,             # forced true if irreversible
      provenance: [event_id], confidence
    }

    Predicate = { kind: url_matches|element_visible|text_matches|
                        http_status|row_count|value_equals|file_exists,
                  args{}, negate }

Process envelope carries: parameter signature, lifecycle state
(invariant 13), review state (draft -> reviewed -> approved), coverage
estimate, and version lineage.

### Deferred modules (Phase 1+, do not build in Phase 0)

- review/: FastAPI + minimal web client rendering the IR as a graph,
  with evidence replay, question answering, parameter naming,
  postcondition editing, and the poka-yoke from invariant 12.
- compile/: tier-ladder target selection (direct HTTP where Tier 1
  evidence is complete and safely reproducible, then role/name
  locators, then attribute locators, then CSS/XPath, then visual
  fallback), emitting readable, version-controllable Playwright
  Python with explicit assertions per postcondition. Compiler refuses
  any process below `approved`.
- run/: dry-run, step-through, and auto modes; runtime postcondition
  enforcement; fault capture (screenshot, DOM snapshot, network tail,
  structured fault record); resume-from-step driven by idempotency.
- reliability/: fleet metrics (run count, first-pass yield, MTBF,
  MTTR, availability, OEE decomposition: availability x performance x
  quality); p-chart on failure proportion with Western Electric
  rules; EWMA on locator-vector disagreement, alarming on trend
  before functional failure; fault taxonomy (LOC-TIMEOUT, POST-FAIL,
  AUTH-EXPIRED, SHAPE-DRIFT, RATE-LIMIT) with reaction plans bound to
  lifecycle transitions; CMMS-format export; partial re-capture
  triggers scoped to the drifted region only.

## Part III — Repository shape

    pendant/
      capture/     collectors, redaction registry, CDP adapters
      store/       schema, migrations, blob store, coverage estimator
      align/       normalizer, MSA, column classifier
      induce/      prompt assembly, schema-constrained calls, validators,
                   instrumentation
      ir/          pydantic models, graph ops, invariant validators
      cli.py
    tests/
      fixtures/traces/     seeded defect library (see Gate 1)
      reference_process/   deterministic script for Gate 0
    docs/
      IR.md  CAPTURE.md  BASELINE.md  DECISIONS.md  FMEA.md
    CLAUDE.md              this document

## Part IV — Phase gates (numeric, non-negotiable, shown to me before proceeding)

GATE 0 — Recorder qualified (capture fidelity study).
Build a deterministic Playwright reference script (~30 known steps,
known parameters) and a `pendant capture-msa` CLI command that replays
it N times and reports: event capture fidelity vs. known ground truth,
drop rate, duplicate rate, redaction escapes; then across a second
machine or browser version for reproducibility.
PASS: fidelity >= 99.5%, zero redaction escapes, reproducible token
sequences. One redaction escape is a stop-the-line defect, severity
10, no waiver.

GATE 1 — Aligner qualified (seeded defect library).
15 to 20 hand-authored synthetic RunTrace fixtures covering: one
varying parameter; a conditional present in a subset of runs; repeated
identical steps (alignment ambiguity); unordered regions; a variant
appearing in exactly one run; volatile identifiers that defeat naive
canonicalization; simulated event drops; near-duplicate elements
differing only in frame context. Score with a confusion matrix.
PASS: >= 95% column classification accuracy overall AND zero
conditional regions misclassified as invariant. The second criterion
is absolute; the two error types do not trade against each other.

GATE 2 — Inductor worth building around (real pilot process).
Select the pilot via a candidate Pareto (frequency x cycle time x
error cost x application stability), apply ECRS (eliminate, combine,
rearrange, simplify) before automate, and record the manual baseline
per invariant 14. Then capture demonstrations until the Good-Turing
estimate clears threshold, align, induce.
PASS: <= 3 operator corrections per 10 IR nodes, >= 90% of steps
arriving with a proposed postcondition, every conditional region
arriving with a coherent clarifying question. If this gate fails,
iterate on prompt and evidence format at the induce/ layer; do not
proceed to any compiler work.

GATE 3 — Pilot cutover (Phase 2, recorded here for continuity).
Shadow mode: compiled automation runs alongside continued manual
execution for a paired sample of >= 10 runs; compare outcomes in the
target system, not exit codes.
PASS: zero outcome discrepancies, automation first-pass yield >= 90%,
net time recovered positive inclusive of supervision.

## Part V — Build order (strict)

1. Write CLAUDE.md as a copy of this document.
2. Write docs/IR.md as a formal specification BEFORE implementing,
   including the rationale for mandatory postconditions and finite
   timeouts. Write docs/FMEA.md seeded with: redaction escape
   (S10), silent success / postcondition passes while outcome wrong
   (top RPN), conditional-as-invariant misclassification, reviewer
   rubber-stamping, selector rot, premature desktop scope.
3. Implement pendant/ir/ with full validator coverage. Tests must
   prove an empty-postconditions step and a null timeout both raise,
   and that risk=irreversible forces approval_required.
4. Author the seeded defect library fixtures (Gate 1 corpus).
5. Implement pendant/align/ and pass Gate 1 against the fixtures.
   Show me the confusion matrix.
6. Implement pendant/capture/ with the redaction registry, then the
   reference process and `capture-msa`, and pass Gate 0. Show me the
   fidelity report.
7. Implement pendant/store/ including the Good-Turing promotion gate.
8. Implement pendant/induce/ with instrumentation, run it on the real
   pilot corpus, and present Gate 2 results.
9. Implement pendant/cli.py incrementally alongside the above:
   record, runs, align, induce, show, capture-msa, coverage.

Note the ordering inversion at steps 5 and 6: the aligner is proven
against synthetic fixtures before the recorder exists, and the
recorder is then qualified against a scripted ground truth before its
output is ever fed to the aligner. Neither component is permitted to
vouch for the other.

## Part VI — Conventions

- Python 3.11+, uv for dependency management, ruff, mypy strict.
- Pydantic v2 for every cross-module contract.
- pytest; the aligner is pure and tested directly, never mocked.
- Readable emitted artifacts everywhere; no opaque runtimes. If the
  tool disappears, its artifacts still run and still read.
- Ask before making any architectural decision not specified here.
  Record every such decision in docs/DECISIONS.md with rationale and
  the alternatives rejected.
- Do not begin desktop (UIA / macOS Accessibility) capture, the
  compiler, the runner, or the review web client in Phase 0 under any
  circumstances. Scope discipline is itself a gate criterion.

## Part VII — Definitions of done for Phase 0

Phase 0 is complete when: Gates 0, 1, and 2 have each passed with
their reports committed to docs/; the pilot process has a manual
baseline in docs/BASELINE.md; a schema-valid IR graph for the pilot
process exists in the store at review state `draft` with its coverage
estimate displayed; and docs/DECISIONS.md and docs/FMEA.md reflect
every deviation and every identified risk. Nothing else.
