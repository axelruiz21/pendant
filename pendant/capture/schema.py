"""RunTrace / Event: the serialized boundary between capture and everything else.

One RunTrace per demonstration, stored as NDJSON (one Event per line).
Traces are append-only evidence (invariant 1): nothing downstream may
mutate them.

Note on tiers: CLAUDE.md's Event sketch lists tier 1|2|3|4 while also
defining narration as Tier 5; we resolve the inconsistency by allowing
tier 5 for narration events (docs/DECISIONS.md D-011).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pendant.ir.models import TargetVector

EventKind = Literal[
    "navigate",
    "click",
    "input",
    "key",
    "network",
    "focus",
    "clipboard",
    "dialog",
    "download",
    "narration",
]


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value_redacted: str | None = None
    value_class: str | None = None
    keys: list[str] = Field(default_factory=list)


class NetworkInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: str
    url_template: str
    url_params: dict[str, str] = Field(default_factory=dict)
    status: int | None = None
    req_sha: str | None = None
    resp_sha: str | None = None


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    seq: int = Field(ge=0)
    t_mono_ms: float = Field(ge=0)
    t_wall: str  # ISO 8601
    tier: Literal[1, 2, 3, 4, 5]
    kind: EventKind
    target: TargetVector | None = None
    payload: Payload | None = None
    network: NetworkInfo | None = None
    screenshot_ref: str | None = None
    redactions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _kind_consistency(self) -> Event:
        if self.kind == "network" and self.network is None:
            raise ValueError("kind='network' requires the network block")
        if self.kind != "network" and self.network is not None:
            raise ValueError("network block is only valid on kind='network'")
        if self.kind == "narration" and self.tier != 5:
            raise ValueError("narration events are tier 5")
        return self


class RunTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    events: list[Event] = Field(default_factory=list)

    @model_validator(mode="after")
    def _events_belong_and_ordered(self) -> RunTrace:
        for e in self.events:
            if e.run_id != self.run_id:
                raise ValueError(
                    f"event {e.event_id!r} carries run_id {e.run_id!r}, expected {self.run_id!r}"
                )
        seqs = [e.seq for e in self.events]
        if seqs != sorted(seqs) or len(set(seqs)) != len(seqs):
            raise ValueError("event seq values must be strictly increasing")
        return self


def load_run_trace(path: Path) -> RunTrace:
    """Load one NDJSON trace file (one Event per line)."""
    events = [
        Event.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not events:
        raise ValueError(f"empty trace file: {path}")
    return RunTrace(run_id=events[0].run_id, events=events)


def save_run_trace(trace: RunTrace, path: Path) -> None:
    """Write one NDJSON trace file. Refuses to overwrite (invariant 1)."""
    if path.exists():
        raise FileExistsError(f"trace already exists, evidence is immutable: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for e in trace.events:
            f.write(json.dumps(e.model_dump(mode="json"), sort_keys=True) + "\n")
