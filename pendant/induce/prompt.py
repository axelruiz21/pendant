"""Prompt assembly for the inductor.

Inputs, per CLAUDE.md Part II: AlignmentReport + narration transcript
+ IR JSON schema + action-type catalog. The prompt encodes the
non-negotiables: no guessed guards, explicit null reasons, questions
for every conditional region, system-of-record postconditions
preferred (invariant 11).
"""

from __future__ import annotations

import json
from typing import get_args

from pendant.align.report import AlignmentReport
from pendant.induce.schema import InducedProcess
from pendant.ir.models import ActionType

ACTION_CATALOG: tuple[str, ...] = get_args(ActionType)

RULES = """\
You are converting evidence from multiple demonstrations of a browser
process into a draft automation program. Hard rules:

1. NEVER invent steps, guards, or postconditions the evidence does not
   support. Every step must cite provenance event ids drawn from the
   alignment report cells.
2. Every step needs at least one proposed postcondition. Prefer
   evidence from the network tier (http_status against the system of
   record) over UI appearance (element_visible / text_matches); rate
   UI-only postconditions "weak", system-of-record ones "strong". If
   you cannot propose a defensible postcondition, output a proposal
   with predicate null and a specific stated reason (minimum 15
   characters, never a placeholder).
3. Every conditional column group in the alignment report must produce
   exactly one clarifying question for the operator (an English
   question ending in '?', referencing what varies), and the
   corresponding branch edges must carry guard_question, NOT a guessed
   guard predicate.
4. Parameterized columns become process parameters; name them from the
   narration when possible.
5. timeout_ms must be a finite positive integer chosen per step.
   Steps that write to the system of record are risk "write";
   destructive or non-reversible effects are "irreversible".
6. Unordered column groups may be emitted in the order of the report.
7. Confidence is your honest per-step estimate in [0,1] that the step,
   target, and postcondition are correct as stated.

Respond with ONLY a JSON object valid under the provided output
schema. No prose, no markdown fences.
"""


def build_prompt(report: AlignmentReport, narration: list[str]) -> str:
    sections = [
        RULES,
        "## Output JSON schema\n" + json.dumps(InducedProcess.model_json_schema()),
        "## Action-type catalog\n" + json.dumps(list(ACTION_CATALOG)),
        "## Alignment report (deterministic evidence; do not re-align)\n"
        + report.model_dump_json(),
        "## Operator narration (the only channel carrying intent)\n"
        + (json.dumps(narration) if narration else "(none recorded)"),
    ]
    return "\n\n".join(sections)


def build_retry_prompt(base_prompt: str, previous_output: str, error: str) -> str:
    return (
        base_prompt
        + "\n\n## Your previous output was REJECTED by schema validation\n"
        + "Error:\n"
        + error
        + "\nPrevious output:\n"
        + previous_output[:4000]
        + "\n\nEmit a corrected JSON object. Respond with ONLY the JSON."
    )
