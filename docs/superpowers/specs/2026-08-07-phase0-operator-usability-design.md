# Phase 0 operator usability — catalog + record-session HUD

Date: 2026-08-07  
Status: draft for review  
Approach: catalog for Gate 2 awareness + thin polish and guided terminal HUD on `pendant record` only (no new commands, no web UI).

## Goal

Reduce mid-demo cognitive load and silent failure modes for a **technical** operator preparing Gate 2, without inventing a Phase 1 review client or changing the CLI command set.

## Non-goals

- Web review UI, compile, run
- New subcommands (e.g. `pendant demo` wizard chaining align/induce)
- Audio narration, redaction policy changes, Gate 0 re-qualification unless record behavior regresses materially
- SME-oriented hand-holding copy beyond clear technical messages

## Deliverable A — Usability catalog

**Artifact:** `docs/USABILITY.md` (living doc).

### Structure

One section per stage: **Install**, **Record**, **Coverage**, **Align / Induce**, **Review**.

Each issue row:

| Column | Meaning |
|--------|---------|
| ID | Stable id, e.g. `REC-03` |
| Pain | What goes wrong |
| Why it bites | Technical-operator impact |
| Silent? | Y if failure/feedback can be missed |
| Severity | S (stop-demo / data risk), M (mid-session confusion), L (annoyance) |
| Phase 0 fix? | Y only if in Deliverable B |
| Gate 2 note | How it affects pilot demos / metrics |

### Seeded issues (to land in the catalog; editable as we learn)

**Install**
- `INS-01` Playwright Chromium / `channel=chromium` mismatch → headed launch fails (M, Phase 0 fix: clearer error)
- `INS-02` `ANTHROPIC_API_KEY` only discovered at induce (L, no Phase 0 UX fix beyond doc pointer)

**Record**
- `REC-01` No live feedback of captured events → operator unsure recorder is alive (M, silent Y, **fix**)
- `REC-02` Blank hotkey has no confirmation → scrub may be assumed or missed (S/M, silent Y, **fix**)
- `REC-03` Attention split: browser for demo, terminal for narration/`stop` (M, mitigate via banner; no new model)
- `REC-04` Focus events flood evidence without HUD filter (L, **fix**: HUD skips focus)
- `REC-05` Empty/failed attach can feel like a successful session (S, silent Y, **fix**: fail loud)

**Coverage**
- `COV-01` “Coverage %” vs unseen mass / promotion gate poorly explained after record (M, **fix**: plain-English line)
- `COV-02` Homogeneous demos look “done” by run count but Good-Turing disagrees (M, catalog + post-run line)

**Align / Induce**
- `IND-01` Large JSON to stdout is hard to scan (L, catalog only this effort)
- `IND-02` Unresolved postconditions / induction failure must be actionable (M, partial already; catalog; tiny error polish OK if touched)
- `IND-03` Corrections logged only via separate `log-corrections` (L, Gate 2 process note)

**Review**
- `REV-01` Only `pendant show` text IR; no evidence replay / poka-yoke (M anticipated Phase 1; catalog)
- `REV-02` No way to answer `guard_question` in-product (M Phase 1; catalog)

## Deliverable B — Record-session HUD + fail-loud polish

**Surface:** `pendant record` (+ small helpers, e.g. `pendant/cli_hud.py` or functions in `cli.py`). No new subcommands.

### Startup banner (stderr)

Print once after attach succeeds:

- run id, process id/name
- blank hotkey and window seconds
- how to narrate (type + Enter) and finish (`stop`)
- store root path (so wrong `--store` is obvious)

### Live lines (stderr, non-blocking)

After each **alignable user** event of kinds: `navigate`, `click`, `input`, `key`, `dialog`, `download` (not `focus`, not every network tick unless we later opt in):

```text
[12] click · Submit order
[13] input · Customer name = Globex…   # redacted values show as {redacted}/<secret>
```

- Coalesced inputs: prefer updating understanding via the final coalesced event (collector already coalesces); HUD prints when the coalesced event is emitted/updated without spamming per keystroke if the collector only emits once per field—match collector behavior.
- Target snippet: accessible name, else testid, else truncated URL for navigate.
- Cap line length (~100 chars) so the terminal stays readable mid-demo.

### Blank confirmation

On `blank_last` / blank_request path, print:

```text
blanked K events in last Ns (epoch N)
```

If K=0, still print so the operator knows the hotkey fired.

### Idle warning (once)

If ≥30s after attach with zero HUD-eligible events, print one warning:

```text
still no clicks/inputs/navigations — is this the right page?
```

### Finalize summary

After successful `finalize` + `add_run`:

- tally of events by kind (compact)
- coverage one-liner from store (estimated coverage %, unseen mass %, runs)
- plain English: if unseen mass > 0.10, state that promotion past `draft` is blocked until ≤10%
- exit 0

### Fail loud

- If browser launch (including missing Chromium / `channel=chromium`), attach, or `add_run` fails: print clear error on stderr, exit 1 (`INS-01`, `REC-05`)
- Do not claim “Stored run …” on failure
- Prefer catching expected failure types over raw tracebacks for operator-facing paths (optional `--debug` for traceback is out of scope unless trivial)

### Align / induce

Out of scope for HUD. Keep existing clearer induce errors; catalog documents remaining friction.

## Architecture notes

- HUD is presentation-only: does not mutate traces, change redaction, or alter event schemas.
- **Hook model (resolved):** collector accepts an optional `on_event` callback invoked (a) when a new event is appended and (b) when an input is coalesced in place (same `event_id`, updated payload). CLI HUD prints from that callback. Polling `len(events)` alone is insufficient because coalesce does not increase length.
- Blank confirmation: invoke the same reporter from `blank_last` return path (CLI wraps or collector callback `on_blank(k, seconds, epoch)`).
- Idle warning: CLI-side timer/task cancelled on first HUD-eligible event and on shutdown; must not block `stop`.
- Live HUD skips `focus` and does not print per-network events (network still recorded; tally shows them at finalize).

## Testing

- Unit tests for formatting helpers (snippets, tallies, blank line, promotion sentence).
- Optional: lightweight test that blank path invokes the confirmation printer (mock/spy).
- Full Gate 0 not required unless collector event emission semantics change; HUD should observe existing emissions.

## Success criteria

1. `docs/USABILITY.md` exists with all five stages and the seeded IDs above (refined as needed).
2. During `pendant record`, a technical operator sees live capture lines and blank confirmation without new commands.
3. Post-run coverage explains the promotion gate in one plain sentence when blocked.
4. Attach/store failures exit non-zero with an explicit message.
5. No new CLI subcommands; no web UI; invariants 1–3 unchanged.

## Open decisions resolved in brainstorming

- Catalog + Phase 0 CLI/capture fixes (Approach 2)
- Thin polish + guided record HUD (not full demo wizard)
- Primary persona: technical operator (cognitive load + silent failures)
