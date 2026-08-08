"""capture: collectors, redaction registry, CDP adapters.

The boundary schema (Event, RunTrace) lives in pendant.capture.schema
and is consumed by store/ and align/. Redaction executes inside the
collector, before any write to disk (invariant 3).
"""

from pendant.capture.schema import Event, NetworkInfo, Payload, RunTrace

__all__ = ["Event", "NetworkInfo", "Payload", "RunTrace"]
