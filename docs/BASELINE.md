# PENDANT Manual Baseline — Pilot Process

Status: **NOT STARTED — blocks Gate 2 and all compilation work.**

Per invariant 14, this document must contain a manual cycle-time study
(n >= 10) for the pilot process before any automation of it is
compiled. Nothing may cite this document as satisfied until the table
below is populated with real observations.

## Pilot selection (prerequisite)

To be completed with the operator via candidate Pareto.

Eligibility (all four required, CLAUDE.md Part VII):

1. Personal, non-Tesla browser process.
2. 10–40 steps per run.
3. Run weekly or better.
4. At least one varying input AND at least one branch (a decision the
   operator makes mid-process based on what the page shows).

Brainstorm prompts — recurring browser chores that commonly qualify
(walk the list and note anything done in the last month):

- Bill / invoice / statement retrieval and filing (utilities, cards,
  SaaS receipts).
- Recurring form submissions (timesheets, expense reports,
  reimbursements, appointment bookings).
- Weekly report or data pulls (dashboard export, CSV download,
  copy-into-spreadsheet).
- Order, shipment, or application status tracking with a follow-up
  action when a state changes.
- Cross-app data entry (copy fields from one web app into another).
- Account reconciliation or verification sweeps (comparing two lists,
  marking discrepancies).
- Listing management (marketplace postings, renewals, price updates).

Scoring instructions: for each candidate fill one row below.
Frequency and cycle time from memory are fine at this stage (the
n >= 10 study below is what makes the baseline real). Error cost and
app stability on a 1–5 scale (5 = costly mistakes / very stable DOM).
Pareto score = frequency x cycle time, then break ties toward higher
error cost and higher stability. Disqualify candidates that fail any
eligibility criterion regardless of score.

| Candidate process | Frequency (runs/wk) | Cycle time (min) | Error cost (1-5) | App stability (1-5) | Eligible? | Pareto score |
|-------------------|---------------------|------------------|------------------|---------------------|-----------|--------------|
| _(pending)_       |                     |                  |                  |                     |           |              |

ECRS pass (eliminate, combine, rearrange, simplify) to be recorded
here for the selected candidate **before** demonstrations are captured.

## Cycle-time study (n >= 10)

| Run | Date | Operator | Start | End | Cycle time (min) | Interruptions / notes |
|-----|------|----------|-------|-----|------------------|-----------------------|
| 1   |      |          |       |     |                  |                       |
| 2   |      |          |       |     |                  |                       |
| ... |      |          |       |     |                  |                       |

Summary statistics (mean, median, min, max, std dev): _(pending)_

## Program metrics baseline

- Primary: NET minutes recovered per week = (manual minutes avoided)
  − (supervision + triage + maintenance minutes). Baseline manual
  minutes/week: _(pending)_.
- Guardrail: undetected defect escapes, target zero, audited by
  sampling. Sampling plan: _(pending)_.
