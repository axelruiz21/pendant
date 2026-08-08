"""Graph operations over the IR: traversal order and a printable rendering.

The rendering is a readable artifact per Part VI: if the tool
disappears, the printed graph still reads as an unambiguous process
description.
"""

from __future__ import annotations

from collections import deque

from pendant.ir.models import IRGraph, Predicate, ProcessEnvelope, Step


def traversal_order(graph: IRGraph) -> list[str]:
    """Deterministic BFS order from entry (all steps are reachable by schema)."""
    adjacency: dict[str, list[str]] = {s.id: [] for s in graph.steps}
    for e in graph.edges:
        adjacency[e.from_step].append(e.to_step)
    order: list[str] = []
    seen: set[str] = set()
    queue: deque[str] = deque([graph.entry])
    while queue:
        node = queue.popleft()
        if node in seen:
            continue
        seen.add(node)
        order.append(node)
        queue.extend(sorted(adjacency[node]))
    return order


def _fmt_predicate(p: Predicate) -> str:
    inner = ", ".join(f"{k}={_short(v)}" for k, v in sorted(p.args.items()))
    text = f"{p.kind}({inner})"
    return f"NOT {text}" if p.negate else text


def _short(value: object) -> str:
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


def _fmt_step(step: Step) -> list[str]:
    lines = [f"[{step.id}] {step.label}"]
    target = step.action.target_vector
    target_desc = ""
    if target is not None:
        best = (
            (f"role={target.role!r} name={target.name!r}" if target.role and target.name else None)
            or (f"testid={target.testid!r}" if target.testid else None)
            or (f"css={target.css!r}" if target.css else None)
            or f"attrs={target.attrs!r}"
        )
        target_desc = f" -> {best}"
    lines.append(f"    action: {step.action.type}{target_desc}")
    if step.action.params:
        lines.append(f"    params: {_short(step.action.params)}")
    for p in step.preconditions:
        lines.append(f"    pre:  {_fmt_predicate(p)}")
    for p in step.postconditions:
        weak = "  [flagged weak]" if step.postcondition_strength == "weak" else ""
        lines.append(f"    post: {_fmt_predicate(p)}{weak}")
    lines.append(
        f"    timeout: {step.timeout_ms} ms | on_fault: {step.on_fault.policy}"
        + (f" x{step.on_fault.max_retries}" if step.on_fault.policy == "retry" else "")
        + f" | idempotency: {step.idempotency} | risk: {step.risk}"
        + (" | APPROVAL REQUIRED" if step.approval_required else "")
    )
    lines.append(
        f"    confidence: {step.confidence:.2f} | provenance: {len(step.provenance)} event(s)"
    )
    return lines


def render_text(envelope: ProcessEnvelope) -> str:
    """Render a process envelope as a printable, self-describing document."""
    g = envelope.graph
    out: list[str] = []
    out.append(f"PROCESS {envelope.name}  (id={envelope.process_id}, v{envelope.version})")
    out.append(
        f"  lifecycle: {envelope.lifecycle_state} | review: {envelope.review_state}"
        f" | ir schema: {envelope.ir_schema_version}"
    )
    cov = envelope.coverage_estimate
    if cov is not None:
        out.append(
            f"  coverage: {(1.0 - cov.unseen_mass) * 100:.1f}% estimated "
            f"({cov.distinct_variants} variants, {cov.singleton_variants} singletons; "
            f"unseen mass {cov.unseen_mass * 100:.1f}%)"
        )
    else:
        out.append("  coverage: not yet estimated")
    if envelope.parameter_signature:
        sig = ", ".join(f"{p.name}:{p.value_class}" for p in envelope.parameter_signature)
        out.append(f"  parameters: {sig}")
    out.append("")
    by_id = {s.id: s for s in g.steps}
    for step_id in traversal_order(g):
        out.extend(_fmt_step(by_id[step_id]))
        outgoing = [e for e in g.edges if e.from_step == step_id]
        for e in outgoing:
            if e.guard is not None:
                out.append(f"    --[{_fmt_predicate(e.guard)}]--> {e.to_step}")
            elif e.guard_question is not None:
                out.append(f"    --[? {e.guard_question}]--> {e.to_step}")
            else:
                out.append(f"    --> {e.to_step}")
        out.append("")
    return "\n".join(out)
