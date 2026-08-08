"""Assisted runner: walk an IR graph, execute, verify, stop when unsure.

Contract with the IR (D-017, hardened per the D-017 review findings):

- preconditions must pass before the action fires, and postconditions
  within the step's finite timeout_ms, or the step FAULTS — there is
  no unverified success and no unlicensed action (invariant 5);
- approval_required stops for the operator before acting, and operator
  prompts (approvals, missing parameters) happen BEFORE the step's
  deadline starts, so think-time is never billed against timeout_ms;
- a branch is taken only on a passing guard or an explicit operator
  answer to the edge's guard_question — never a guess (invariant 6);
- faults follow the step's on_fault policy; automatic retry of a step
  whose idempotency is not "safe" requires operator confirmation,
  because the side effect may already have landed;
- http_status evidence windows contain only responses to requests
  issued by the current action attempt (plus http_call's own call);
- secret values (getpass prompts, --param values with secret-looking
  names) are tracked and redacted from every operator notice, fault
  note, and the persisted run report (invariant 3);
- any exception during predicate or guard evaluation converts to a
  step fault or a clean abort — the run always produces a report.

The operator console is a protocol so tests drive runs deterministically.
"""

from __future__ import annotations

import asyncio
import getpass
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from playwright.async_api import Page, Request, Response, async_playwright

