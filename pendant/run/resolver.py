"""TargetVector -> live Playwright locator (invariant 4 at run time).

The IR captures identity vectors, never single selectors; resolution
tries the dimensions in order of identity strength and accepts only a
UNIQUE match. An ambiguous or missing target is a fault the operator
sees (typically selector rot, FMEA "locator rot" row) — the runner
never clicks a guess.

Capture records `frame_url` unconditionally (top frame included), so a
non-None frame_url is treated as iframe scoping ONLY when an iframe
matching it actually exists on the page; otherwise resolution proceeds
in the top frame (review finding, D-017 hardening).
"""

from __future__ import annotations

from playwright.async_api import FrameLocator, Locator, Page

from pendant.ir.models import TargetVector


class ResolutionError(Exception):
    """Target could not be resolved to exactly one element."""


def css_escape(value: str) -> str:
    """Escape a raw value for embedding in a CSS attribute selector."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _candidate_selectors(target: TargetVector) -> list[tuple[str, str]]:
    """(description, css/xpath selector) candidates, strongest first."""
    candidates: list[tuple[str, str]] = []
    if target.testid:
        candidates.append(("testid", f'[data-testid="{css_escape(target.testid)}"]'))
    if target.attrs:
        joined = "".join(
            f'[{k}="{css_escape(v)}"]' for k, v in sorted(target.attrs.items())
        )
        candidates.append(("attrs", joined))
    if target.css:
        candidates.append(("css", target.css))
    if target.xpath:
        candidates.append(("xpath", f"xpath={target.xpath}"))
    return candidates


async def _scope_for(page: Page, target: TargetVector) -> Page | FrameLocator:
    if not target.frame_url:
        return page
    frame_selector = f'iframe[src*="{css_escape(target.frame_url)}"]'
    if await page.locator(frame_selector).count() > 0:
        return page.frame_locator(frame_selector)
    # frame_url was recorded for a top-frame element (capture sets it
    # unconditionally); no matching iframe exists, so resolve top-frame.
    return page


async def resolve_target(page: Page, target: TargetVector) -> Locator:
    """Resolve to a unique locator or raise ResolutionError.

    Any error from a single dimension (invalid selector, protocol
    error) disqualifies that dimension and moves on; only when every
    dimension fails does the target fault.
    """
    scope = await _scope_for(page, target)

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
        try:
            count = await locator.count()
        except Exception as exc:  # invalid selector / protocol error
            attempts.append(f"{description} -> error: {exc}")
            continue
        if count == 1:
            return locator
        attempts.append(f"{description} -> {count} matches")
    raise ResolutionError(
        "no locator dimension resolved to exactly one element "
        f"(likely selector rot): {'; '.join(attempts)}"
    )
