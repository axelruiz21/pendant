"""Registry coverage for pendant/capture/redaction.py (invariant 3).

The RedactionRegistry runs inside the collector before any write, and
the FMEA rates a redaction escape severity 10. These tests pin the
registry contract without the browser-dependent Gate 0 harness:
match_field over every default rule, URL/body redaction, screenshot
mask selectors, and capture-time URL templatization — plus the
blank-hotkey screenshot race, referenced-blob-only store ingestion,
and the InducedStep invariant-15 mirror.
"""

import asyncio
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pytest
from pydantic import ValidationError

from pendant.capture.collector import Collector
from pendant.capture.redaction import (
    DEFAULT_MASK_KEYWORDS,
    DEFAULT_SECRET_QUERY_PARAMS,
    REDACTED,
    RedactionRegistry,
)
from pendant.capture.schema import Event, Payload, RunTrace
from pendant.capture.templatize import templatize_url
from pendant.induce.schema import InducedStep
from pendant.ir.models import TargetVector
from pendant.store import Store

REGISTRY = RedactionRegistry()

# At least one field name per default secret pattern.
SECRET_FIELD_NAMES = (
    "password",
    "passwd",
    "passphrase",
    "pass",
    "client_secret",
    "token",
    "api_token",
    "api-token",
    "apitoken",
    "api_key",
    "apikey",
    "api-key",
    "auth",
    "authorization",
    "otp",
    "pin",
)

# At least one field name per default PII pattern. "passport" resolves
# to the broad secret "pass" rule first; what matters is that it matches.
PII_FIELD_NAMES = (
    "ssn",
    "social_security",
    "social security",
    "credit_card",
    "credit card number",
    "card_number",
    "card-num",
    "card no",
    "card",
    "cc_number",
    "cc-num",
    "cc_no",
    "cvv",
    "cvc",
    "date_of_birth",
    "date-of-birth",
    "dob",
    "birthdate",
    "passport",
    "iban",
    "routing",
    "account_number",
)

BENIGN_FIELD_NAMES = (
    "username",
    "email",
    "search",
    "quantity",
    "first-name",
    "city",
    "message",
)


def query_params(url: str) -> dict[str, str]:
    return dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))


class TestMatchField:
    @pytest.mark.parametrize("field", SECRET_FIELD_NAMES)
    def test_secret_field_matches_secret_rule(self, field: str) -> None:
        rule = REGISTRY.match_field(field)
        assert rule is not None
        assert rule.startswith("secret:")

    @pytest.mark.parametrize("field", PII_FIELD_NAMES)
    def test_pii_field_matches_some_rule(self, field: str) -> None:
        assert REGISTRY.match_field(field) is not None

    @pytest.mark.parametrize(
        "field", ["PASSWORD", "Api-Key", "SSN", "Card Number", "OTP", "Client_Secret"]
    )
    def test_matching_is_case_insensitive(self, field: str) -> None:
        assert REGISTRY.match_field(field) is not None

    @pytest.mark.parametrize("field", BENIGN_FIELD_NAMES)
    def test_benign_field_does_not_match(self, field: str) -> None:
        assert REGISTRY.match_field(field) is None

    def test_any_matching_identity_string_wins(self) -> None:
        rule = REGISTRY.match_field(None, "Email address", "data-password-input")
        assert rule is not None
        assert rule.startswith("secret:")

    def test_all_benign_identity_strings_do_not_match(self) -> None:
        assert REGISTRY.match_field("Full name", None, "shipping-address") is None

    def test_no_identity_strings(self) -> None:
        assert REGISTRY.match_field() is None
        assert REGISTRY.match_field(None, "") is None

    def test_secret_rules_take_precedence_over_pii(self) -> None:
        rule = REGISTRY.match_field("card_token")
        assert rule is not None
        assert rule.startswith("secret:")


