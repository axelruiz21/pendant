"""SQLite store with sequential SQL migrations and a blob directory.

Evidence is immutable (invariant 1): events and traces are insert-only
(the API exposes no update or delete for them), IR envelopes are
versioned rows, and review/lifecycle state changes are an append-only
attributed log rather than column updates. Blobs are content-addressed
by SHA-256 and verified on write.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from pendant.capture.schema import Event, RunTrace
from pendant.ir.models import (
    CoverageEstimate,
    LifecycleState,
    ProcessEnvelope,
    ReviewState,
)
from pendant.store.coverage import good_turing_coverage

DEFAULT_UNSEEN_MASS_THRESHOLD = 0.10

MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE processes (
            process_id TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE runs (
            run_id      TEXT PRIMARY KEY,
            process_id  TEXT NOT NULL REFERENCES processes(process_id),
            created_at  TEXT NOT NULL,
            event_count INTEGER NOT NULL
        );
        CREATE TABLE events (
            run_id     TEXT NOT NULL REFERENCES runs(run_id),
            seq        INTEGER NOT NULL,
            event_id   TEXT NOT NULL,
            kind       TEXT NOT NULL,
            tier       INTEGER NOT NULL,
            event_json TEXT NOT NULL,
            PRIMARY KEY (run_id, seq)
        );
        CREATE TABLE ir_versions (
            process_id    TEXT NOT NULL REFERENCES processes(process_id),
            version       INTEGER NOT NULL,
            envelope_json TEXT NOT NULL,
            created_at    TEXT NOT NULL,
            PRIMARY KEY (process_id, version)
        );
        CREATE TABLE state_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            process_id      TEXT NOT NULL REFERENCES processes(process_id),
            version         INTEGER NOT NULL,
            review_state    TEXT NOT NULL,
            lifecycle_state TEXT NOT NULL,
            actor           TEXT NOT NULL,
            at              TEXT NOT NULL,
            detail          TEXT NOT NULL
        );
        """,
    ),
]


