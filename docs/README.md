# PENDANT documentation

<p align="center">
  <img src="assets/logo.png" alt="PENDANT logo" width="200">
</p>

PENDANT is a programming-by-demonstration system for browser processes. This folder holds the Phase 0 specifications, gate reports, and living risk/decision logs. The product overview and install steps live in the root [README](../README.md). The binding governing document is [CLAUDE.md](../CLAUDE.md).

## Start here

| If you need… | Read |
|--------------|------|
| What PENDANT is and how to run it | [../README.md](../README.md) |
| Non-negotiable architecture & gates | [../CLAUDE.md](../CLAUDE.md) |
| The IR reliability contract | [IR.md](IR.md) |
| How capture / redaction works | [CAPTURE.md](CAPTURE.md) |
| Why a given design choice was made | [DECISIONS.md](DECISIONS.md) |
| Known failure modes and controls | [FMEA.md](FMEA.md) |
| Pilot process selection & time study | [BASELINE.md](BASELINE.md) |

## Phase gates

Numeric criteria are defined in CLAUDE.md Part IV. Reports are committed when a gate is run—not assumed.

| Gate | Document | Status |
|------|----------|--------|
| 0 — Recorder fidelity | [GATE0_REPORT.md](GATE0_REPORT.md) | PASS |
| 1 — Aligner (seeded defects) | [GATE1_REPORT.md](GATE1_REPORT.md) | PASS |
| 2 — Inductor on real pilot | *(to be written after baseline + demos)* | Pending |
| 3 — Pilot cutover (shadow) | Phase 2 | Not started |

## Specs

### [IR.md](IR.md)

Normative Intermediate Representation. Every step must carry a non-empty postconditions list and a finite `timeout_ms`—schema failures, not warnings. Locators are identity vectors. `risk=irreversible` forces `approval_required`.

### [CAPTURE.md](CAPTURE.md)

CDP recorder: five channels (network, structure, events, pixels, narration), in-collector redaction (invariant 3), Gate 0 harness (`pendant capture-msa`), and disclosed Phase 0 limitations.

### [BASELINE.md](BASELINE.md)

Before Gate 2 or any compile work: candidate Pareto (frequency × cycle time × error cost × stability), ECRS pass on the winner, then an n ≥ 10 manual cycle-time study. Primary metric: net minutes recovered per week.

## Living logs

### [DECISIONS.md](DECISIONS.md)

Append-only log for architectural choices not fully fixed by CLAUDE.md (CLI stack, aligner scores, narration as typed notes, dual-build Gate 0 leg, induce output schema, …). Reversals get a new entry.

### [FMEA.md](FMEA.md)

Seeded failure modes: redaction escape (S10), silent success / wrong outcome (top RPN), conditional-as-invariant, reviewer rubber-stamping, selector rot, premature desktop scope, and related rows with controls.

## Assets

| File | Use |
|------|-----|
| [assets/logo.png](assets/logo.png) | Project mark (industrial teaching pendant) |

## Reading order for contributors

1. README → CLAUDE.md (invariants + build order)  
2. IR.md → implement or extend `pendant/ir/`  
3. GATE1 then GATE0 reports (ordering inversion: aligner before trusting the recorder)  
4. CAPTURE.md / DECISIONS.md when touching capture or cross-module contracts  
5. BASELINE.md before inducing a real pilot
