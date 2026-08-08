# PENDANT Capture — Recorder Design and Qualification

Status: qualified at Gate 0 (see `docs/GATE0_REPORT.md`): 100.00%
fidelity over 13 runs on two distinct Chromium builds, zero redaction
escapes, reproducible token sequences.

## Architecture

CDP-based recorder over Playwright's Python async API with a raw CDP
session per page (`pendant/capture/collector.py`). Four concurrent
channels merge into one time-correlated stream with monotonic and
wall-clock timestamps on every record; output is NDJSON, one RunTrace
per demonstration (`pendant/capture/schema.py`).

| Tier | Channel | Source | Notes |
|---|---|---|---|
| 1 | Network | CDP `Network.*` | XHR/Fetch traffic; method, templatized URL (`/api/orders/48219 -> /api/orders/{p0}`, literals preserved in `url_params`), status, request/response body hashes computed over registry-redacted bodies |
| 2 | Structure | injected content script | full identity vector per invariant 4 (role, accessible name, testid, stable attrs, CSS path, XPath, frame URL, bbox) snapshotted at every user-initiated event |
| 3 | Events | content script + CDP `Page.*` | clicks, inputs (coalesced per element), Enter/Tab/Escape keys, navigations, focus, clipboard, dialogs, downloads |
| 4 | Pixels | `page.screenshot` | one masked screenshot per user event, content-addressed (SHA-256), best-effort during navigation |
| 5 | Narration | operator-typed notes | anchored at entry time; the only channel carrying intent (see D-012 for the audio deviation) |

The network event is emitted at CDP `responseReceived`, not
`loadingFinished`: a response whose body the page never reads (e.g.
`fetch().ok` followed by navigation) may never finish loading, and
losing the Tier 1 record of a POST that provably happened would be a
capture drop. The response body hash is attached asynchronously when
the body is retrievable.

## Redaction (invariant 3 — inside the collector, before any write)

1. **Password-type inputs never leave the page.** The content script
   sends `value: null, secret: true` for `input[type=password]`; the
   collector never sees the value.
2. **Field registry** (`pendant/capture/redaction.py`, configurable):
   input values whose field identity (accessible name, testid, id/name
   attributes, autocomplete) matches a secret or PII rule are replaced
   with `{redacted}` before buffering.
3. **Headers**: the Event schema has no header field; Authorization,
   Cookie, and friends are structurally unrepresentable in a trace.
4. **Bodies**: request/response bodies are never persisted; only
   SHA-256 hashes of *registry-redacted* bodies are stored, so a short
   secret cannot be recovered by dictionary attack on the hash.
   Non-JSON/non-form bodies are treated as opaque and excluded from
   hashing input entirely.
5. **URLs**: values of secret-looking query parameters are redacted in
   navigate payloads.
6. **Screenshots** are masked with selectors derived from the same
   registry (`mask_keywords`), plus `input[type=password]`.
7. **Operator hotkey**: Alt+Shift+X blanks values and deletes buffered
   screenshots from the last N seconds (default 10, `--blank-window`).

Qualification: the reference process types a seeded password and a
seeded card number; Gate 0 scans every byte of every persisted
artifact for both canaries. One escape fails the gate — severity 10,
no waiver.

## Known Phase 0 limitations (disclosed, not hidden)

- Keystroke channel records Enter/Tab/Escape (plus modifiers) only;
  text entry is captured as coalesced input events with final values.
- JavaScript dialogs are observed via CDP and recorded, but Playwright
  auto-dismisses them during scripted capture.
- Only XHR/Fetch resource types are recorded on Tier 1; static assets
  are noise for process purposes. Document navigations appear on the
  navigate channel.
- Narration is typed, not spoken (D-012).
- Screenshot masking is selector-driven; the byte-level canary scan
  cannot see rendered pixels, so a manual blob spot-check is part of
  reviewing any registry change.
- The Gate 0 reproducibility leg is single-machine, dual-build
  (D-004); rerun `pendant capture-msa` on a second machine when one is
  available.

## Running the study

    uv run playwright install chromium
    uv run pendant capture-msa --runs 10 --runs-alt 3

Writes `docs/GATE0_REPORT.md` and exits nonzero on any gate failure.
