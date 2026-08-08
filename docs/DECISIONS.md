# PENDANT Decisions Log

Format: one entry per architectural decision not fully specified by
`CLAUDE.md`. Each records the decision, rationale, and alternatives
rejected. Entries are append-only; a reversed decision gets a new
entry superseding the old one.

---

## D-001 — Python 3.12 via uv-managed toolchain

**Decision.** Pin CPython 3.12 (`.python-version`), managed by uv.

**Rationale.** CLAUDE.md requires 3.11+; 3.12 is the newest version
with mature wheel coverage for pydantic-core and Playwright on this
machine, and uv provisions it hermetically so the system Python (3.9)
is never involved.

**Rejected.** 3.11 (no advantage over 3.12); 3.13 (less soak time
with the pinned dependency set).

## D-002 — CLI built on argparse (stdlib)

**Decision.** `pendant/cli.py` uses `argparse` subcommands, no CLI
framework dependency.

**Rationale.** Seven flat subcommands (`record`, `runs`, `align`,
`induce`, `show`, `capture-msa`, `coverage`) need no framework;
stdlib keeps the dependency set minimal and the artifact readable per
Part VI ("no opaque runtimes").

**Rejected.** typer/click (extra dependency for no structural gain at
this command count).

## D-003 — Gate 1 fixtures: committed NDJSON, authored via a builder script

**Decision.** The seeded defect library is committed as literal
NDJSON files under `tests/fixtures/traces/`, one directory per
scenario with a `manifest.json` carrying the hand-written ground-truth
labels. The NDJSON is *authored* by a small deterministic builder
script (`tests/fixtures/build_fixtures.py`) that is also committed;
the committed NDJSON is the artifact of record and the aligner is
tested against the files, not the builder.

**Rationale.** "Hand-authored" is preserved in the sense that every
event, variation, and defect is individually and intentionally
specified in the builder; generating the final NDJSON mechanically
eliminates hand-transcription errors in 500+ event lines while keeping
the corpus diffable, reviewable, and regenerable. Ground-truth labels
live in manifests written directly by hand.

**Rejected.** Hand-typing raw NDJSON (error-prone at this volume; a
typo in a fixture would corrupt the gate itself); generating fixtures
at test runtime (corpus must be a stable, reviewable artifact, not a
moving target).

## D-004 — Gate 0 reproducibility leg: second browser build on one machine

**Decision.** The reproducibility arm of Gate 0 runs the reference
process on a second browser build — Playwright's headed Chromium
versus the separately-shipped `chromium-headless-shell` binary (and,
when available, a second installed Chromium revision) — on this
machine. Token-sequence reproducibility is compared across the two
builds. A genuinely distinct second machine remains open; the fidelity
report marks this leg "single-machine, dual-build" until one is
provided.

**Rationale.** Only one machine is available in this environment.
Two distinct browser builds exercise the realistic sources of capture
variance (rendering, event timing, CDP behavior differences) that the
reproducibility criterion targets. The limitation is disclosed on the
report rather than silently narrowed.

**Rejected.** Skipping the reproducibility leg (weakens Gate 0);
pretending headless/headed of the same binary counts as two machines
without disclosure.

## D-005 — IR details not fixed by CLAUDE.md

**Decision.** As specified in `docs/IR.md`: per-kind Predicate `args`
validation tables; closed Phase 0 action-type catalog (`navigate`,
`click`, `fill`, `select`, `press`, `upload`, `download`, `http_call`,
`extract`, `assert_only`); `timeout_ms` bounded to (0, 86_400_000];
`extra="forbid"` on all models; edges carry `guard | guard_question`
and multi-out-edge steps require one of them; TargetVector must have
at least one populated locator dimension; explicit
`approval_required=false` on an irreversible step is a hard error
rather than a silent coercion.

**Rationale.** Each closes an ambiguity in the Step/Predicate sketch
in a direction that makes invalid states unrepresentable, per the
spirit of invariants 4, 5, 6, 15.

**Rejected.** Open string action types (unverifiable catalog); warning-
level checks (Part I preamble forbids); silent coercion of the
approval flag (hides a contradiction the operator should see).

## D-006 — Store location and layout

**Decision.** Default store root is `./pendant_data/` relative to the
working directory, overridable with `--store` / `PENDANT_STORE`.
Layout: `pendant.db` (SQLite, WAL mode) plus `blobs/sha256/ab/cd/<hex>`
content-addressed directory. Migrations are sequential numbered SQL
scripts applied by version stamp in a `schema_migrations` table.

**Rationale.** Project-local keeps evidence next to the process being
studied and makes the store trivially archivable; sharded blob dirs
avoid large flat directories; numbered SQL migrations are readable
artifacts per Part VI.

**Rejected.** `~/.pendant` global store (couples unrelated corpora);
ORM/migration framework (opaque, unnecessary dependency).

## D-007 — Aligner scoring parameters

**Decision.** Needleman-Wunsch (Gotoh) with affine gaps: match +4,
gap open -6, gap extend -1, and NO cross-token substitution — two
columns merge only when their canonical tokens are identical, so
every aligned column carries exactly one token. Guide order for
progressive MSA: center-star seeded by the most-similar pair (LCS
ratio), remaining runs joined by descending mean similarity to the
included set; all ties break on run id; traceback preference is fixed
(match > gap-in-B > gap-in-A). Parameters live in one `AlignParams`
dataclass; Gate 1 must pass with the committed defaults.

