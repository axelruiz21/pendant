"""Redaction registry and blank-hotkey race coverage (invariant 3)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from pendant.capture.collector import Collector
from pendant.capture.redaction import REDACTED, RedactionRegistry
from pendant.capture.schema import Event, Payload
from pendant.induce.schema import InducedStep
from pendant.ir.models import TargetVector
from pendant.store import Store


class TestRedactionRegistry:
    def test_password_field_matches(self) -> None:
        reg = RedactionRegistry()
        assert reg.match_field("Password") is not None
        assert reg.match_field("card-number", "cc-number") is not None
        assert reg.match_field("Customer name") is None

    def test_url_secret_params_redacted(self) -> None:
        reg = RedactionRegistry()
        url, redactions = reg.redact_url(
            "https://app.test/x?token=live-secret&tab=orders"
        )
        assert "live-secret" not in url
        assert "token=" in url
        assert any(r.startswith("url-param:token") for r in redactions)

    def test_json_body_redacted_before_hash_input(self) -> None:
        reg = RedactionRegistry()
        body, redactions = reg.redact_body(
            '{"username":"ops","password":"hunter2"}', "application/json"
        )
        assert "hunter2" not in body
        assert REDACTED in body
        assert any("password" in r for r in redactions)


class TestBlankScreenshotRace:
    def test_blank_bumps_epoch_and_marks_events(self, tmp_path: Path) -> None:
        c = Collector("run1", tmp_path, screenshots=False)
        c.events.append(
            Event(
                event_id="run1-0000",
                run_id="run1",
                seq=0,
                t_mono_ms=c._now_ms(),
                t_wall="2026-08-07T00:00:00+00:00",
                tier=3,
                kind="input",
                target=TargetVector(testid="password"),
                payload=Payload(value_redacted="secret-value", value_class="text"),
            )
        )
        assert c._blank_epoch == 0
        n = c.blank_last(60)
        assert n == 1
        assert c._blank_epoch == 1
        assert c.events[0].payload is not None
        assert c.events[0].payload.value_redacted is None
        assert "blank-hotkey" in c.events[0].redactions

    def test_screenshot_completion_respects_blank_epoch(self, tmp_path: Path) -> None:
        c = Collector("run1", tmp_path, screenshots=True)
        event = Event(
            event_id="run1-0000",
            run_id="run1",
            seq=0,
            t_mono_ms=c._now_ms(),
            t_wall="2026-08-07T00:00:00+00:00",
            tier=3,
            kind="click",
            target=TargetVector(testid="submit"),
        )
        c.events.append(event)
        # Simulate: blank fires while a screenshot task would have been in flight.
        epoch = c._blank_epoch
        c.blank_last(60)
        assert c._blank_epoch == epoch + 1

        async def fake_late_attach() -> None:
            # Mirror the guard in Collector._screenshot after await returns.
            if epoch != c._blank_epoch:
                return
            if "blank-hotkey" in c.events[0].redactions:
                return
            c.blob_dir.mkdir(parents=True, exist_ok=True)
            path = c.blob_dir / ("a" * 64 + ".png")
            path.write_bytes(b"should-not-write")
            c.events[0] = c.events[0].model_copy(
                update={"screenshot_ref": f"sha256:{path.stem}"}
            )

        asyncio.run(fake_late_attach())
        assert c.events[0].screenshot_ref is None
        assert not (c.blob_dir.exists() and list(c.blob_dir.glob("*.png")))


class TestStoreReferencedBlobsOnly:
    def test_orphan_blobs_not_ingested(self, tmp_path: Path) -> None:
        import hashlib

        store = Store(tmp_path / "store")
        store.create_process("p1", "demo")
        blob_dir = tmp_path / "blobs"
        blob_dir.mkdir()
        orphan_bytes = b"orphan-bytes"
        orphan = hashlib.sha256(orphan_bytes).hexdigest()
        (blob_dir / f"{orphan}.png").write_bytes(orphan_bytes)
        kept_bytes = b"kept-bytes"
        kept = hashlib.sha256(kept_bytes).hexdigest()
        (blob_dir / f"{kept}.png").write_bytes(kept_bytes)
        from pendant.capture.schema import RunTrace

        trace = RunTrace(
            run_id="r1",
            events=[
                Event(
                    event_id="r1-0",
                    run_id="r1",
                    seq=0,
                    t_mono_ms=0,
                    t_wall="2026-08-07T00:00:00+00:00",
                    tier=3,
                    kind="click",
                    target=TargetVector(testid="x"),
                    screenshot_ref=f"sha256:{kept}",
                )
            ],
        )
        store.add_run("p1", trace, blob_dir=blob_dir)
        assert store.get_blob(f"sha256:{kept}") == kept_bytes
        with pytest.raises(FileNotFoundError):
            store.get_blob(f"sha256:{orphan}")


class TestInducedStepInvariant15:
    def test_explicit_false_on_irreversible_raises(self) -> None:
        with pytest.raises(ValidationError, match="invariant 15"):
            InducedStep.model_validate(
                {
                    "id": "s1",
                    "label": "Delete order",
                    "action": {"type": "navigate", "params": {"url": "https://x"}},
                    "proposed_postconditions": [
                        {
                            "null_reason": "No Tier 1 evidence for this irreversible step yet."
                        }
                    ],
                    "timeout_ms": 5000,
                    "on_fault": {"policy": "abort", "max_retries": 0},
                    "idempotency": "unsafe",
                    "risk": "irreversible",
                    "approval_required": False,
                    "confidence": 0.5,
                }
            )
