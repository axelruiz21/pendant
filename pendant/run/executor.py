"""Assisted runner: walk an IR graph, execute, verify, stop when unsure.

Contract with the IR (D-017):

- every step's postconditions must pass within its finite timeout_ms,
  or the step FAULTS — there is no unverified success (invariant 5);
- approval_required stops for the operator before acting (invariant 15);
- a branch is taken only on a passing guard or an explicit operator
  answer to the edge's guard_question — never a guess (invariant 6);
- faults follow the step's on_fault policy (retry / escalate /
  rollback / abort);
- parameter values are supplied by the operator or --param flags;
  secret-looking values are prompted without echo and never logged
  (invariant 3).

The operator console is a protocol so tests drive runs deterministically.
"""

from __future__ import annotations

import asyncio
import getpass
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from playwright.async_api import Page, Response, async_playwright

from pendant.capture.redaction import RedactionRegistry
from pendant.ir.models import Action, Edge, ProcessEnvelope, Step
from pendant.run.predicates import (
    EvalContext,
    ResponseRecord,
    evaluate,
    resolve_template,
)
from pendant.run.resolver import resolve_target

POLL_INTERVAL_S = 0.1


class RunAborted(Exception):
    """The run stopped: operator decline, abort policy, or dead end."""


class StepFault(Exception):
    """The step's action failed or its postconditions did not pass in time."""


class OperatorConsole(Protocol):
    def approve(self, step: Step) -> bool: ...

    def choose(self, question: str, options: list[str]) -> int: ...

    def ask_value(self, name: str, secret: bool) -> str: ...

    def notify(self, message: str) -> None: ...


class StdioConsole:
    """Interactive console on stdin/stdout; secrets via getpass."""

    def approve(self, step: Step) -> bool:
        print(f"\nAPPROVAL REQUIRED — step {step.id!r}: {step.label}")
        print(f"  action={step.action.type} risk={step.risk} idempotency={step.idempotency}")
        return input("  proceed? [y/N] ").strip().lower() == "y"

    def choose(self, question: str, options: list[str]) -> int:
        print(f"\n{question}")
        for i, option in enumerate(options):
            print(f"  [{i}] {option}")
        while True:
            raw = input("  choice: ").strip()
            if raw.isdigit() and int(raw) < len(options):
                return int(raw)
            print("  enter one of the listed numbers")

    def ask_value(self, name: str, secret: bool) -> str:
        prompt = f"value for {name!r}: "
        return getpass.getpass(prompt) if secret else input(prompt)

    def notify(self, message: str) -> None:
        print(message)


@dataclass
class StepResult:
    step_id: str
    label: str
    outcome: Literal["ok", "rolled_back"]
    attempts: int
    elapsed_ms: int
    note: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "step_id": self.step_id,
            "label": self.label,
            "outcome": self.outcome,
            "attempts": self.attempts,
            "elapsed_ms": self.elapsed_ms,
            "note": self.note,
        }


@dataclass
class RunReport:
    process_id: str
    version: int
    outcome: Literal["completed", "aborted"]
    results: list[StepResult] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "process_id": self.process_id,
            "version": self.version,
            "outcome": self.outcome,
            "note": self.note,
            "steps": [r.to_dict() for r in self.results],
        }