**Rationale.** CLAUDE.md fixes the algorithm family but not the
scores. Prohibiting cross-token merges keeps the column model clean
(a column is one step, with per-run presence and payloads) — a mixed
column has no coherent classification under the four-class scheme.
Cheap gap extension tolerates simulated event drops; deterministic
tie-breaks make the aligner a pure function of its input
(invariant 2). Scores are data, not architecture; tuning is permitted
so long as Gate 1 is re-run.

**Rejected.** Mismatch scores with partial credit for same-kind
tokens (produces mixed-token columns that defeat classification);
learned or per-corpus adaptive scores (violates determinism in
spirit); tree-guided MSA with full distance matrix + UPGMA (more
machinery than N<=10 runs warrants; can be revisited).

## D-008 — Unordered-region detection method

**Decision.** After MSA, a maximal group of adjacent columns is
classified `unordered` when every run contains exactly the same
multiset of tokens within the group's span but at least two runs
disagree on their order. Detection operates on a windowed permutation
test over the aligned matrix, bounded to windows of <= 8 columns.

**Rationale.** CLAUDE.md names the class but not the detector. The
multiset-equality definition is the direct formalization of "present
in all, ordered differently", and the window bound keeps the check
linear in practice.

**Rejected.** Full partial-order mining (Phase 0 does not need it);
treating order variance as conditional regions (loses the distinction
the classifier is required to make).

## D-009 — LLM provider for induce/

**Decision.** `induce/` is built against a minimal `LLMProvider`
protocol (prompt in, text out). Phase 0 ships two implementations: an
Anthropic Messages API client (stdlib `urllib`, key from
`ANTHROPIC_API_KEY`) and a deterministic `ReplayProvider` used by unit
tests. All induce logic — prompt assembly, schema validation,
reject-and-retry, instrumentation — is provider-independent and tested
against the deterministic provider; Gate 2 runs against the real one.

**Rationale.** Invariant 6 and Gate 2 concern the *validation and
instrumentation envelope* around the model, which must be testable
without network. The stdlib client avoids a heavyweight SDK dependency
for one endpoint.

**Rejected.** Anthropic/OpenAI SDK dependency (unneeded surface);
mocking the provider inside induce logic tests via patching (a real
injected implementation is cleaner and matches the pure-function
testing convention).

## D-010 — Reference process served from a local deterministic web app

**Decision.** The Gate 0 reference process is a self-contained static
HTML/JS application (`tests/reference_process/app/`) served by a local
`http.server` on an ephemeral port, plus a JSON-scripted driver of ~30
steps with known parameters, including a seeded fake credential typed
into a password field to exercise the redaction escape counter.

**Rationale.** Ground truth requires a page whose structure and
network behavior are fully known and version-pinned; any external site
would make Gate 0 non-reproducible. The seeded credential makes the
zero-redaction-escapes criterion falsifiable rather than vacuous.

**Rejected.** Public demo sites (non-deterministic, network-dependent);
Playwright-served `route()` fakes only (less realistic Tier 1 traffic).

## D-011 — Event.tier admits 5 for narration

**Decision.** The Event schema allows `tier: 1|2|3|4|5`, with
narration events required to carry tier 5.

**Rationale.** CLAUDE.md's Event sketch lists `tier: 1|2|3|4` while
Part II defines narration as Tier 5 and includes `narration` in the
kind enum. The two cannot both hold; we resolve toward the channel
definition because narration provably needs a tier and 5 is the one
named for it.

**Rejected.** Folding narration into tier 3 (misrepresents the
channel; narration is the only intent-carrying channel and downstream
consumers filter on tier).

## D-012 — Narration is typed notes in Phase 0, not push-to-talk audio

**Decision.** `pendant record` accepts operator-typed notes on stdin
during the session; each line becomes a tier-5 narration event
anchored at entry time. Push-to-talk audio capture with
session-close transcription is deferred until a transcription
backend is chosen with the operator.

**Rationale.** Audio capture plus STT requires microphone plumbing and
an external transcription dependency that CLAUDE.md does not name.
Typed notes preserve the architectural role of the channel (the only
carrier of intent, anchored to the event stream) so induce/ consumes
the same schema either way; swapping in audio later changes only the
input device, not the trace format.

**Rejected.** Shipping an STT dependency unasked (architectural
decision reserved to the operator); omitting narration entirely
(induce/ would lose its only intent evidence).

## D-013 — Inductor output schema is distinct from the IR

**Decision.** `induce/` emits an `InducedProcess`
(`pendant/induce/schema.py`): steps carry postcondition *proposals* —
a predicate rated strong/weak, or an explicit null with a substantive
stated reason — plus one clarifying question per conditional region.
Conversion to a schema-valid `ProcessEnvelope` (`to_envelope`) refuses
while any step lacks a resolved postcondition.

**Rationale.** Invariant 5 makes empty postconditions unrepresentable
in the IR, while invariant 6 and Part II require the inductor to say
"no postcondition, because ..." rather than fabricate one. Those two
requirements cannot live in one schema; the induced form is the
explicit intermediate where honesty is legal, and the IR remains the
place where it is not.

**Rejected.** Placeholder postconditions to satisfy the IR validator
(exactly the "plausible guess" invariant 6 forbids); relaxing the IR
validator for drafts (erodes the reliability contract everywhere).
