# Gate 2 Rehearsal — Closed-Loop Pipeline Mechanics (NOT Gate 2)

Date: 2026-08-08. Status: **loop mechanics VALIDATED end to end.**

This is a rehearsal of the pipeline's mechanics, not Gate 2. Gate 2 is
defined only on the real pilot process with a real model
(invariant 14, docs/BASELINE.md), and no gate criterion is claimed
here. The "model" in this rehearsal was the interactive assistant
authoring the induction response through the `--model file` exchange
(D-016), so the induction-quality numbers below measure the loop, not
a production model.

## What ran

Full closed loop on the Gate 0 reference process, no API key, one
machine:

1. **Capture** — 3 scripted demonstrations (headless Chromium, real
   `Collector`, redaction registry active) with varied customer /
   quantity / SKUs / addresses / discount code, and run 3 skipping the
   discount branch entirely. 52/49/44 events stored into a fresh
   store. Seeded-canary scan over the store: **zero escapes**.
2. **Coverage** — Good-Turing reported 67% (2 variants, unseen mass
   33%): correctly refuses promotion at 3 demos.
3. **Align** — 48 columns: 34 invariant, 9 parameterized (every varied
   field plus the order-detail URL), 5 conditional (exactly the
   discount region, present 2/3). Zero misclassifications against
   intent.
4. **Induce** (`--model file`, D-016 exchange; response authored by
   the assistant): schema-valid **first pass**; 21 steps;
   postcondition proposal rate **1.00**; **1 question for 1
   conditional region**, referencing exactly the conditional columns;
   D-018 placeholders used throughout (`{customer}`, `{quantity}`,
   ...) with all 10 parameters declared — cross-validation
   (provenance, placeholders, question discipline) passed.
5. **Review** — `pendant show` rendered the draft with live coverage
   overlay; envelope saved as `ref-order` v1 (draft).
6. **Run** — `pendant run --allow-draft --headless` against a fresh
   server instance (new ephemeral port bound via `--param base_url`):
   **21/21 steps ok, first attempt each**. Exercised: placeholder
   binding from `--param`; secret prompts for password and card
   number (fill-without-value, registry-classified); the branch
   question surfaced at the conditional region (operator chose the
   discount path); approval stop at the `risk=write` create-order
   step; method-pinned `http_status` postconditions verified against
   the actual POST/GET evidence windows.
7. **Hygiene** — the password and card values typed during the run
   appear nowhere in the run report or anywhere in the store
   (post-run scan clean).

## What this proves / does not prove

Proven: every schema boundary and CLI seam in
capture → store → align → induce → review → run is compatible in
practice; the D-018 placeholder convention round-trips from induction
to execution; runner invariants (approval, branch questions, secret
handling, evidence windows) behave as specified on a real browser.

Not proven: induction quality of any real LLM (Gate 2 proper), pilot
realism, capture fidelity on any machine other than this one. Next:
re-run induction with a real provider (`--model claude-sonnet-4-5` or
any D-016 spec) on this same stored corpus and compare against this
authored baseline, then Gate 2 on the real pilot.