from pendant.capture.redaction import REDACTED, RedactionRegistry
from pendant.ir.models import Action, Edge, Predicate, ProcessEnvelope, Step, placeholder_names
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
    """The step's action failed or its conditions did not pass in time."""


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
        registry: RedactionRegistry | None = None,
    ) -> None:
        self.envelope = envelope
        self.graph = envelope.graph
        self.console = console
        self.params: dict[str, str] = dict(params or {})
        self.headless = headless
        self.download_dir = download_dir or Path("pendant_data") / "downloads"
        self._registry = registry or RedactionRegistry()
        self._responses: list[ResponseRecord] = []
        self._window_requests: set[Request] = set()
        # Values that must never appear in notices, notes, or the
        # persisted report (invariant 3). Seeded from any provided
        # param whose NAME matches the redaction registry.
        self._secret_values: set[str] = {
            v for k, v in self.params.items() if self._is_secret_name(k) and v
        }
        self._fill_values: dict[str, str] = {}

    # -- redaction (invariant 3) ----------------------------------------------

    def _is_secret_name(self, name: str) -> bool:
        return self._registry.match_field(name) is not None

    def _mark_secret(self, value: str) -> None:
        if value:
            self._secret_values.add(value)

    def _redact(self, text: str) -> str:
        for value in self._secret_values:
            text = text.replace(value, REDACTED)
        return text

    def _notify(self, message: str) -> None:
        self.console.notify(self._redact(message))

    # -- parameter handling ----------------------------------------------------

    def _resolve(self, template: str, *, context_hint: str, secret_hint: bool = False) -> str:
        """Fill placeholders, asking the operator for anything missing."""
        while True:
            try:
                return resolve_template(template, self.params)
            except KeyError as exc:
                name = exc.args[0]
                secret = secret_hint or self._is_secret_name(name)
                value = self.console.ask_value(f"{name} ({context_hint})", secret)
                self.params[name] = value
                if secret:
                    self._mark_secret(value)

    def _target_is_secret(self, action: Action) -> bool:
        target = action.target_vector
        if target is None:
            return False
        return (
            self._registry.match_field(
                target.name, target.testid, *target.attrs.values()
            )
            is not None
        )

    def _prebind_step(self, step: Step) -> None:
        """Resolve every parameter the step will need BEFORE its deadline
        starts, so operator think-time is not billed against timeout_ms.
        Also collects the fill value when the action has no template."""
        hint = f"step {step.id}: {step.label}"
        secret_target = self._target_is_secret(step.action)
        for value in step.action.params.values():
            if isinstance(value, str):
                self._resolve(value, context_hint=hint, secret_hint=secret_target)
        if step.action.type == "fill" and not isinstance(
            step.action.params.get("value"), str
        ):
            target = step.action.target_vector
            assert target is not None  # schema-enforced for fill
            name = target.name or target.testid or step.label
            value = self.console.ask_value(f"{name} ({hint})", secret_target)
            if secret_target:
                self._mark_secret(value)
            self._fill_values[step.id] = value
        for predicate in [*step.preconditions, *step.postconditions]:
            self._prebind_predicate(predicate, hint)
        for edge in self.graph.edges:
            if edge.from_step == step.id and edge.guard is not None:
                self._prebind_predicate(edge.guard, hint)

    def _prebind_predicate(self, predicate: Predicate, hint: str) -> None:
        for name in sorted(placeholder_names(predicate.args)):
            if name not in self.params:
                self._resolve("{" + name + "}", context_hint=f"{hint} (predicate)")

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
        page.on("request", self._on_request)
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
            report.note = self._redact(str(exc))
        finally:
            page.remove_listener("request", self._on_request)
            page.remove_listener("response", self._on_response)
        return report

    def _on_request(self, request: Request) -> None:
        self._window_requests.add(request)

    def _on_response(self, response: Response) -> None:
        # Only responses to requests issued inside the current action
        # window count as evidence; a late response from a previous
        # attempt or a pre-action background poll must not verify this
        # attempt (review finding: evidence misattribution).
        if response.request in self._window_requests:
            self._responses.append(
                ResponseRecord(
                    url=response.url,
                    status=response.status,
                    method=response.request.method,
                )
            )

    def _open_response_window(self) -> None:
        self._responses.clear()
        self._window_requests.clear()

    def _ctx(self, page: Page, deadline: float) -> EvalContext:
        remaining_ms = max((deadline - time.monotonic()) * 1000, 1.0)
        return EvalContext(
            page=page,
            responses=self._responses,
            params=self.params,
            call_timeout_ms=min(remaining_ms, 5_000.0),
        )

    async def _execute_step(self, page: Page, step: Step) -> tuple[StepResult, str | None]:
        self._notify(f"-> {step.id}: {step.label}")
        attempts = 0
        started = time.monotonic()
        while True:
            attempts += 1
            # Operator interaction happens before the deadline starts.
            if step.approval_required and not self.console.approve(step):
                raise RunAborted(f"operator declined approval at step {step.id!r}")
            self._prebind_step(step)
            deadline = time.monotonic() + step.timeout_ms / 1000
            try:
                await self._await_predicates(
                    page, step.preconditions, deadline, step, "preconditions"
                )
                self._open_response_window()
                await self._do_action(page, step, deadline)
                await self._await_predicates(
                    page, step.postconditions, deadline, step, "postconditions"
                )
                elapsed = int((time.monotonic() - started) * 1000)
                return StepResult(step.id, step.label, "ok", attempts, elapsed), None
            except StepFault as fault:
                policy = step.on_fault
                if policy.policy == "retry" and attempts <= policy.max_retries:
                    if step.idempotency == "safe":
                        self._notify(
                            f"   fault, retrying ({attempts}/{policy.max_retries}): {fault}"
                        )
                        await asyncio.sleep(policy.backoff_ms / 1000)
                        continue
                    # The action may already have taken effect; blind
                    # re-execution of a non-safe step needs a human.
                    choice = self.console.choose(
                        f"Step {step.id!r} faulted but its action is "
                        f"idempotency={step.idempotency!r} and may have already taken "
                        f"effect: {self._redact(str(fault))}. Retry anyway?",
                        ["retry the step (re-executes the action)", "abort the run"],
                    )
                    if choice == 0:
                        await asyncio.sleep(policy.backoff_ms / 1000)
                        continue
                    raise RunAborted(
                        f"operator declined unsafe retry at step {step.id!r}"
                    ) from fault
                if policy.policy == "rollback":
                    assert policy.transfer_to is not None  # schema-enforced
                    elapsed = int((time.monotonic() - started) * 1000)
                    result = StepResult(
                        step.id,
                        step.label,
                        "rolled_back",
                        attempts,
                        elapsed,
                        note=self._redact(str(fault)),
                    )
                    return result, policy.transfer_to
                if policy.policy == "escalate":
                    warning = (
                        ""
                        if step.idempotency == "safe"
                        else f" CAUTION: idempotency={step.idempotency!r}; the action "
                        "may have already taken effect."
                    )
                    choice = self.console.choose(
                        f"Step {step.id!r} faulted: {self._redact(str(fault))}."
                        f"{warning} How should the run proceed?",
                        ["retry the step", "abort the run"],
                    )
                    if choice == 0:
                        continue
                raise RunAborted(
                    f"step {step.id!r} faulted: {self._redact(str(fault))}"
                ) from fault

    async def _do_action(self, page: Page, step: Step, deadline: float) -> None:
        action = step.action
        timeout_ms = max((deadline - time.monotonic()) * 1000, 1.0)
        try:
            await self._dispatch_action(page, step, action, timeout_ms)
        except StepFault:
            raise
        except Exception as exc:  # playwright errors carry the diagnosis
            raise StepFault(
                self._redact(f"action {action.type!r} failed: {exc}")
            ) from exc

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
                response = await page.request.fetch(
                    url, method=method, timeout=timeout_ms
                )
                self._responses.append(
                    ResponseRecord(url=response.url, status=response.status, method=method)
                )
            case _:
                assert action.target_vector is not None  # schema-enforced
                locator = await resolve_target(page, action.target_vector)
                match action.type:
                    case "click":
                        await locator.click(timeout=timeout_ms)
                    case "fill":
                        template = action.params.get("value")
                        if isinstance(template, str):
                            value = self._resolve(
                                template,
                                context_hint=hint,
                                secret_hint=self._target_is_secret(action),
                            )
                        else:
                            value = self._fill_values[step.id]
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
                        into = str(action.params.get("into", "last_download"))
                        saved = self.download_dir / download.suggested_filename
                        await download.save_as(saved)
                        self.params[into] = str(saved)
                    case "extract":
                        into = str(action.params.get("into", f"{step.id}_text"))
                        self.params[into] = (
                            await locator.inner_text(timeout=timeout_ms)
                        ).strip()
                    case _:  # pragma: no cover - closed catalog
                        raise StepFault(f"unsupported action type {action.type!r}")

    async def _await_predicates(
        self,
        page: Page,
        predicates: list[Predicate],
        deadline: float,
        step: Step,
        phase: str,
    ) -> None:
        if not predicates:
            return
        while True:
            failed: list[str] = []
            for predicate in predicates:
                try:
                    ok = await evaluate(predicate, self._ctx(page, deadline))
                except Exception as exc:
                    # A predicate that cannot be evaluated is a fault,
                    # never a crash (review finding).
                    raise StepFault(
                        self._redact(
                            f"{phase} predicate {predicate.kind!r} failed to "
                            f"evaluate: {exc}"
                        )
                    ) from exc
                if not ok:
                    failed.append(predicate.kind)
            if not failed:
                return
            if time.monotonic() > deadline:
                raise StepFault(
                    f"{phase} not satisfied within {step.timeout_ms}ms: {failed}"
                )
            await asyncio.sleep(POLL_INTERVAL_S)

    async def _next_step(self, page: Page, step: Step) -> str | None:
        outs: list[Edge] = [e for e in self.graph.edges if e.from_step == step.id]
        if not outs:
            return None
        if len(outs) == 1 and outs[0].guard is None and outs[0].guard_question is None:
            return outs[0].to_step
        deadline = time.monotonic() + 5.0  # guards read settled state
        for edge in outs:
            if edge.guard is None:
                continue
            try:
                passed = await evaluate(edge.guard, self._ctx(page, deadline))
            except Exception as exc:
                raise RunAborted(
                    self._redact(
                        f"guard on edge {step.id!r} -> {edge.to_step!r} failed to "
                        f"evaluate: {exc}"
                    )
                ) from exc
            if passed:
                return edge.to_step
        questioned = [e for e in outs if e.guard_question is not None]
        if questioned:
            options = [f"{e.guard_question} -> {e.to_step}" for e in questioned]
            choice = self.console.choose(
                f"Branch after step {step.id!r} ({step.label}): which way?", options
            )
            return questioned[choice].to_step
        raise RunAborted(
            f"no outgoing edge from step {step.id!r} satisfied its guard"
        )
