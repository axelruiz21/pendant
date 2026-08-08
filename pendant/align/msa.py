"""Progressive pairwise Needleman-Wunsch (Gotoh affine gaps) over profiles.

Deterministic by construction: fixed scoring parameters, fixed guide
order (center-star seeded by the most similar pair, ties broken by
run id), fixed traceback preference. Two columns merge only when
their tokens are identical; there is no cross-token substitution, so
every aligned column carries exactly one token (docs/DECISIONS.md
D-007).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pendant.align.normalizer import CanonToken

NEG = float("-inf")


@dataclass(frozen=True)
class SeqItem:
    """One alignable event of one run, canonicalized."""

    token: CanonToken
    event_id: str
    payload: tuple[object, ...]


@dataclass
class Column:
    """One aligned column: a single token with per-run occupancy."""

    token: CanonToken
    cells: dict[str, SeqItem] = field(default_factory=dict)  # run_id -> item


@dataclass(frozen=True)
class AlignParams:
    match: float = 4.0
    gap_open: float = -6.0
    gap_extend: float = -1.0
    max_unordered_window: int = 8


def _lcs_len(a: list[CanonToken], b: list[CanonToken]) -> int:
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0] * (len(b) + 1)
        for j, y in enumerate(b, start=1):
            cur[j] = prev[j - 1] + 1 if x == y else max(prev[j], cur[j - 1])
        prev = cur
    return prev[len(b)]


def _similarity(a: list[CanonToken], b: list[CanonToken]) -> float:
    if not a and not b:
        return 1.0
    return 2.0 * _lcs_len(a, b) / (len(a) + len(b))


def _pairwise_profiles(a: list[Column], b: list[Column], p: AlignParams) -> list[Column]:
    """Gotoh alignment of two profiles; merge only identical tokens."""
    n, m = len(a), len(b)
    mm = [[NEG] * (m + 1) for _ in range(n + 1)]
    gx = [[NEG] * (m + 1) for _ in range(n + 1)]  # gap in b (a column vs gap)
    gy = [[NEG] * (m + 1) for _ in range(n + 1)]  # gap in a
    mm[0][0] = 0.0
    for i in range(1, n + 1):
        gx[i][0] = p.gap_open + p.gap_extend * (i - 1)
    for j in range(1, m + 1):
        gy[0][j] = p.gap_open + p.gap_extend * (j - 1)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if a[i - 1].token == b[j - 1].token:
                best_prev = max(mm[i - 1][j - 1], gx[i - 1][j - 1], gy[i - 1][j - 1])
                mm[i][j] = best_prev + p.match
            gx[i][j] = max(
                mm[i - 1][j] + p.gap_open,
                gx[i - 1][j] + p.gap_extend,
                gy[i - 1][j] + p.gap_open,
            )
            gy[i][j] = max(
                mm[i][j - 1] + p.gap_open,
                gx[i][j - 1] + p.gap_open,
                gy[i][j - 1] + p.gap_extend,
            )
    # Deterministic traceback: preference M > X > Y at every choice.
    out: list[Column] = []
    i, j = n, m
    state = _pick(
        {"M": mm[n][m], "X": gx[n][m], "Y": gy[n][m]},
        max(mm[n][m], gx[n][m], gy[n][m]),
    )
    while i > 0 or j > 0:
        if state == "M" and i > 0 and j > 0:
            merged = Column(token=a[i - 1].token, cells={**a[i - 1].cells, **b[j - 1].cells})
            out.append(merged)
            prev_scores = {
                "M": mm[i - 1][j - 1],
                "X": gx[i - 1][j - 1],
                "Y": gy[i - 1][j - 1],
            }
            target = mm[i][j] - p.match
            state = _pick(prev_scores, target)
            i, j = i - 1, j - 1
        elif state == "X" and i > 0:
            out.append(Column(token=a[i - 1].token, cells=dict(a[i - 1].cells)))
            prev_scores = {
                "M": mm[i - 1][j] + p.gap_open,
                "X": gx[i - 1][j] + p.gap_extend,
                "Y": gy[i - 1][j] + p.gap_open,
            }
            state = _pick(prev_scores, gx[i][j])
            i = i - 1
        elif state == "Y" and j > 0:
            out.append(Column(token=b[j - 1].token, cells=dict(b[j - 1].cells)))
            prev_scores = {
                "M": mm[i][j - 1] + p.gap_open,
                "X": gx[i][j - 1] + p.gap_open,
                "Y": gy[i][j - 1] + p.gap_extend,
            }
            state = _pick(prev_scores, gy[i][j])
            j = j - 1
        else:  # exhausted one sequence; only gaps remain
            state = "X" if i > 0 else "Y"
    out.reverse()
    return out


def _pick(prev_scores: dict[str, float], target: float) -> str:
    for s in ("M", "X", "Y"):
        if prev_scores[s] == target:
            return s
    raise AssertionError("traceback inconsistency")


def progressive_align(
    sequences: dict[str, list[SeqItem]], params: AlignParams | None = None
) -> list[Column]:
    """Collapse N runs into an aligned column matrix.

    Guide order: seed with the most similar pair, then join remaining
    runs by descending mean similarity to the included set. All ties
    break on run id, so the result is a pure function of the input.
    """
    p = params or AlignParams()
    run_ids = sorted(sequences)
    if not run_ids:
        return []
    if len(run_ids) == 1:
        only = run_ids[0]
        return [
            Column(token=it.token, cells={only: it}) for it in sequences[only]
        ]
    tokens = {r: [it.token for it in sequences[r]] for r in run_ids}

    def pair(x: str, y: str) -> tuple[str, str]:
        return (x, y) if x < y else (y, x)

    sim: dict[tuple[str, str], float] = {}
    for idx, r1 in enumerate(run_ids):
        for r2 in run_ids[idx + 1 :]:
            sim[(r1, r2)] = _similarity(tokens[r1], tokens[r2])
    best_score = max(sim.values())
    seed = min(k for k, v in sim.items() if v == best_score)
    included = [seed[0], seed[1]]

    def to_profile(run_id: str) -> list[Column]:
        return [Column(token=it.token, cells={run_id: it}) for it in sequences[run_id]]

    profile = _pairwise_profiles(to_profile(seed[0]), to_profile(seed[1]), p)
    remaining = [r for r in run_ids if r not in included]
    while remaining:

        def mean_sim(r: str) -> float:
            return sum(sim[pair(r, o)] for o in included) / len(included)

        best = min(remaining, key=lambda r: (-mean_sim(r), r))
        profile = _pairwise_profiles(profile, to_profile(best), p)
        included.append(best)
        remaining.remove(best)
    return profile
