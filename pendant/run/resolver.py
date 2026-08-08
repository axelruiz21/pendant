"""TargetVector -> live Playwright locator (invariant 4 at run time).

The IR captures identity vectors, never single selectors; resolution
tries the dimensions in order of identity strength and accepts only a
UNIQUE match. An ambiguous or missing target is a fault the operator
sees (typically selector rot, FMEA "locator rot" row) — the runner
never clicks a guess.
"""

from __future__ import annotations

from playwright.async_api import FrameLocator, Locator, Page

from pendant.ir.models import TargetVector


class ResolutionError(Exception):
    """Target could not be resolved to exactly one element."""


def _candidate_selectors(target: TargetVector) -> list[tuple[str, str]]:
    """(description, css/xpath selector) candidates, strongest first."""
    candidates: list[tuple[str, str]] = []
    if target.testid:
        candidates.append(("testid", f'[data-testid="{target.testid}"]'))
    if target.attrs:
        joined = "".join(f'[{k}="{v}"]' for k, v in sorted(target.attrs.items()))
        candidates.append(("attrs", joined))
    if target.css:
        candidates.append(("css", target.css))
    if target.xpath:
        candidates.append(("xpath", f"xpath={target.xpath}"))
    return candidates


async def resolve_target(page: Page, target: TargetVector) -> Locator:
    """Resolve to a unique locator or raise ResolutionError."""
    scope: Page | FrameLocator = page
    if target.frame_url:
        scope = page.frame_locator(f'iframe[src*="{target.frame_url}"]')

    attempts: list[str] = []
    candidates: list[tuple[str, Locator]] = []
    if target.role and target.name:
        candidates.append(
            (
                f"role={target.role!r} name={target.name!r}",
                scope.get_by_role(target.role, name=target.name, exact=True),  # type: ignore[arg-type]
            )
        )
    candidates.extend(
        (description, scope.locator(selector))
        for description, selector in _candidate_selectors(target)
    )
    if not candidates:
        raise ResolutionError(f"target vector has no resolvable dimensions: {target}")

    for description, locator in candidates:
        count = await locator.count()
        if count == 1:
            return locator
        attempts.append(f"{description} -> {count} matches")
    raise ResolutionError(
        "no locator dimension resolved to exactly one element "
        f"(likely selector rot): {'; '.join(attempts)}"
    )
