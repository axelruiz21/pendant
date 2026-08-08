"""run: assisted execution of a reviewed IR graph in a real browser.

Phase 2 pulled forward as a prototype (docs/DECISIONS.md D-017). The
runner executes exactly what the IR licenses and nothing more: every
step's postconditions are awaited within its finite timeout, approval
gates stop for the operator, unknown branches surface their
guard_question instead of guessing, and faults follow the step's
on_fault policy. Gate 3 (shadow mode) is NOT claimed by this module.
"""

from pendant.run.executor import (
    OperatorConsole,
    RunAborted,
    Runner,
    RunReport,
    StdioConsole,
    StepFault,
    StepResult,
)
from pendant.run.resolver import ResolutionError, resolve_target

__all__ = [
    "OperatorConsole",
    "ResolutionError",
    "RunAborted",
    "RunReport",
    "Runner",
    "StdioConsole",
    "StepFault",
    "StepResult",
    "resolve_target",
]
