"""store: SQLite metadata + content-addressed blobs, append-only.

Process -> Runs -> Events, versioned, migrations from day one.
Enforces the Good-Turing promotion gate (invariant 9): a process
cannot leave `draft` while estimated unseen variant mass exceeds the
configured threshold.
"""

from pendant.store.coverage import good_turing_coverage
from pendant.store.db import PromotionRefused, Store

__all__ = ["PromotionRefused", "Store", "good_turing_coverage"]