class PromotionRefused(Exception):
    """Raised when the Good-Turing gate blocks leaving `draft`."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.blob_root = root / "blobs" / "sha256"
        root.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(root / "pendant.db")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def close(self) -> None:
        self._conn.close()

    def _migrate(self) -> None:
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {
            row[0] for row in self._conn.execute("SELECT version FROM schema_migrations")
        }
        for version, sql in MIGRATIONS:
            if version in applied:
                continue
            self._conn.executescript(sql)
            self._conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, _now()),
            )
            self._conn.commit()

    # -- blobs ---------------------------------------------------------------

    def put_blob(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        path = self.blob_root / digest[:2] / digest[2:4] / digest
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"blob store corruption at {path}")
        return f"sha256:{digest}"

    def get_blob(self, ref: str) -> bytes:
        digest = ref.removeprefix("sha256:")
        path = self.blob_root / digest[:2] / digest[2:4] / digest
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise RuntimeError(f"blob store corruption at {path}")
        return data

    # -- processes and runs ----------------------------------------------------

    def create_process(self, process_id: str, name: str) -> None:
        self._conn.execute(
            "INSERT INTO processes (process_id, name, created_at) VALUES (?, ?, ?)",
            (process_id, name, _now()),
        )
        self._conn.commit()

    def list_processes(self) -> list[tuple[str, str]]:
        rows = self._conn.execute(
            "SELECT process_id, name FROM processes ORDER BY process_id"
        )
        return [(r[0], r[1]) for r in rows]

    def add_run(self, process_id: str, trace: RunTrace, blob_dir: Path | None = None) -> None:
        """Ingest one demonstration. Blobs referenced by events are copied in."""
        if blob_dir is not None:
            for blob in sorted(blob_dir.glob("*")):
                if blob.is_file():
                    self.put_blob(blob.read_bytes())
        with self._conn:
            self._conn.execute(
                "INSERT INTO runs (run_id, process_id, created_at, event_count) "
                "VALUES (?, ?, ?, ?)",
                (trace.run_id, process_id, _now(), len(trace.events)),
            )
            self._conn.executemany(
                "INSERT INTO events (run_id, seq, event_id, kind, tier, event_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        trace.run_id,
                        e.seq,
                        e.event_id,
                        e.kind,
                        e.tier,
                        json.dumps(e.model_dump(mode="json"), sort_keys=True),
                    )
                    for e in trace.events
                ],
            )

    def list_runs(self, process_id: str) -> list[tuple[str, str, int]]:
        rows = self._conn.execute(
            "SELECT run_id, created_at, event_count FROM runs "
            "WHERE process_id = ? ORDER BY created_at, run_id",
            (process_id,),
        )
        return [(r[0], r[1], r[2]) for r in rows]

    def get_trace(self, run_id: str) -> RunTrace:
        rows = self._conn.execute(
            "SELECT event_json FROM events WHERE run_id = ? ORDER BY seq", (run_id,)
        ).fetchall()
        if not rows:
            raise KeyError(f"no such run: {run_id}")
        events = [Event.model_validate_json(r[0]) for r in rows]
        return RunTrace(run_id=run_id, events=events)

    def get_traces(self, process_id: str) -> list[RunTrace]:
        return [self.get_trace(run_id) for run_id, _, _ in self.list_runs(process_id)]

    # -- coverage (invariant 9) ---------------------------------------------------

    def coverage(self, process_id: str) -> CoverageEstimate:
        return good_turing_coverage(self.get_traces(process_id))

    # -- IR envelopes and state -----------------------------------------------------

    def save_envelope(self, envelope: ProcessEnvelope) -> None:
        """Persist a new IR version. Versions are append-only."""
        exists = self._conn.execute(
            "SELECT 1 FROM ir_versions WHERE process_id = ? AND version = ?",
            (envelope.process_id, envelope.version),
        ).fetchone()
        if exists:
            raise ValueError(
                f"version {envelope.version} of {envelope.process_id} already stored; "
                "IR versions are append-only"
            )
        with self._conn:
            self._conn.execute(
                "INSERT INTO ir_versions (process_id, version, envelope_json, created_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    envelope.process_id,
                    envelope.version,
                    envelope.model_dump_json(),
                    _now(),
                ),
            )
            self._conn.execute(
                "INSERT INTO state_log (process_id, version, review_state, "
                "lifecycle_state, actor, at, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    envelope.process_id,
                    envelope.version,
                    envelope.review_state,
                    envelope.lifecycle_state,
                    "system:save",
                    _now(),
                    "{}",
                ),
            )

    def latest_version(self, process_id: str) -> int | None:
        row = self._conn.execute(
            "SELECT MAX(version) FROM ir_versions WHERE process_id = ?", (process_id,)
        ).fetchone()
        return row[0] if row and row[0] is not None else None

    def get_envelope(self, process_id: str, version: int | None = None) -> ProcessEnvelope:
        """Load an envelope with current state and coverage overlaid."""
        if version is None:
            version = self.latest_version(process_id)
            if version is None:
                raise KeyError(f"no IR stored for process {process_id}")
        row = self._conn.execute(
            "SELECT envelope_json FROM ir_versions WHERE process_id = ? AND version = ?",
            (process_id, version),
        ).fetchone()
        if row is None:
            raise KeyError(f"no IR version {version} for process {process_id}")
        state = self._conn.execute(
            "SELECT review_state, lifecycle_state FROM state_log "
            "WHERE process_id = ? AND version = ? ORDER BY id DESC LIMIT 1",
            (process_id, version),
        ).fetchone()
        raw = json.loads(row[0])
        raw["review_state"], raw["lifecycle_state"] = state[0], state[1]
        raw["coverage_estimate"] = self.coverage(process_id).model_dump(mode="json")
        return ProcessEnvelope.model_validate(raw)

    def promote(
        self,
        process_id: str,
        version: int,
        new_state: ReviewState,
        actor: str,
        *,
        unseen_mass_threshold: float = DEFAULT_UNSEEN_MASS_THRESHOLD,
        lifecycle_state: LifecycleState = "active",
    ) -> ProcessEnvelope:
        """Advance review state; refuses to leave draft under-covered."""
        current = self.get_envelope(process_id, version)
        order: list[ReviewState] = ["draft", "reviewed", "approved"]
        if order.index(new_state) != order.index(current.review_state) + 1:
            raise ValueError(
                f"illegal review transition {current.review_state} -> {new_state}"
            )
        coverage = self.coverage(process_id)
        if new_state != "draft" and coverage.unseen_mass > unseen_mass_threshold:
            raise PromotionRefused(
                f"estimated unseen variant mass {coverage.unseen_mass:.2f} exceeds "
                f"threshold {unseen_mass_threshold:.2f}; capture more demonstrations "
                f"(runs={coverage.runs}, singleton variants="
                f"{coverage.singleton_variants})"
            )
        self._conn.execute(
            "INSERT INTO state_log (process_id, version, review_state, "
            "lifecycle_state, actor, at, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                process_id,
                version,
                new_state,
                lifecycle_state,
                actor,
                _now(),
                json.dumps({"unseen_mass": coverage.unseen_mass}),
            ),
        )
        self._conn.commit()
        return self.get_envelope(process_id, version)
