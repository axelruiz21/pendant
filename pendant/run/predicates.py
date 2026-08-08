"""Predicate evaluation against the live browser (invariant 5 at run time).

Each IR predicate kind maps to one observable check. `http_status`
asserts against Tier 1 evidence: the runner records network responses
per step and the predicate matches the system-of-record call, not a
UI toast.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from playwright.async_api import Page

from pendant.ir.models import Predicate, TargetVector
from pendant.run.resolver import ResolutionError, resolve_target


@dataclass
class ResponseRecord:
    url: str
    status: int


@dataclass
class EvalContext:
    """What a predicate may observe: the page, this step's network
    window, resolved run parameters, and the downloads directory."""

    page: Page
    responses: list[ResponseRecord] = field(default_factory=list)
    params: dict[str, str] = field(default_factory=dict)
    download_dir: Path | None = None


def resolve_template(template: str, params: dict[str, str]) -> str:
    """Fill {name} placeholders; unknown names raise KeyError('name')."""

    def _sub(match: re.Match[str]) -> str:
        return params[match.group(1)]

    return re.sub(r"\{([a-zA-Z0-9_]+)\}", _sub, template)


def _target_of(args: dict[str, object]) -> TargetVector:
    raw = args["target"]
    if isinstance(raw, TargetVector):
        return raw
    return TargetVector.model_validate(raw)


async def _evaluate_positive(predicate: Predicate, ctx: EvalContext) -> bool:
    args = predicate.args
    match predicate.kind:
        case "url_matches":
            pattern = resolve_template(str(args["pattern"]), ctx.params)
            return re.search(pattern, ctx.page.url) is not None
        case "element_visible":
            try:
                locator = await resolve_target(ctx.page, _target_of(args))
            except ResolutionError:
                return False
            return await locator.is_visible()
        case "text_matches":
            try:
                locator = await resolve_target(ctx.page, _target_of(args))
            except ResolutionError:
                return False
            pattern = resolve_template(str(args["pattern"]), ctx.params)
            text = await locator.inner_text()
            return re.search(pattern, text) is not None
        case "http_status":
            url_part = resolve_template(str(args["url_template"]), ctx.params)
            expected = int(str(args["status"]))
            return any(
                url_part in r.url and r.status == expected for r in ctx.responses
            )
        case "row_count":
            try:
                locator = await resolve_target(ctx.page, _target_of(args))
            except ResolutionError:
                return False
            count = await locator.locator("> *").count()
            value = int(str(args["value"]))
            match str(args["op"]):
                case "eq":
                    return count == value
                case "ge":
                    return count >= value
                case _:
                    return count <= value
        case "value_equals":
            try:
                locator = await resolve_target(ctx.page, _target_of(args))
            except ResolutionError:
                return False
            expected_value = resolve_template(str(args["value"]), ctx.params)
            return await locator.input_value() == expected_value
        case "file_exists":
            path = Path(resolve_template(str(args["path_template"]), ctx.params))
            return path.exists()
    raise AssertionError(f"unhandled predicate kind {predicate.kind!r}")  # pragma: no cover


async def evaluate(predicate: Predicate, ctx: EvalContext) -> bool:
    result = await _evaluate_positive(predicate, ctx)
    return (not result) if predicate.negate else result
