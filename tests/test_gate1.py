"""Gate 1: aligner qualification against the seeded defect library.

PASS criteria (CLAUDE.md Part IV):
  - >= 95% column classification accuracy overall, AND
  - ZERO conditional regions misclassified as invariant (absolute;
    the two error types do not trade against each other).

Scoring: produced columns are paired to hand-written ground truth by
LCS over canonical tokens. Within adjacent groups of identical tokens
the class comparison is multiset-based, because which of several
identical columns absorbs a gap is arbitrary (fixture s3). Expected
columns left unpaired count as "missed"; produced columns left
unpaired are "spurious" and reported.

Run directly to (re)generate the committed report:
  uv run python tests/test_gate1.py   -> docs/GATE1_REPORT.md
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from pendant.align import align_runs
from pendant.capture.schema import load_run_trace

TRACES = Path(__file__).parent / "fixtures" / "traces"
CLASSES = ["invariant", "parameterized", "conditional", "unordered"]
PRED_LABELS = [*CLASSES, "missed"]


@dataclass
class ScenarioResult:
    name: str
    expected_total: int
    correct: int
    confusion: Counter[tuple[str, str]]  # (true, predicted)
    spurious: list[str]


@dataclass
class GateResult:
    scenarios: list[ScenarioResult] = field(default_factory=list)

    @property
    def confusion(self) -> Counter[tuple[str, str]]:
        total: Counter[tuple[str, str]] = Counter()
        for s in self.scenarios:
            total.update(s.confusion)
        return total

    @property
    def expected_total(self) -> int:
        return sum(s.expected_total for s in self.scenarios)

    @property
    def correct(self) -> int:
        return sum(s.correct for s in self.scenarios)

    @property
    def accuracy(self) -> float:
        return self.correct / self.expected_total

    @property
    def conditional_as_invariant(self) -> int:
        return self.confusion[("conditional", "invariant")]

    @property
    def passed(self) -> bool:
        return self.accuracy >= 0.95 and self.conditional_as_invariant == 0


def _lcs_pairs(a: list[str], b: list[str]) -> list[tuple[int, int]]:
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            dp[i][j] = (
                dp[i + 1][j + 1] + 1 if a[i] == b[j] else max(dp[i + 1][j], dp[i][j + 1])
            )
    pairs: list[tuple[int, int]] = []
    i = j = 0
    while i < n and j < m:
        if a[i] == b[j]:
            pairs.append((i, j))
            i, j = i + 1, j + 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def score_scenario(scenario_dir: Path) -> ScenarioResult:
    manifest = json.loads((scenario_dir / "manifest.json").read_text(encoding="utf-8"))
    traces = [load_run_trace(scenario_dir / fn) for fn in manifest["runs"]]
    report = align_runs(traces)
    expected = [(c["token"], c["class"]) for c in manifest["expected_columns"]]
    produced = [(c.token, c.classification) for c in report.columns]

    pairs = _lcs_pairs([t for t, _ in expected], [t for t, _ in produced])
    confusion: Counter[tuple[str, str]] = Counter()

    # Group consecutive LCS pairs that share a token; compare class
    # multisets inside each group.
    groups: list[list[tuple[int, int]]] = []
    for pair in pairs:
        token = expected[pair[0]][0]
        if groups and expected[groups[-1][-1][0]][0] == token and groups[-1][-1][0] == pair[0] - 1:
            groups[-1].append(pair)
        else:
            groups.append([pair])
    for group in groups:
        exp_classes = sorted(expected[i][1] for i, _ in group)
        prod_classes = sorted(produced[j][1] for _, j in group)
        exp_counter, prod_counter = Counter(exp_classes), Counter(prod_classes)
        for cls in exp_counter & prod_counter:
            confusion[(cls, cls)] += (exp_counter & prod_counter)[cls]
        leftover_exp = sorted((exp_counter - prod_counter).elements())
        leftover_prod = sorted((prod_counter - exp_counter).elements())
        for true_cls, pred_cls in zip(leftover_exp, leftover_prod, strict=True):
            confusion[(true_cls, pred_cls)] += 1

    paired_exp = {i for i, _ in pairs}
    paired_prod = {j for _, j in pairs}
    for i, (_, cls) in enumerate(expected):
        if i not in paired_exp:
            confusion[(cls, "missed")] += 1
    spurious: list[str] = [
        produced[j][1] for j in range(len(produced)) if j not in paired_prod
    ]

    correct = sum(confusion[(c, c)] for c in CLASSES)
    return ScenarioResult(
        name=manifest["scenario"],
        expected_total=len(expected),
        correct=correct,
        confusion=confusion,
        spurious=spurious,
    )


def run_gate1() -> GateResult:
    result = GateResult()
    for scenario_dir in sorted(TRACES.iterdir()):
        if (scenario_dir / "manifest.json").exists():
            result.scenarios.append(score_scenario(scenario_dir))
    return result


def render_report(result: GateResult) -> str:
    lines: list[str] = []
    lines.append("# Gate 1 Report — Aligner Qualification (Seeded Defect Library)")
    lines.append("")
    lines.append(f"Result: **{'PASS' if result.passed else 'FAIL'}**")
    lines.append("")
    lines.append(
        f"- Column classification accuracy: **{result.accuracy * 100:.2f}%** "
        f"({result.correct}/{result.expected_total}) — criterion: >= 95%"
    )
    lines.append(
        f"- Conditional regions misclassified as invariant: "
        f"**{result.conditional_as_invariant}** — criterion: zero, absolute"
    )
    spurious_total = sum(len(s.spurious) for s in result.scenarios)
    lines.append(f"- Spurious columns (produced, not in ground truth): {spurious_total}")
    lines.append("")
    lines.append("## Confusion matrix (rows = ground truth, columns = predicted)")
    lines.append("")
    confusion = result.confusion
    header = "| true \\ predicted | " + " | ".join(PRED_LABELS) + " | total |"
    lines.append(header)
    lines.append("|" + "---|" * (len(PRED_LABELS) + 2))
    for true_cls in CLASSES:
        row_total = sum(confusion[(true_cls, p)] for p in PRED_LABELS)
        cells = " | ".join(str(confusion[(true_cls, p)]) for p in PRED_LABELS)
        lines.append(f"| {true_cls} | {cells} | {row_total} |")
    lines.append("")
    lines.append("## Per-scenario results")
    lines.append("")
    lines.append("| scenario | columns | correct | errors | spurious |")
    lines.append("|---|---|---|---|---|")
    for s in result.scenarios:
        errors = s.expected_total - s.correct
        lines.append(
            f"| {s.name} | {s.expected_total} | {s.correct} | {errors} | {len(s.spurious)} |"
        )
    lines.append("")
    lines.append(
        "Corpus: 20 hand-authored RunTrace fixtures across 8 seeded-defect scenarios "
        "(tests/fixtures/traces/, docs/DECISIONS.md D-003). Aligner parameters: committed "
        "defaults (docs/DECISIONS.md D-007)."
    )
    lines.append("")
    return "\n".join(lines)


class TestGate1:
    def test_overall_accuracy_at_least_95_percent(self) -> None:
        result = run_gate1()
        detail = {s.name: f"{s.correct}/{s.expected_total}" for s in result.scenarios}
        assert result.accuracy >= 0.95, f"accuracy {result.accuracy:.3f}; per-scenario {detail}"

    def test_zero_conditionals_misclassified_as_invariant(self) -> None:
        result = run_gate1()
        assert result.conditional_as_invariant == 0

    def test_every_scenario_aligned_without_spurious_columns(self) -> None:
        result = run_gate1()
        spurious = {s.name: s.spurious for s in result.scenarios if s.spurious}
        assert not spurious, f"spurious columns: {spurious}"


if __name__ == "__main__":
    gate = run_gate1()
    report = render_report(gate)
    out = Path(__file__).parent.parent / "docs" / "GATE1_REPORT.md"
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"written: {out}")
    raise SystemExit(0 if gate.passed else 1)