class Runner:
    """Executes one ProcessEnvelope in assisted mode."""

    def __init__(
        self,
        envelope: ProcessEnvelope,
        console: OperatorConsole,
        params: dict[str, str] | None = None,
        *,
        headless: bool = False,
        download_dir: Path | None = None,
    ) -> None:
        self.envelope = envelope
        self.graph = envelope.graph
        self.console = console
        self.params: dict[str, str] = dict(params or {})
        self.headless = headless
        self.download_dir = download_dir or Path("pendant_data") / "downloads"
        self._registry = RedactionRegistry()
        self._responses: list[ResponseRecord] = []
        self._secret_params: set[str] = set()

    # -- parameter handling ----------------------------------------------------

    def _is_secret_name(self, name: str) -> bool:
        return self._registry.match_field(name) is not None

    def _resolve(self, template: str, *, context_hint: str) -> str:
        """Fill placeholders, asking the operator for anything missing."""
        while True:
            try:
                return resolve_template(template, self.params)
            except KeyError as exc:
                name = exc.args[0]
                secret = self._is_secret_name(name)
                value = self.console.ask_value(f"{name} ({context_hint})", secret)
                self.params[name] = value
                if secret:
                    self._secret_params.add(name)

    # -- run loop --------------------------------------------------------------

    async def run(self) -> RunReport:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless)
            context = await browser.new_context()
            page = await context.new_page()
            try:
                return await self.run_on_page(page)
            finally:
                await browser.close()

    async def run_on_page(self, page: Page) -> RunReport:
        page.on("response", self._on_response)
        report = RunReport(
            process_id=self.envelope.process_id,
            version=self.envelope.version,
            outcome="completed",
        )
        current: str | None = self.graph.entry
        try:
            while current is not None:
                step = self.graph.step(current)
                result, override_next = await self._execute_step(page, step)
                report.results.append(result)
                current = override_next if override_next else await self._next_step(page, step)
        except RunAborted as exc:
            report.outcome = "aborted"
            report.note = str(exc)
        return report

    def _on_response(self, response: Response) -> None:
        self._responses.append(ResponseRecord(url=response.url, status=response.status))

    def _ctx(self, page: Page) -> EvalContext:
        return EvalContext(
            page=page,
            responses=self._responses,
            params=self.params,
            download_dir=self.download_dir,
        )

    async def _execute_step(self, page: Page, step: Step) -> tuple[StepResult, str | None]:
        self.console.notify(f"-> {step.id}: {step.label}")
        attempts = 0
        started = time.monotonic()
        while True:
            attempts += 1
            self._responses.clear()
            if step.approval_required and not self.console.approve(step):
                raise RunAborted(f"operator declined approval at step {step.id!r}")
            deadline = time.monotonic() + step.timeout_ms / 1000
            try:
                await self._do_action(page, step, deadline)
                await self._await_postconditions(page, step, deadline)
                elapsed = int((time.monotonic() - started) * 1000)
                return StepResult(step.id, step.label, "ok", attempts, elapsed), None
            except StepFault as fault:
                policy = step.on_fault
                if policy.policy == "retry" and attempts <= policy.max_retries:
                    self.console.notify(
                        f"   fault, retrying ({attempts}/{policy.max_retries}): {fault}"
                    )
                    await asyncio.sleep(policy.backoff_ms / 1000)
                    continue
                if policy.policy == "rollback":
                    assert policy.transfer_to is not None  # schema-enforced
                    elapsed = int((time.monotonic() - started) * 1000)
                    result = StepResult(
                        step.id, step.label, "rolled_back", attempts, elapsed, note=str(fault)
                    )
                    return result, policy.transfer_to
                if policy.policy == "escalate":
                    choice = self.console.choose(
                        f"Step {step.id!r} faulted: {fault}. How should the run proceed?",
                        ["retry the step", "abort the run"],
                    )
                    if choice == 0:
                        continue
                raise RunAborted(f"step {step.id!r} faulted: {fault}") from fault

    async def _do_action(self, page: Page, step: Step, deadline: float) -> None:
        action = step.action
        timeout_ms = max((deadline - time.monotonic()) * 1000, 1.0)
        try:
            await self._dispatch_action(page, step, action, timeout_ms)
        except StepFault:
            raise
        except Exception as exc:  # playwright errors carry the diagnosis
            raise StepFault(f"action {action.type!r} failed: {exc}") from exc

    async def _dispatch_action(
        self, page: Page, step: Step, action: Action, timeout_ms: float
    ) -> None:
        hint = f"step {step.id}: {step.label}"
        match action.type:
            case "navigate":
                url = self._resolve(str(action.params["url"]), context_hint=hint)
                await page.goto(url, timeout=timeout_ms)
            case "assert_only":
                return
            case "http_call":
                url = self._resolve(str(action.params["url_template"]), context_hint=hint)
                method = str(action.params.get("method", "GET"))
                response = await page.request.fetch(url, method=method)
                self._responses.append(ResponseRecord(url=response.url, status=response.status))
            case _:
                assert action.target_vector is not None  # schema-enforced
                locator = await resolve_target(page, action.target_vector)
                match action.type:
                    case "click":
                        await locator.click(timeout=timeout_ms)
                    case "fill":
                        template = action.params.get("value")
                        if isinstance(template, str):
                            value = self._resolve(template, context_hint=hint)
                        else:
                            target = action.target_vector
                            name = target.name or target.testid or step.label
                            secret = self._registry.match_field(
                                target.name, target.testid, *target.attrs.values()
                            ) is not None
                            value = self.console.ask_value(f"{name} ({hint})", secret)
                        await locator.fill(value, timeout=timeout_ms)
                    case "select":
                        value = self._resolve(str(action.params["value"]), context_hint=hint)
                        await locator.select_option(value, timeout=timeout_ms)
                    case "press":
                        await locator.press(str(action.params["keys"]), timeout=timeout_ms)
                    case "upload":
                        path = self._resolve(
                            str(action.params["path_template"]), context_hint=hint
                        )
                        await locator.set_input_files(path, timeout=timeout_ms)
                    case "download":
                        self.download_dir.mkdir(parents=True, exist_ok=True)
                        async with page.expect_download(timeout=timeout_ms) as pending:
                            await locator.click(timeout=timeout_ms)
                        download = await pending.value
                        saved = self.download_dir / download.suggested_filename
                        await download.save_as(saved)
                        self.params["last_download"] = str(saved)
                    case "extract":
                        into = str(action.params.get("into", f"{step.id}_text"))
                        self.params[into] = (await locator.inner_text()).strip()
                    case _:  # pragma: no cover - closed catalog
                        raise StepFault(f"unsupported action type {action.type!r}")

    async def _await_postconditions(self, page: Page, step: Step, deadline: float) -> None:
        ctx = self._ctx(page)
        while True:
            failed: list[str] = []
            for predicate in step.postconditions:
                if not await evaluate(predicate, ctx):
                    failed.append(predicate.kind)
            if not failed:
                return
            if time.monotonic() > deadline:
                raise StepFault(
                    f"postconditions not satisfied within {step.timeout_ms}ms: {failed}"
                )
            await asyncio.sleep(POLL_INTERVAL_S)

    async def _next_step(self, page: Page, step: Step) -> str | None:
        outs: list[Edge] = [e for e in self.graph.edges if e.from_step == step.id]
        if not outs:
            return None
        if len(outs) == 1 and outs[0].guard is None and outs[0].guard_question is None:
            return outs[0].to_step
        ctx = self._ctx(page)
        for edge in outs:
            if edge.guard is not None and await evaluate(edge.guard, ctx):
                return edge.to_step
        questioned = [e for e in outs if e.guard_question is not None]
        if questioned:
            options = [
                f"{e.guard_question} -> {e.to_step}" for e in questioned
            ]
            choice = self.console.choose(
                f"Branch after step {step.id!r} ({step.label}): which way?", options
            )
            return questioned[choice].to_step
        raise RunAborted(
            f"no outgoing edge from step {step.id!r} satisfied its guard"
        )
