"""Store: append-only semantics, coverage estimation, promotion gate."""

from pathlib import Path

import pytest
from test_ir import make_envelope  # reuses the valid two-step envelope builder

from pendant.capture.schema import RunTrace, load_run_trace
from pendant.ir.models import ProcessEnvelope
from pendant.store import PromotionRefused, Store, good_turing_coverage

FIXTURES = Path(__file__).parent / "fixtures" / "traces"


def load_scenario(name: str) -> list[RunTrace]:
    d = FIXTURES / name
    return [load_run_trace(p) for p in sorted(d.glob("*.ndjson"))]


class TestCoverage:
    def test_no_runs_means_no_coverage(self) -> None:
        cov = good_turing_coverage([])
        assert cov.unseen_mass == 1.0

    def test_identical_runs_converge(self) -> None:
        traces = load_scenario("s7_event_drops")  # 2 runs, 2 distinct variants
        cov = good_turing_coverage(traces)
        assert cov.runs == 2
        assert cov.distinct_variants == 2  # the drop makes run 2 a distinct variant
        assert cov.unseen_mass == 1.0  # both singletons: no confidence at all

    def test_repeated_variants_reduce_unseen_mass(self) -> None:
        traces = load_scenario("s7_event_drops")
        # Duplicate each variant by re-labelling run ids: 4 runs, 2 variants,
        # 0 singletons -> unseen mass 0.
        clones = []
        for t in traces:
            events = [
                e.model_copy(
                    update={"run_id": t.run_id + "b", "event_id": e.event_id + "b"}
                )
                for e in t.events
            ]
            clones.append(t.model_copy(update={"run_id": t.run_id + "b", "events": events}))
        cov = good_turing_coverage(traces + clones)
        assert cov.runs == 4
        assert cov.singleton_variants == 0
        assert cov.unseen_mass == 0.0


class TestStore:
    def test_run_round_trip(self, tmp_path: Path) -> None:
        store = Store(tmp_path / "store")
        store.create_process("p1", "Order entry")
        traces = load_scenario("s1_param_single")
        for t in traces:
            store.add_run("p1", t)
        assert len(store.list_runs("p1")) == 3
        loaded = store.get_trace(traces[0].run_id)
        assert loaded == traces[0]

    def test_blob_content_addressing(self, tmp_path: Path) -> None:
        store = Store(tmp_path / "store")
        ref = store.put_blob(b"evidence bytes")
        assert store.get_blob(ref) == b"evidence bytes"
        assert store.put_blob(b"evidence bytes") == ref

    def test_ir_versions_append_only(self, tmp_path: Path) -> None:
        store = Store(tmp_path / "store")
        store.create_process("proc-1", "Order entry")
        env = make_envelope()
        store.save_envelope(env)
        with pytest.raises(ValueError, match="append-only"):
            store.save_envelope(env)

    def test_promotion_refused_while_unseen_mass_high(self, tmp_path: Path) -> None:
        store = Store(tmp_path / "store")
        store.create_process("proc-1", "Order entry")
        # s2: runs 1 and 3 share the dialog variant, run 2 is a singleton
        # variant -> Good-Turing unseen mass 1/3.
        for t in load_scenario("s2_conditional"):
            store.add_run("proc-1", t)
        store.save_envelope(make_envelope())
        cov = store.coverage("proc-1")
        assert cov.unseen_mass == pytest.approx(1 / 3)
        with pytest.raises(PromotionRefused, match="unseen variant mass"):
            store.promote("proc-1", 1, "reviewed", actor="operator:test")

    def test_promotion_passes_once_covered(self, tmp_path: Path) -> None:
        store = Store(tmp_path / "store")
        store.create_process("proc-1", "Order entry")
        base = load_scenario("s7_event_drops")[0]
        for i in range(12):  # 12 identical-variant runs -> unseen mass 0
            events = [
                e.model_copy(update={"run_id": f"r{i}", "event_id": f"r{i}-{e.seq}"})
                for e in base.events
            ]
            store.add_run("proc-1", base.model_copy(update={"run_id": f"r{i}", "events": events}))
        store.save_envelope(make_envelope())
        promoted = store.promote("proc-1", 1, "reviewed", actor="operator:test")
        assert promoted.review_state == "reviewed"
        assert promoted.coverage_estimate is not None
        assert promoted.coverage_estimate.unseen_mass == 0.0

    def test_envelope_overlay_carries_live_coverage(self, tmp_path: Path) -> None:
        store = Store(tmp_path / "store")
        store.create_process("proc-1", "Order entry")
        store.save_envelope(make_envelope())
        env = store.get_envelope("proc-1")
        assert isinstance(env, ProcessEnvelope)
        assert env.coverage_estimate is not None
        assert env.coverage_estimate.runs == 0
        assert env.coverage_estimate.unseen_mass == 1.0

    def test_illegal_transition_rejected(self, tmp_path: Path) -> None:
        store = Store(tmp_path / "store")
        store.create_process("proc-1", "Order entry")
        store.save_envelope(make_envelope())
        with pytest.raises(ValueError, match="illegal review transition"):
            store.promote("proc-1", 1, "approved", actor="operator:test")
