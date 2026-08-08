"""Redaction registry: executes inside the collector, before any write.

Invariant 3: a trace corpus containing a live credential cannot be
un-leaked, so nothing secret may ever reach disk. Layers:

1. Password-type inputs never leave the browser context: the injected
   content script sends `value: null, secret: true` for them. The
   registry is the second line of defense, not the first.
2. Field-name registry (configurable): input values whose field
   identity (accessible name, testid, id/name attributes,
   autocomplete) matches a secret or PII rule are replaced before the
   event is buffered.
3. Header names (Authorization, Cookie, ...) are never persisted;
   the Event schema has no header field, and request/response body
   hashes are computed over registry-redacted bodies.
4. URL query parameters with secret-looking names are redacted in
   navigate payloads.
5. Screenshot masking selectors are derived from the same registry.
"""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict

REDACTED = "{redacted}"

DEFAULT_SECRET_FIELD_PATTERNS: tuple[str, ...] = (
    r"pass(word|wd|phrase)?",
    r"secret",
    r"(api[-_]?)?token",
    r"api[-_]?key",
    r"auth",
    r"otp",
    r"pin\b",
)

DEFAULT_PII_FIELD_PATTERNS: tuple[str, ...] = (
    r"ssn",
    r"social[-_ ]?security",
    r"(credit[-_ ]?)?card([-_ ]?(number|num|no))?",
    r"\bcc[-_]?(number|num|no)\b",
    r"cvv|cvc",
    r"date[-_ ]?of[-_ ]?birth|\bdob\b|birth",
    r"passport",
    r"iban|routing|account[-_ ]?number",
)

DEFAULT_SECRET_HEADERS: tuple[str, ...] = (
    "authorization",
    "cookie",
    "set-cookie",
    "proxy-authorization",
    "x-api-key",
    "x-auth-token",
)

DEFAULT_SECRET_QUERY_PARAMS: tuple[str, ...] = (
    "token",
    "auth",
    "api_key",
    "apikey",
    "key",
    "secret",
    "password",
    "session",
    "sid",
)

# Substrings (not regexes) used to build screenshot mask selectors;
# kept deliberately broad because over-masking is harmless and
# under-masking is a severity-10 escape.
DEFAULT_MASK_KEYWORDS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "apikey",
    "api-key",
    "auth",
    "otp",
    "pin",
    "ssn",
    "social",
    "card",
    "cvv",
    "cvc",
    "dob",
    "birth",
    "passport",
    "iban",
    "routing",
)


class RedactionRegistry(BaseModel):
    """Configurable PII/secret field registry (invariant 3)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    secret_field_patterns: tuple[str, ...] = DEFAULT_SECRET_FIELD_PATTERNS
    pii_field_patterns: tuple[str, ...] = DEFAULT_PII_FIELD_PATTERNS
    secret_headers: tuple[str, ...] = DEFAULT_SECRET_HEADERS
    secret_query_params: tuple[str, ...] = DEFAULT_SECRET_QUERY_PARAMS
    mask_keywords: tuple[str, ...] = DEFAULT_MASK_KEYWORDS

    def match_field(self, *identity_strings: str | None) -> str | None:
        """Return the rule id matching a field identity, or None."""
        haystacks = [s.lower() for s in identity_strings if s]
        for kind, patterns in (
            ("secret", self.secret_field_patterns),
            ("pii", self.pii_field_patterns),
        ):
            for pattern in patterns:
                for haystack in haystacks:
                    if re.search(pattern, haystack):
                        return f"{kind}:{pattern}"
        return None

    def redact_url(self, url: str) -> tuple[str, list[str]]:
        """Replace values of secret-looking query params."""
        parts = urlsplit(url)
        if not parts.query:
            return url, []
        redactions: list[str] = []
        pairs: list[tuple[str, str]] = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if key.lower() in self.secret_query_params:
                pairs.append((key, REDACTED))
                redactions.append(f"url-param:{key}")
            else:
                pairs.append((key, value))
        redacted = urlunsplit(parts._replace(query=urlencode(pairs)))
        return redacted, redactions

    def redact_body(self, body: str, content_type: str | None) -> tuple[str, list[str]]:
        """Redact registry-matching values inside a request/response body.

        Applied BEFORE the body hash is computed, so the persisted hash
        is a hash of the redacted body and cannot leak by dictionary
        attack against short secrets.
        """
        ct = (content_type or "").lower()
        if "json" in ct:
            try:
                parsed = json.loads(body)
            except ValueError:
                return self._redact_flat_text(body)
            redactions: list[str] = []
            cleaned = self._redact_json(parsed, redactions)
            return json.dumps(cleaned, sort_keys=True), redactions
        if "urlencoded" in ct:
            pairs = parse_qsl(body, keep_blank_values=True)
            redactions = []
            out: list[tuple[str, str]] = []
            for key, value in pairs:
                rule = self.match_field(key)
                if rule:
                    out.append((key, REDACTED))
                    redactions.append(f"body-field:{key}")
                else:
                    out.append((key, value))
            return urlencode(out), redactions
        return self._redact_flat_text(body)

    def _redact_json(self, node: object, redactions: list[str]) -> object:
        if isinstance(node, dict):
            out: dict[str, object] = {}
            for key, value in node.items():
                if isinstance(value, str | int | float) and self.match_field(str(key)):
                    out[key] = REDACTED
                    redactions.append(f"body-field:{key}")
                else:
                    out[key] = self._redact_json(value, redactions)
            return out
        if isinstance(node, list):
            return [self._redact_json(v, redactions) for v in node]
        return node

    def _redact_flat_text(self, body: str) -> tuple[str, list[str]]:
        # Opaque body: cannot address fields; keep it out of the trace
        # entirely rather than risk an escape.
        return REDACTED, ["body-opaque"]

    def mask_selectors(self) -> list[str]:
        """CSS selectors to mask in screenshots, driven by the registry."""
        selectors = ["input[type=password]"]
        for keyword in self.mask_keywords:
            selectors.append(f'[name*="{keyword}" i]')
            selectors.append(f'[id*="{keyword}" i]')
            selectors.append(f'[data-testid*="{keyword}" i]')
            selectors.append(f'[autocomplete*="{keyword}" i]')
        return selectors
