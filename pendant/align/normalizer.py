"""Canonicalization: Event -> comparison token, with a configurable ruleset.

The token is (kind, role, normalized_name, url) per CLAUDE.md Part II.
Volatile substrings (timestamps, generated identifiers, session ids)
are stripped by data-driven rules so that identical steps compare
equal across runs, while genuine parameters stay visible to the
payload identity used by the classifier.

Two distinct URL treatments, both rule-driven:

- token URL (`normalize_url_token`): volatile query params dropped,
  remaining query values templatized (?tab={tab}), volatile path
  segments templatized -> defines *which step this is*;
- payload URL (`normalize_url_payload`): volatile query params
  dropped, everything else literal -> defines *what it was applied
  to*, so /orders/48219 vs /orders/48220 is the same step with a
  varying parameter.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict

from pendant.capture.schema import Event

# Order matters: earlier rules run first.
DEFAULT_NAME_RULES: list[tuple[str, str]] = [
    (r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", "{uuid}"),
    (r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?", "{ts}"),
    (r"\b\d{1,2}:\d{2}(?::\d{2})?\b", "{time}"),
    (r"\b[0-9a-fA-F]{16,}\b", "{hex}"),
    (r"\d{4,}", "{num}"),
]

DEFAULT_VOLATILE_QUERY_PARAMS: frozenset[str] = frozenset(
    {
        "session",
        "sessionid",
        "sid",
        "token",
        "auth",
        "ts",
        "timestamp",
        "since",
        "t",
        "_",
        "cb",
        "nonce",
        "state",
    }
)


class NormalizerRules(BaseModel):
    """Configurable ruleset; the defaults are data, not architecture."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name_rules: tuple[tuple[str, str], ...] = tuple(DEFAULT_NAME_RULES)
    volatile_query_params: frozenset[str] = DEFAULT_VOLATILE_QUERY_PARAMS


class CanonToken(NamedTuple):
    kind: str
    role: str
    name: str
    url: str

    def __str__(self) -> str:
        return f"{self.kind}|{self.role}|{self.name}|{self.url}"


def normalize_name(name: str, rules: NormalizerRules) -> str:
    out = name
    for pattern, replacement in rules.name_rules:
        out = re.sub(pattern, replacement, out)
    return re.sub(r"\s+", " ", out).strip()


def _split_url(url: str) -> tuple[str, list[tuple[str, str]]]:
    base, _, fragment_stripped = url.partition("#")
    path, _, query = base.partition("?")
    pairs: list[tuple[str, str]] = []
    if query:
        for piece in query.split("&"):
            k, _, v = piece.partition("=")
            if k:
                pairs.append((k, v))
    del fragment_stripped
    return path, pairs


def normalize_url_token(url: str, rules: NormalizerRules) -> str:
    """URL as step identity: volatile params dropped, values templatized."""
    path, pairs = _split_url(url)
    segments = [normalize_name(seg, rules) for seg in path.split("/")]
    kept = sorted(k for k, _ in pairs if k.lower() not in rules.volatile_query_params)
    out = "/".join(segments)
    if kept:
        out += "?" + "&".join(f"{k}={{{k}}}" for k in kept)
    return out


def normalize_url_payload(url: str, rules: NormalizerRules) -> str:
    """URL as step argument: volatile params dropped, the rest literal."""
    path, pairs = _split_url(url)
    kept = sorted((k, v) for k, v in pairs if k.lower() not in rules.volatile_query_params)
    out = path
    if kept:
        out += "?" + "&".join(f"{k}={v}" for k, v in kept)
    return out


# Tiers/kinds excluded from alignment: narration carries intent, not
# process structure.
NON_ALIGNABLE_KINDS: frozenset[str] = frozenset({"narration"})


def canonical_token(event: Event, rules: NormalizerRules) -> CanonToken | None:
    """Comparison token, or None when the event does not participate in MSA."""
    if event.kind in NON_ALIGNABLE_KINDS or event.tier == 5:
        return None
    role = ""
    name = ""
    url = ""
    if event.target is not None:
        role = event.target.role or ""
        name = normalize_name(event.target.name or "", rules)
        if event.target.frame_url:
            url = normalize_url_token(event.target.frame_url, rules)
    if event.kind == "network" and event.network is not None:
        url = normalize_url_token(event.network.url_template, rules)
    elif event.kind == "navigate" and event.payload is not None and event.payload.value_redacted:
        url = normalize_url_token(event.payload.value_redacted, rules)
    elif event.kind == "key" and event.payload is not None:
        name = "+".join(event.payload.keys)
    elif event.kind == "dialog" and event.payload is not None and event.payload.value_redacted:
        name = normalize_name(event.payload.value_redacted, rules)
    return CanonToken(kind=event.kind, role=role, name=name, url=url)


def payload_key(event: Event, rules: NormalizerRules) -> tuple[object, ...]:
    """Payload identity used to split invariant from parameterized columns.

    Volatile inputs (session params, timestamps in query strings) are
    excluded so they cannot masquerade as parameters; response hashes
    are excluded because the response is the world's reply, not part
    of what the process itself does.
    """
    if event.kind == "network" and event.network is not None:
        n = event.network
        stable_params = tuple(
            sorted(
                (k, v)
                for k, v in n.url_params.items()
                if k.lower() not in rules.volatile_query_params
            )
        )
        return (n.method, stable_params, n.req_sha, n.status)
    if event.kind == "navigate" and event.payload is not None and event.payload.value_redacted:
        return (normalize_url_payload(event.payload.value_redacted, rules),)
    if event.payload is not None:
        return (event.payload.value_redacted, tuple(event.payload.keys))
    return ()
