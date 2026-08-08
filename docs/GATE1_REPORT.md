# Gate 1 Report — Aligner Qualification (Seeded Defect Library)

Result: **PASS**

- Column classification accuracy: **100.00%** (93/93) — criterion: >= 95%
- Conditional regions misclassified as invariant: **0** — criterion: zero, absolute
- Spurious columns (produced, not in ground truth): 0

## Confusion matrix (rows = ground truth, columns = predicted)

| true \ predicted | invariant | parameterized | conditional | unordered | missed | total |
|---|---|---|---|---|---|---|
| invariant | 72 | 0 | 0 | 0 | 0 | 72 |
| parameterized | 0 | 12 | 0 | 0 | 0 | 12 |
| conditional | 0 | 0 | 6 | 0 | 0 | 6 |
| unordered | 0 | 0 | 0 | 3 | 0 | 3 |

## Per-scenario results

| scenario | columns | correct | errors | spurious |
|---|---|---|---|---|
| s1_param_single | 12 | 12 | 0 | 0 |
| s2_conditional | 13 | 13 | 0 | 0 |
| s3_repeated_steps | 12 | 12 | 0 | 0 |
| s4_unordered | 12 | 12 | 0 | 0 |
| s5_singleton | 12 | 12 | 0 | 0 |
| s6_volatile_ids | 10 | 10 | 0 | 0 |
| s7_event_drops | 12 | 12 | 0 | 0 |
| s8_frame_near_dup | 10 | 10 | 0 | 0 |

Corpus: 20 hand-authored RunTrace fixtures across 8 seeded-defect scenarios (tests/fixtures/traces/, docs/DECISIONS.md D-003). Aligner parameters: committed defaults (docs/DECISIONS.md D-007).