class TestRedactUrl:
    @pytest.mark.parametrize("param", DEFAULT_SECRET_QUERY_PARAMS)
    def test_every_default_secret_param_is_redacted(self, param: str) -> None:
        redacted, redactions = REGISTRY.redact_url(f"https://app.test/p?{param}=LIVEVALUE")
        assert "LIVEVALUE" not in redacted
        assert query_params(redacted) == {param: REDACTED}
        assert redactions == [f"url-param:{param}"]

    def test_param_name_matching_is_case_insensitive(self) -> None:
        redacted, redactions = REGISTRY.redact_url("https://app.test/p?TOKEN=LIVEVALUE")
        assert "LIVEVALUE" not in redacted
        assert redactions == ["url-param:TOKEN"]

    def test_non_secret_params_preserved(self) -> None:
        redacted, redactions = REGISTRY.redact_url(
            "https://app.test/search?q=shoes&page=2&token=abc123"
        )
        assert query_params(redacted) == {"q": "shoes", "page": "2", "token": REDACTED}
        assert redactions == ["url-param:token"]
        assert "abc123" not in redacted

    def test_blank_values_kept(self) -> None:
        redacted, redactions = REGISTRY.redact_url("https://app.test/p?note=&page=2")
        assert query_params(redacted) == {"note": "", "page": "2"}
        assert redactions == []

    @pytest.mark.parametrize(
        "url",
        ["https://app.test/a/b", "https://app.test/", "https://app.test/docs#section-2"],
    )
    def test_url_without_query_untouched(self, url: str) -> None:
        assert REGISTRY.redact_url(url) == (url, [])

    def test_registry_pattern_params_redacted(self) -> None:
        # These names miss the exact-name list but match the field
        # registry — the pre-fix behavior leaked them verbatim.
        redacted, redactions = REGISTRY.redact_url(
            "https://app.test/cb?access_token=eyJLIVE&ssn=123-45-6789&card_number=4111"
        )
        for leaked in ("eyJLIVE", "123-45-6789", "4111"):
            assert leaked not in redacted
        assert set(redactions) == {
            "url-param:access_token",
            "url-param:ssn",
            "url-param:card_number",
        }

    def test_oauth_implicit_fragment_redacted(self) -> None:
        redacted, redactions = REGISTRY.redact_url(
            "https://app.test/cb#access_token=SECRETVAL&token_type=bearer"
        )
        assert "SECRETVAL" not in redacted
        assert "url-fragment:access_token" in redactions


class TestRedactBodyJson:
    def test_nested_secret_keys_redacted(self) -> None:
        body = json.dumps({"user": {"name": "amy", "password": "hunter2"}, "count": 2})
        redacted, redactions = REGISTRY.redact_body(body, "application/json")
        assert "hunter2" not in redacted
        assert json.loads(redacted) == {
            "user": {"name": "amy", "password": REDACTED},
            "count": 2,
        }
        assert redactions == ["body-field:password"]

    def test_secret_keys_inside_lists_redacted(self) -> None:
        body = json.dumps({"items": [{"card_number": "4111111111111111", "qty": 1}]})
        redacted, redactions = REGISTRY.redact_body(body, "application/json")
        assert "4111111111111111" not in redacted
        assert json.loads(redacted) == {"items": [{"card_number": REDACTED, "qty": 1}]}
        assert redactions == ["body-field:card_number"]

    def test_container_value_under_secret_key_fully_redacted(self) -> None:
        # Pre-fix escape: a matching key with a list/dict value leaked
        # its contents because only scalar values were replaced.
        body = json.dumps({"api_key": ["sk-live-123"], "auth": {"bearer": "tok-live"}})
        redacted, redactions = REGISTRY.redact_body(body, "application/json")
        assert "sk-live-123" not in redacted
        assert "tok-live" not in redacted
        assert json.loads(redacted) == {"api_key": REDACTED, "auth": REDACTED}
        assert sorted(redactions) == ["body-field:api_key", "body-field:auth"]

    def test_output_keys_sorted_for_determinism(self) -> None:
        a, _ = REGISTRY.redact_body('{"b": 1, "a": 2}', "application/json")
        b, _ = REGISTRY.redact_body('{"a": 2, "b": 1}', "application/json")
        assert a == b == '{"a": 2, "b": 1}'

    def test_malformed_json_falls_through_to_opaque(self) -> None:
        redacted, redactions = REGISTRY.redact_body('{"password": "hunt', "application/json")
        assert redacted == REDACTED
        assert redactions == ["body-opaque"]

    def test_scalar_json_body_is_opaque(self) -> None:
        # Pre-fix escape: a top-level JSON string has no field identity
        # to address and used to pass through verbatim.
        redacted, redactions = REGISTRY.redact_body('"sk-live-topsecret"', "application/json")
        assert redacted == REDACTED
        assert redactions == ["body-opaque"]

    def test_content_type_with_charset_still_json(self) -> None:
        body = json.dumps({"token": "tok-live"})
        redacted, _ = REGISTRY.redact_body(body, "application/json; charset=utf-8")
        assert "tok-live" not in redacted


