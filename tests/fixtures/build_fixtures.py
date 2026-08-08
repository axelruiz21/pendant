"""Author the Gate 1 seeded defect library (docs/DECISIONS.md D-003).

Every event, variation, and defect below is individually intentional;
this script exists to eliminate hand-transcription errors in the
committed NDJSON, not to randomize anything. The committed files under
tests/fixtures/traces/ are the corpus of record; ground-truth labels
in each manifest.json are hand-written here.

Scenario coverage required by Gate 1:
  s1_param_single     one varying parameter
  s2_conditional      a conditional present in a subset of runs
  s3_repeated_steps   repeated identical steps (alignment ambiguity)
  s4_unordered        unordered region
  s5_singleton        a variant appearing in exactly one run
  s6_volatile_ids     volatile identifiers defeating naive canonicalization
  s7_event_drops      simulated event drops
  s8_frame_near_dup   near-duplicates differing only in frame context

Run:  uv run python tests/fixtures/build_fixtures.py
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pendant.capture.schema import Event, NetworkInfo, Payload, RunTrace
from pendant.ir.models import TargetVector

OUT = Path(__file__).parent / "traces"

LOGIN = "https://orders.acme.test/login"
NEW = "https://orders.acme.test/orders/new"
API = "https://orders.acme.test/api"

EPOCH = datetime(2026, 8, 7, 9, 0, 0, tzinfo=UTC)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


class R:
    """One demonstration run under construction."""

    def __init__(self, run_id: str, start_offset_min: int = 0) -> None:
        self.run_id = run_id
        self.events: list[Event] = []
        self._t0 = EPOCH + timedelta(minutes=start_offset_min)

    def _push(
        self,
        kind: str,
        tier: int,
        *,
        target: TargetVector | None = None,
        payload: Payload | None = None,
        network: NetworkInfo | None = None,
    ) -> None:
        seq = len(self.events)
        self.events.append(
            Event(
                event_id=f"{self.run_id}-{seq:03d}",
                run_id=self.run_id,
                seq=seq,
                t_mono_ms=float(seq * 800),
                t_wall=(self._t0 + timedelta(seconds=seq * 2)).isoformat(),
                tier=tier,  # type: ignore[arg-type]
                kind=kind,  # type: ignore[arg-type]
                target=target,
                payload=payload,
                network=network,
            )
        )

    def _vector(self, role: str, name: str, frame: str) -> TargetVector:
        s = slug(name)
        return TargetVector(
            role=role,
            name=name,
            testid=s,
            attrs={"data-testid": s},
            css=f'[data-testid="{s}"]',
            xpath=f'//*[@data-testid="{s}"]',
            frame_url=frame,
            bbox=(120.0, 240.0, 160.0, 36.0),
        )

    def navigate(self, url: str) -> None:
        self._push("navigate", 3, payload=Payload(value_redacted=url, value_class="url"))

    def click(self, name: str, *, frame: str = NEW, role: str = "button") -> None:
        self._push("click", 3, target=self._vector(role, name, frame))

    def fill(
        self,
        label: str,
        value: str,
        *,
        frame: str = NEW,
        role: str = "textbox",
        value_class: str = "text",
    ) -> None:
        self._push(
            "input",
            3,
            target=self._vector(role, label, frame),
            payload=Payload(value_redacted=value, value_class=value_class),
        )

    def key(self, *keys: str) -> None:
        self._push("key", 3, payload=Payload(keys=list(keys)))

    def net(
        self,
        method: str,
        url_template: str,
        status: int,
        *,
        params: dict[str, str] | None = None,
        req: str | None = None,
        resp: str | None = None,
    ) -> None:
        self._push(
            "network",
            1,
            network=NetworkInfo(
                method=method,
                url_template=url_template,
                url_params=params or {},
                status=status,
                req_sha=req,
                resp_sha=resp,
            ),
        )

    def dialog(self, message: str) -> None:
        self._push("dialog", 3, payload=Payload(value_redacted=message, value_class="text"))

    def narration(self, text: str) -> None:
        self._push("narration", 5, payload=Payload(value_redacted=text, value_class="speech"))

    def trace(self) -> RunTrace:
        return RunTrace(run_id=self.run_id, events=self.events)


def tok(kind: str, role: str = "", name: str = "", url: str = "") -> str:
    return f"{kind}|{role}|{name}|{url}"


def prefix(r: R) -> None:
    """Shared login prefix: five columns, all invariant."""
    r.navigate(LOGIN)
    r.fill("Username", "opsuser", frame=LOGIN)
    r.click("Sign in", frame=LOGIN)
    r.net("POST", f"{API}/login", 200, req="sha256:login-body", resp="sha256:login-ok")
    r.navigate(NEW)


PREFIX_EXPECTED: list[tuple[str, str]] = [
    (tok("navigate", url=LOGIN), "invariant"),
    (tok("input", "textbox", "Username", LOGIN), "invariant"),
    (tok("click", "button", "Sign in", LOGIN), "invariant"),
    (tok("network", url=f"{API}/login"), "invariant"),
    (tok("navigate", url=NEW), "invariant"),
]

Scenario = tuple[str, str, list[RunTrace], list[tuple[str, str]], str]


def s1_param_single() -> Scenario:
    customers = ["Globex Corp", "Initech LLC", "Hooli Inc"]
    order_ids = ["48219", "48220", "48221"]
    runs: list[RunTrace] = []
    for i, (customer, oid) in enumerate(zip(customers, order_ids, strict=True), start=1):
        r = R(f"s1-run{i}", start_offset_min=(i - 1) * 30)
        prefix(r)
        if i == 1:
            r.narration("Now I enter the customer exactly as it appears on the PO.")
        r.fill("Customer name", customer)
        r.fill("Quantity", "10")
        r.key("Enter")
        r.click("Create order")
        r.net("POST", f"{API}/orders", 201, req=f"sha256:order-{slug(customer)}")
        r.net("GET", f"{API}/orders/{{p0}}", 200, params={"p0": oid}, resp=f"sha256:o{oid}")
        r.click("Done")
        runs.append(r.trace())
    expected = [
        *PREFIX_EXPECTED,
        (tok("input", "textbox", "Customer name", NEW), "parameterized"),
        (tok("input", "textbox", "Quantity", NEW), "invariant"),
        (tok("key", name="Enter"), "invariant"),
        (tok("click", "button", "Create order", NEW), "invariant"),
        (tok("network", url=f"{API}/orders"), "parameterized"),
        (tok("network", url=f"{API}/orders/{{p0}}"), "parameterized"),
        (tok("click", "button", "Done", NEW), "invariant"),
    ]
    return (
        "s1_param_single",
        "One varying parameter (customer name) flowing through to Tier 1 evidence.",
        runs,
        expected,
        "Narration event in run 1 must be excluded from alignment (tier 5).",
    )


def s2_conditional() -> Scenario:
    quantities = ["500", "10", "500"]  # dialog fires on qty > 100: runs 1 and 3
    order_ids = ["48230", "48231", "48232"]
    runs: list[RunTrace] = []
    for i, (qty, oid) in enumerate(zip(quantities, order_ids, strict=True), start=1):
        r = R(f"s2-run{i}", start_offset_min=(i - 1) * 30)
        prefix(r)
        r.fill("Customer name", "Globex Corp")
        r.fill("Quantity", qty)
        r.click("Create order")
        if int(qty) > 100:
            r.dialog("Quantity exceeds 100 - approval required")
            r.click("Approve anyway")
        r.net("POST", f"{API}/orders", 201, req=f"sha256:order-qty-{qty}")
        r.net("GET", f"{API}/orders/{{p0}}", 200, params={"p0": oid}, resp=f"sha256:o{oid}")
        r.click("Done")
        runs.append(r.trace())
    expected = [
        *PREFIX_EXPECTED,
        (tok("input", "textbox", "Customer name", NEW), "invariant"),
        (tok("input", "textbox", "Quantity", NEW), "parameterized"),
        (tok("click", "button", "Create order", NEW), "invariant"),
        (tok("dialog", name="Quantity exceeds 100 - approval required"), "conditional"),
        (tok("click", "button", "Approve anyway", NEW), "conditional"),
        (tok("network", url=f"{API}/orders"), "parameterized"),
        (tok("network", url=f"{API}/orders/{{p0}}"), "parameterized"),
        (tok("click", "button", "Done", NEW), "invariant"),
    ]
    return (
        "s2_conditional",
        "Approval dialog present in runs 1 and 3 only (quantity-driven guard, unknown to aligner).",
        runs,
        expected,
        "The two conditional columns must NEVER be classified invariant (Gate 1 absolute).",
    )


def s3_repeated_steps() -> Scenario:
    line_counts = [3, 2]
    order_ids = ["48240", "48241"]
    runs: list[RunTrace] = []
    for i, (lines, oid) in enumerate(zip(line_counts, order_ids, strict=True), start=1):
        r = R(f"s3-run{i}", start_offset_min=(i - 1) * 30)
        prefix(r)
        for _ in range(lines):
            r.click("Add line")
        r.click("Create order")
        r.net("POST", f"{API}/orders", 201, req=f"sha256:order-{lines}-lines")
        r.net("GET", f"{API}/orders/{{p0}}", 200, params={"p0": oid}, resp=f"sha256:o{oid}")
        r.click("Done")
        runs.append(r.trace())
    expected = [
        *PREFIX_EXPECTED,
        (tok("click", "button", "Add line", NEW), "invariant"),
        (tok("click", "button", "Add line", NEW), "invariant"),
        (tok("click", "button", "Add line", NEW), "conditional"),
        (tok("click", "button", "Create order", NEW), "invariant"),
        (tok("network", url=f"{API}/orders"), "parameterized"),
        (tok("network", url=f"{API}/orders/{{p0}}"), "parameterized"),
        (tok("click", "button", "Done", NEW), "invariant"),
    ]
    return (
        "s3_repeated_steps",
        "Identical 'Add line' clicked 3x vs 2x: alignment ambiguity among identical tokens.",
        runs,
        expected,
        "Which of the three identical columns is the unmatched one is arbitrary; the harness "
        "compares class multisets within adjacent same-token groups.",
    )


def s4_unordered() -> Scenario:
    fields = {
        "A": ("Shipping address", "12 Harbor Way"),
        "B": ("Billing address", "99 Ledger St"),
        "C": ("Contact email", "ops@globex.test"),
    }
    orders = ["ABC", "CBA", "BAC"]
    order_ids = ["48250", "48251", "48252"]
    runs: list[RunTrace] = []
    for i, (order, oid) in enumerate(zip(orders, order_ids, strict=True), start=1):
        r = R(f"s4-run{i}", start_offset_min=(i - 1) * 30)
        prefix(r)
        for key in order:
            label, value = fields[key]
            r.fill(label, value)
        r.click("Create order")
        r.net("POST", f"{API}/orders", 201, req="sha256:order-addresses")
        r.net("GET", f"{API}/orders/{{p0}}", 200, params={"p0": oid}, resp=f"sha256:o{oid}")
        r.click("Done")
        runs.append(r.trace())
    expected = [
        *PREFIX_EXPECTED,
        (tok("input", "textbox", "Shipping address", NEW), "unordered"),
        (tok("input", "textbox", "Billing address", NEW), "unordered"),
        (tok("input", "textbox", "Contact email", NEW), "unordered"),
        (tok("click", "button", "Create order", NEW), "invariant"),
        (tok("network", url=f"{API}/orders"), "invariant"),
        (tok("network", url=f"{API}/orders/{{p0}}"), "parameterized"),
        (tok("click", "button", "Done", NEW), "invariant"),
    ]
    return (
        "s4_unordered",
        "Three fills with identical values entered in a different order in every run.",
        runs,
        expected,
        "Unordered region order in expected_columns follows run 1; the harness treats the "
        "unordered group as order-insensitive.",
    )


def s5_singleton() -> Scenario:
    order_ids = ["48260", "48261", "48262"]
    runs: list[RunTrace] = []
    for i, oid in enumerate(order_ids, start=1):
        r = R(f"s5-run{i}", start_offset_min=(i - 1) * 30)
        prefix(r)
        if i == 2:  # variant in exactly one run
            r.click("Dismiss")
            r.net("POST", f"{API}/session/refresh", 200, req="sha256:refresh")
        r.fill("Customer name", "Globex Corp")
        r.click("Create order")
        r.net("POST", f"{API}/orders", 201, req="sha256:order-globex")
        r.net("GET", f"{API}/orders/{{p0}}", 200, params={"p0": oid}, resp=f"sha256:o{oid}")
        r.click("Done")
        runs.append(r.trace())
    expected = [
        *PREFIX_EXPECTED,
        (tok("click", "button", "Dismiss", NEW), "conditional"),
        (tok("network", url=f"{API}/session/refresh"), "conditional"),
        (tok("input", "textbox", "Customer name", NEW), "invariant"),
        (tok("click", "button", "Create order", NEW), "invariant"),
        (tok("network", url=f"{API}/orders"), "invariant"),
        (tok("network", url=f"{API}/orders/{{p0}}"), "parameterized"),
        (tok("click", "button", "Done", NEW), "invariant"),
    ]
    return (
        "s5_singleton",
        "Session-expired banner dismissed in exactly one of three runs.",
        runs,
        expected,
        "Singleton variants feed the Good-Turing estimator downstream; here they must simply "
        "classify conditional, never invariant.",
    )


def s6_volatile_ids() -> Scenario:
    sessions = ["abc123", "zzz999"]
    stamps = ["2026-08-07T10:00:00Z", "2026-08-14T14:30:00Z"]
    order_labels = ["Open order ORD-20260807-1432", "Open order ORD-20260814-2119"]
    po_numbers = ["PO-7781", "PO-9902"]
    order_ids = ["48270", "48271"]
    runs: list[RunTrace] = []
    for i in range(2):
        orders_page = f"https://orders.acme.test/orders?session={sessions[i]}&tab=items"
        r = R(f"s6-run{i + 1}", start_offset_min=i * 30)
        r.navigate(LOGIN)
        r.fill("Username", "opsuser", frame=LOGIN)
        r.click("Sign in", frame=LOGIN)
        r.net("POST", f"{API}/login", 200, req="sha256:login-body", resp="sha256:login-ok")
        r.navigate(orders_page)
        r.net("GET", f"{API}/orders", 200, params={"since": stamps[i]}, resp=f"sha256:list{i}")
        r.click(order_labels[i], frame=orders_page)
        r.fill("PO number", po_numbers[i], frame=orders_page)
        r.click("Save changes", frame=orders_page)
        r.net(
            "PUT",
            f"{API}/orders/{{p0}}",
            200,
            params={"p0": order_ids[i]},
            req=f"sha256:save-{po_numbers[i]}",
        )
        runs.append(r.trace())
    norm_page = "https://orders.acme.test/orders?tab={tab}"
    expected = [
        (tok("navigate", url=LOGIN), "invariant"),
        (tok("input", "textbox", "Username", LOGIN), "invariant"),
        (tok("click", "button", "Sign in", LOGIN), "invariant"),
        (tok("network", url=f"{API}/login"), "invariant"),
        (tok("navigate", url=norm_page), "invariant"),
        (tok("network", url=f"{API}/orders"), "invariant"),
        (tok("click", "button", "Open order ORD-{num}-{num}", norm_page), "invariant"),
        (tok("input", "textbox", "PO number", norm_page), "parameterized"),
        (tok("click", "button", "Save changes", norm_page), "invariant"),
        (tok("network", url=f"{API}/orders/{{p0}}"), "parameterized"),
    ]
    return (
        "s6_volatile_ids",
        "Session ids in frame URLs, timestamps in query params, order numbers in accessible "
        "names: identical process that naive canonicalization would over-segment.",
        runs,
        expected,
        "PO number stays parameterized: the normalizer must not strip genuine parameters.",
    )


def s7_event_drops() -> Scenario:
    runs: list[RunTrace] = []
    for i in range(1, 3):
        r = R(f"s7-run{i}", start_offset_min=(i - 1) * 30)
        prefix(r)
        r.fill("Customer name", "Globex Corp")
        r.fill("Quantity", "10")
        r.key("Enter")
        r.click("Create order")
        r.net("POST", f"{API}/orders", 201, req="sha256:order-globex")
        if i == 1:  # run 2 simulates a dropped capture of this event
            r.net("GET", f"{API}/orders/{{p0}}", 200, params={"p0": "48300"}, resp="sha256:o")
        r.click("Done")
        runs.append(r.trace())
    expected = [
        *PREFIX_EXPECTED,
        (tok("input", "textbox", "Customer name", NEW), "invariant"),
        (tok("input", "textbox", "Quantity", NEW), "invariant"),
        (tok("key", name="Enter"), "invariant"),
        (tok("click", "button", "Create order", NEW), "invariant"),
        (tok("network", url=f"{API}/orders"), "invariant"),
        (tok("network", url=f"{API}/orders/{{p0}}"), "conditional"),
        (tok("click", "button", "Done", NEW), "invariant"),
    ]
    return (
        "s7_event_drops",
        "Identical process; one run is missing a single event (simulated capture drop).",
        runs,
        expected,
        "At evidence level a dropped event is indistinguishable from a conditional, so the "
        "expected class is conditional; the defect seeded here is misalignment of neighbors.",
    )


def s8_frame_near_dup() -> Scenario:
    pay_frame = "https://pay.acme.test/widget?merchant=acme"
    runs: list[RunTrace] = []
    for i in range(1, 3):
        r = R(f"s8-run{i}", start_offset_min=(i - 1) * 30)
        prefix(r)
        r.click("Confirm payment")  # main frame
        r.fill("Card holder", "Operations Desk", frame=pay_frame)
        r.click("Confirm payment", frame=pay_frame)  # near-duplicate, iframe
        r.net("POST", "https://pay.acme.test/api/charge", 201, req="sha256:charge")
        r.click("Done")
        runs.append(r.trace())
    norm_pay = "https://pay.acme.test/widget?merchant={merchant}"
    expected = [
        *PREFIX_EXPECTED,
        (tok("click", "button", "Confirm payment", NEW), "invariant"),
        (tok("input", "textbox", "Card holder", norm_pay), "invariant"),
        (tok("click", "button", "Confirm payment", norm_pay), "invariant"),
        (tok("network", url="https://pay.acme.test/api/charge"), "invariant"),
        (tok("click", "button", "Done", NEW), "invariant"),
    ]
    return (
        "s8_frame_near_dup",
        "Two 'Confirm payment' buttons identical in role and name, differing only in frame.",
        runs,
        expected,
        "Tokens must keep frame context: collapsing the two near-duplicates corrupts alignment.",
    )


def main() -> None:
    scenarios = [
        s1_param_single(),
        s2_conditional(),
        s3_repeated_steps(),
        s4_unordered(),
        s5_singleton(),
        s6_volatile_ids(),
        s7_event_drops(),
        s8_frame_near_dup(),
    ]
    if OUT.exists():
        shutil.rmtree(OUT)
    total_runs = 0
    for name, description, runs, expected, notes in scenarios:
        d = OUT / name
        d.mkdir(parents=True)
        filenames = []
        for trace in runs:
            fn = f"{trace.run_id}.ndjson"
            with (d / fn).open("w", encoding="utf-8") as f:
                for e in trace.events:
                    f.write(json.dumps(e.model_dump(mode="json"), sort_keys=True) + "\n")
            filenames.append(fn)
            total_runs += 1
        manifest = {
            "scenario": name,
            "description": description,
            "runs": filenames,
            "expected_columns": [{"token": t, "class": c} for t, c in expected],
            "notes": notes,
        }
        (d / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"{name}: {len(runs)} runs, {len(expected)} expected columns")
    print(f"total RunTrace fixtures: {total_runs}")


if __name__ == "__main__":
    main()
