# Gate 0 Report — Recorder Qualification (Capture Fidelity Study)

Result: **PASS**

- Event capture fidelity: **100.00%** — criterion: >= 99.5%
- Drop rate: 0.00%
- Duplicate rate: 0.00%
- Redaction escapes: **0** — criterion: zero, severity 10, no waiver
- Token sequences reproducible across all runs and both builds: **yes**

## Study design

Deterministic reference process (28 ground-truth events, hand-written in tests/reference_process/script.json) replayed 10x on the primary build and 3x on a second distinct browser build (single-machine, dual-build reproducibility leg per docs/DECISIONS.md D-004).

- headless-shell: version 151.0.7922.34
- chromium-full: version 151.0.7922.34

Seeded canaries (password typed into the login form, card number typed into the order form) were scanned for in every byte of every persisted artifact (NDJSON traces and screenshot blobs).

## Per-run results

| run | build | expected | matched | drops | dups | unexpected | fidelity |
|---|---|---|---|---|---|---|---|
| ref-headless-shell-01 | headless-shell | 28 | 28 | 0 | 0 | 0 | 100.0% |
| ref-headless-shell-02 | headless-shell | 28 | 28 | 0 | 0 | 0 | 100.0% |
| ref-headless-shell-03 | headless-shell | 28 | 28 | 0 | 0 | 0 | 100.0% |
| ref-headless-shell-04 | headless-shell | 28 | 28 | 0 | 0 | 0 | 100.0% |
| ref-headless-shell-05 | headless-shell | 28 | 28 | 0 | 0 | 0 | 100.0% |
| ref-headless-shell-06 | headless-shell | 28 | 28 | 0 | 0 | 0 | 100.0% |
| ref-headless-shell-07 | headless-shell | 28 | 28 | 0 | 0 | 0 | 100.0% |
| ref-headless-shell-08 | headless-shell | 28 | 28 | 0 | 0 | 0 | 100.0% |
| ref-headless-shell-09 | headless-shell | 28 | 28 | 0 | 0 | 0 | 100.0% |
| ref-headless-shell-10 | headless-shell | 28 | 28 | 0 | 0 | 0 | 100.0% |
| ref-chromium-full-01 | chromium-full | 28 | 28 | 0 | 0 | 0 | 100.0% |
| ref-chromium-full-02 | chromium-full | 28 | 28 | 0 | 0 | 0 | 100.0% |
| ref-chromium-full-03 | chromium-full | 28 | 28 | 0 | 0 | 0 | 100.0% |

## Alignment cross-check (primary arm)

Column classes from align/ over the captured traces: {'invariant': 48}; full run presence in every column: yes.

Screenshot masking is selector-driven (registry mask keywords); the byte-level canary scan cannot detect PII rendered as pixels, so mask coverage was verified by selector construction and a manual spot check of blobs remains a reviewer duty when the registry changes.