class TestRedactBodyUrlencoded:
    CT = "application/x-www-form-urlencoded"

    def test_secret_fields_redacted_others_preserved(self) -> None:
        redacted, redactions = REGISTRY.redact_body(
            "username=amy&password=hunter2&remember=on", self.CT
        )
        assert "hunter2" not in redacted
        assert dict(parse_qsl(redacted, keep_blank_values=True)) == {
            "username": "amy",
            "password": REDACTED,
            "remember": "on",
        }
        assert redactions == ["body-field:password"]

    def test_field_patterns_apply_to_body_keys(self) -> None:
        redacted, redactions = REGISTRY.redact_body("access_token=eyJLIVE&ssn=123", self.CT)
        assert "eyJLIVE" not in redacted
        assert "123" not in dict(parse_qsl(redacted)).values()
        assert sorted(redactions) == ["body-field:access_token", "body-field:ssn"]

    def test_blank_values_kept(self) -> None:
        redacted, redactions = REGISTRY.redact_body("note=&token=", self.CT)
        assert dict(parse_qsl(redacted, keep_blank_values=True)) == {
            "note": "",
            "token": REDACTED,
        }
        assert redactions == ["body-field:token"]


class TestRedactBodyOpaque:
    @pytest.mark.parametrize("content_type", ["text/plain", "application/octet-stream", None])
    def test_opaque_body_fully_replaced(self, content_type: str | None) -> None:
        redacted, redactions = REGISTRY.redact_body("password=hunter2", content_type)
        assert redacted == REDACTED
        assert redactions == ["body-opaque"]


class TestMaskSelectors:
    def test_password_input_always_masked(self) -> None:
        assert "input[type=password]" in REGISTRY.mask_selectors()

    def test_every_keyword_yields_all_attribute_selectors(self) -> None:
        selectors = set(REGISTRY.mask_selectors())
        for keyword in DEFAULT_MASK_KEYWORDS:
            for attr in ("name", "id", "data-testid", "autocomplete"):
                assert f'[{attr}*="{keyword}" i]' in selectors

    def test_selectors_well_formed(self) -> None:
        pattern = re.compile(r'^\[(name|id|data-testid|autocomplete)\*="[a-z0-9-]+" i\]$')
        selectors = REGISTRY.mask_selectors()
        assert selectors[0] == "input[type=password]"
        for selector in selectors[1:]:
            assert pattern.match(selector), selector
        assert len(selectors) == 1 + 4 * len(DEFAULT_MASK_KEYWORDS)


class TestTemplatizeUrl:
    def test_numeric_segment(self) -> None:
        template, params = templatize_url("https://app.example.test/api/orders/48219")
        assert template == "https://app.example.test/api/orders/{p0}"
        assert params == {"p0": "48219"}

    def test_uuid_segment(self) -> None:
        uid = "123e4567-e89b-12d3-a456-426614174000"
        template, params = templatize_url(f"https://app.example.test/users/{uid}/cart")
        assert template == "https://app.example.test/users/{p0}/cart"
        assert params == {"p0": uid}

    def test_long_hex_segment(self) -> None:
        token = "deadbeefcafebabe0123"
        template, params = templatize_url(f"https://app.example.test/sessions/{token}")
        assert template == "https://app.example.test/sessions/{p0}"
        assert params == {"p0": token}

    def test_short_hex_and_words_preserved(self) -> None:
        template, params = templatize_url("https://app.example.test/api/v2/deadbeef/report")
        assert template == "https://app.example.test/api/v2/deadbeef/report"
        assert params == {}

    def test_multiple_volatile_segments_numbered_in_order(self) -> None:
        template, params = templatize_url("https://app.example.test/orders/7/items/9001")
        assert template == "https://app.example.test/orders/{p0}/items/{p1}"
        assert params == {"p0": "7", "p1": "9001"}

    def test_query_lifted_into_params_and_removed_from_template(self) -> None:
        template, params = templatize_url(
            "https://app.example.test/api/orders/48219?expand=items&note="
        )
        assert template == "https://app.example.test/api/orders/{p0}"
        assert params == {"p0": "48219", "expand": "items", "note": ""}

    def test_relative_url(self) -> None:
        template, params = templatize_url("/api/users/42")
        assert template == "/api/users/{p0}"
        assert params == {"p0": "42"}

    def test_stable_url_unchanged(self) -> None:
        template, params = templatize_url("https://app.example.test/login")
        assert template == "https://app.example.test/login"
        assert params == {}

    def test_redacted_url_templatizes_without_leaking(self) -> None:
        # Collector order: redact_url first, then templatize; the secret
        # must be gone from both template and lifted params.
        redacted, _ = REGISTRY.redact_url("https://app.test/cb/555?token=LIVEVALUE&q=x")
        template, params = templatize_url(redacted)
        assert template == "https://app.test/cb/{p0}"
        assert params == {"p0": "555", "token": REDACTED, "q": "x"}


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
