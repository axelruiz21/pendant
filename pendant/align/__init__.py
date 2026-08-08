"""align: deterministic multiple sequence alignment over event traces.

Zero network dependencies, fully unit-testable, never delegated to an
LLM (invariant 2). Pipeline: canonicalize (normalizer) -> progressive
pairwise Needleman-Wunsch with affine gaps (msa) -> column
classification (classifier) -> AlignmentReport (report).
"""

from pendant.align.classifier import classify
from pendant.align.msa import AlignParams, progressive_align
from pendant.align.normalizer import NormalizerRules, canonical_token, payload_key
from pendant.align.report import AlignedCell, AlignedColumn, AlignmentReport, align_runs

__all__ = [
    "AlignParams",
    "AlignedCell",
    "AlignedColumn",
    "AlignmentReport",
    "NormalizerRules",
    "align_runs",
    "canonical_token",
    "classify",
    "payload_key",
    "progressive_align",
]
