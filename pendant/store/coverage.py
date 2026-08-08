"""Good-Turing demonstration-sufficiency estimator (invariant 9).

A run's *variant* is its canonicalized token sequence: two
demonstrations that walk the same steps in the same order are the same
variant regardless of parameter values. The estimated probability mass
of unseen variants is the Good-Turing missing-mass estimate
P0 ~= N1 / N, where N1 is the number of variants observed exactly once
and N the number of runs.

The review surface displays estimated coverage, never raw run count.
"""

from __future__ import annotations

from collections import Counter

from pendant.align.normalizer import NormalizerRules, canonical_token
from pendant.capture.schema import RunTrace
from pendant.ir.models import CoverageEstimate


def variant_key(trace: RunTrace, rules: NormalizerRules | None = None) -> str:
    rules = rules or NormalizerRules()
    tokens = []
    for event in trace.events:
        token = canonical_token(event, rules)
        if token is not None:
            tokens.append(str(token))
    return "\u241e".join(tokens)  # symbol-for-record-separator joiner


def good_turing_coverage(
    traces: list[RunTrace], rules: NormalizerRules | None = None
) -> CoverageEstimate:
    if not traces:
        return CoverageEstimate(
            runs=0, distinct_variants=0, singleton_variants=0, unseen_mass=1.0
        )
    counts = Counter(variant_key(t, rules) for t in traces)
    singletons = sum(1 for c in counts.values() if c == 1)
    return CoverageEstimate(
        runs=len(traces),
        distinct_variants=len(counts),
        singleton_variants=singletons,
        unseen_mass=singletons / len(traces),
    )
