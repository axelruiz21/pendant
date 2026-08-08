"""Direct unit tests for align/ internals (pure, never mocked)."""

from pendant.align.msa import AlignParams, SeqItem, progressive_align
from pendant.align.normalizer import (
    CanonToken,
    NormalizerRules,
    normalize_name,
    normalize_url_payload,
    normalize_url_token,
)

RULES = NormalizerRules()


class TestNormalizer:
    def test_volatile_substrings_stripped_from_names(self) -> None:
        assert (
            normalize_name("Open order ORD-20260807-1432", RULES)
            == "Open order ORD-{num}-{num}"
        )
        assert normalize_name("Updated 2026-08-07T10:00:00Z", RULES) == "Updated {ts}"
        assert (
            normalize_name("Job 550e8400-e29b-41d4-a716-446655440000 done", RULES)
            == "Job {uuid} done"
        )
        assert normalize_name("Backup at 12:34:56", RULES) == "Backup at {time}"

    def test_short_numbers_survive(self) -> None:
        assert normalize_name("Add 10 items", RULES) == "Add 10 items"

    def test_url_token_drops_volatile_and_templatizes(self) -> None:
        url = "https://orders.acme.test/orders?session=abc123&tab=items"
        assert normalize_url_token(url, RULES) == "https://orders.acme.test/orders?tab={tab}"
        assert (
            normalize_url_token("https://a.test/orders/48219/edit", RULES)
            == "https://a.test/orders/{num}/edit"
        )

    def test_url_payload_keeps_literals(self) -> None:
        url = "https://orders.acme.test/orders/48219?session=abc&tab=items"
        assert (
            normalize_url_payload(url, RULES)
            == "https://orders.acme.test/orders/48219?tab=items"
        )


def item(token_name: str, run: str, i: int) -> SeqItem:
    return SeqItem(
        token=CanonToken("click", "button", token_name, "https://a.test/"),
        event_id=f"{run}-{i}",
        payload=(),
    )


class TestMSA:
    def test_identical_runs_align_one_to_one(self) -> None:
        seqs = {
            "r1": [item(n, "r1", i) for i, n in enumerate(["A", "B", "C"])],
            "r2": [item(n, "r2", i) for i, n in enumerate(["A", "B", "C"])],
        }
        cols = progressive_align(seqs)
        assert [c.token.name for c in cols] == ["A", "B", "C"]
        assert all(len(c.cells) == 2 for c in cols)

    def test_subset_step_gets_partial_column(self) -> None:
        seqs = {
            "r1": [item(n, "r1", i) for i, n in enumerate(["A", "X", "B"])],
            "r2": [item(n, "r2", i) for i, n in enumerate(["A", "B"])],
        }
        cols = progressive_align(seqs)
        assert [c.token.name for c in cols] == ["A", "X", "B"]
        assert sorted(cols[1].cells) == ["r1"]

    def test_different_tokens_never_merge(self) -> None:
        seqs = {
            "r1": [item("A", "r1", 0)],
            "r2": [item("B", "r2", 0)],
        }
        cols = progressive_align(seqs)
        assert len(cols) == 2
        assert all(len(c.cells) == 1 for c in cols)

    def test_deterministic_across_run_id_permutations(self) -> None:
        base = ["A", "B", "B", "C"]
        variant = ["A", "B", "C"]
        s1 = {
            "r1": [item(n, "r1", i) for i, n in enumerate(base)],
            "r2": [item(n, "r2", i) for i, n in enumerate(variant)],
            "r3": [item(n, "r3", i) for i, n in enumerate(base)],
        }
        cols_a = progressive_align(s1, AlignParams())
        cols_b = progressive_align(dict(reversed(list(s1.items()))), AlignParams())
        shape_a = [(str(c.token), sorted(c.cells)) for c in cols_a]
        shape_b = [(str(c.token), sorted(c.cells)) for c in cols_b]
        assert shape_a == shape_b
