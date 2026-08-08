"""Runner (D-017): assisted execution against the reference process.

Integration tests drive a real headless Chromium against the same
local reference app Gate 0 uses; the operator is a scripted console,
so approvals, branch questions, and secret prompts are exercised
deterministically. No mocks of runner internals. Includes regression
coverage for the D-017 review findings (secret redaction, top-frame
frame_url, method-pinned http_status, preconditions, unsafe retry,
evaluation-error faulting).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from pendant.capture.gate0 import DEFAULT_APP_DIR, ReferenceServer
from pendant.cli import main
from pendant.ir.models import IR_SCHEMA_VERSION, ProcessEnvelope, Step, TargetVector
from pendant.run import Runner
from pendant.run.predicates import resolve_template
from pendant.run.resolver import _candidate_selectors


class ScriptedConsole:
    """Deterministic operator: canned approvals, choices, and values."""

    def __init__(
        self,
        approvals: list[bool] | None = None,
        choices: list[int] | None = None,
        values: dict[str, str] | None = None,
    ) -> None:
        self.approvals = list(approvals or [])
        self.choices = list(choices or [])
        self.values = dict(values or {})
        self.approval_steps: list[str] = []
        self.questions: list[str] = []
        self.value_requests: list[tuple[str, bool]] = []
        self.notices: list[str] = []

    def approve(self, step: Step) -> bool:
        self.approval_steps.append(step.id)
        return self.approvals.pop(0)

    def choose(self, question: str, options: list[str]) -> int:
        self.questions.append(question)
        return self.choices.pop(0)

    def ask_value(self, name: str, secret: bool) -> str:
        self.value_requests.append((name, secret))
        for key, value in self.values.items():
            if key in name:
                return value
        raise AssertionError(f"no scripted value for {name!r}")

    def notify(self, message: str) -> None:
        self.notices.append(message)

    def all_output(self) -> str:
        return json.dumps(self.notices + self.questions)


def _step(
    step_id: str,
    label: str,
    action: dict[str, Any],
    postconditions: list[dict[str, Any]],
    **overrides: Any,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": step_id,
        "label": label,
        "action": action,
        "postconditions": postconditions,
        "timeout_ms": 15_000,
        "on_fault": {"policy": "escalate", "max_retries": 0},
        "idempotency": "safe",
        "risk": "read",
        "confidence": 0.9,
    }
    base.update(overrides)
    return base


def _target(testid: str) -> dict[str, str]:
    return {"testid": testid}


def _visible(testid: str, *, negate: bool = False) -> dict[str, Any]:
    return {
        "kind": "element_visible",
        "args": {"target": _target(testid)},
        "negate": negate,
    }


def order_entry_envelope() -> ProcessEnvelope:
    steps = [
        _step(
            "s_nav",
            "Open sign-in page",
            {"type": "navigate", "params": {"url": "{base_url}/login.html"}},
            [
                {"kind": "url_matches", "args": {"pattern": r"login\.html"}},
                _visible("sign-in"),
            ],
        ),
        _step(
            "s_user",
            "Enter username",
            {
                "type": "fill",
                # Capture records frame_url unconditionally, top frame
                # included (review finding): resolution must fall back
                # to the top frame when no matching iframe exists.
                "target_vector": {
                    "testid": "username",
                    "frame_url": "https://recorded.example/login.html",
                },
                "params": {"value": "{username}"},
            },
            [
                {
                    "kind": "value_equals",
                    "args": {"target": _target("username"), "value": "{username}"},
                }
            ],
        ),
        _step(
            "s_pass",
            "Enter password",
            # No value template: the runner must ask the operator,
            # detect the secret-looking field, and never echo it.
            {"type": "fill", "target_vector": _target("password"), "params": {}},
            [_visible("password")],
        ),
        _step(
            "s_signin",
            "Sign in",
            {"type": "click", "target_vector": _target("sign-in")},
            [
                {
                    "kind": "http_status",
                    "args": {"url_template": "/api/login", "status": 200, "method": "POST"},
                },
                {"kind": "url_matches", "args": {"pattern": r"orders\.html"}},
            ],
        ),
        _step(
            "s_customer",
            "Enter customer",
            {
                "type": "fill",
                "target_vector": _target("customer"),
                "params": {"value": "{customer}"},
            },
            [
                {
                    "kind": "value_equals",
                    "args": {"target": _target("customer"), "value": "{customer}"},
                }
            ],
            # Review finding: preconditions must gate the action.
            preconditions=[{"kind": "url_matches", "args": {"pattern": r"orders\.html"}}],
        ),
        _step(
            "s_create",
            "Create order",
            {"type": "click", "target_vector": _target("create-order")},
            [
                {
                    "kind": "http_status",
                    "args": {"url_template": "/api/orders", "status": 201, "method": "POST"},
                },
                _visible("done"),
                {
                    "kind": "text_matches",
                    "args": {
                        "target": {"css": "#confirmation-text"},
                        "pattern": r"Order 48219 created",
                    },
                },
            ],
            risk="write",
            idempotency="unsafe",
            approval_required=True,
        ),
        _step(
            "s_done",
            "Dismiss confirmation",
            {"type": "click", "target_vector": _target("done")},
            [_visible("done", negate=True)],
        ),
        _step(
            "s_logout",
            "Log out",
            {"type": "click", "target_vector": _target("log-out")},
            [{"kind": "url_matches", "args": {"pattern": r"login\.html"}}],
        ),
    ]
    edges = [
        {"from_step": "s_nav", "to_step": "s_user"},
        {"from_step": "s_user", "to_step": "s_pass"},
        {"from_step": "s_pass", "to_step": "s_signin"},
        {"from_step": "s_signin", "to_step": "s_customer"},
        {"from_step": "s_customer", "to_step": "s_create"},
        {
            "from_step": "s_create",
            "to_step": "s_done",
            "guard_question": "Did the confirmation show the right order?",
        },
        {
            "from_step": "s_create",
            "to_step": "s_logout",
            "guard_question": "Wrong order — log out without dismissing?",
        },
    ]
    return ProcessEnvelope.model_validate(
        {
            "process_id": "order-entry",
            "name": "Order entry (reference)",
            "version": 1,
            "ir_schema_version": IR_SCHEMA_VERSION,
            "graph": {"entry": "s_nav", "steps": steps, "edges": edges},
        }
    )


class TestResolveTemplate:
    def test_placeholders_filled(self) -> None:
        assert resolve_template("{a}/x/{b}", {"a": "1", "b": "2"}) == "1/x/2"

    def test_missing_placeholder_raises_key(self) -> None:
        with pytest.raises(KeyError, match="missing"):
            resolve_template("{missing}", {})

    def test_no_placeholders_passthrough(self) -> None:
        assert resolve_template("https://x/y?z=1", {}) == "https://x/y?z=1"

    def test_regex_quantifiers_are_not_placeholders(self) -> None:
        # Review finding: \d{4} in a url_matches pattern must survive.
        assert resolve_template(r"order-\d{4}", {}) == r"order-\d{4}"
        assert resolve_template(r"x{2,3}y", {}) == r"x{2,3}y"
        assert resolve_template(r"id-\d{4}/{p0}", {"p0": "v"}) == r"id-\d{4}/v"


class TestResolverSelectors:
    def test_quotes_in_values_are_escaped(self) -> None:
        # Review finding: unescaped quotes produced invalid selectors
        # that crashed the run instead of faulting the dimension.
        target = TargetVector(testid='He said "hi"', attrs={"name": 'a"b'})
        selectors = dict(_candidate_selectors(target))
        assert selectors["testid"] == '[data-testid="He said \\"hi\\""]'
        assert selectors["attrs"] == '[name="a\\"b"]'


class TestRunnerIntegration:
    def test_happy_path_with_approval_branch_and_secret(self) -> None:
        console = ScriptedConsole(
            approvals=[True],
            choices=[0],
            values={"password": "not-a-real-secret"},
        )
        with ReferenceServer(DEFAULT_APP_DIR) as server:
            runner = Runner(
                order_entry_envelope(),
                console,
                {"base_url": server.base_url, "username": "opsuser", "customer": "Acme Co"},
                headless=True,
            )
            report = asyncio.run(runner.run())

        assert report.outcome == "completed", report.to_dict()
        assert [r.step_id for r in report.results] == [
            "s_nav", "s_user", "s_pass", "s_signin", "s_customer", "s_create", "s_done",
        ]
        assert all(r.outcome == "ok" and r.attempts == 1 for r in report.results)
        # Approval stopped exactly at the write step.
        assert console.approval_steps == ["s_create"]
        # The branch surfaced its question instead of guessing.
        assert any("s_create" in q for q in console.questions)
        # The password was requested as a secret and never logged.
        assert console.value_requests and console.value_requests[0][1] is True
        assert "not-a-real-secret" not in json.dumps(report.to_dict())
        assert "not-a-real-secret" not in console.all_output()

    def test_postcondition_fault_escalates_then_aborts(self) -> None:
        envelope = ProcessEnvelope.model_validate(
            {
                "process_id": "faulty",
                "name": "Faulty process",
                "version": 1,
                "ir_schema_version": IR_SCHEMA_VERSION,
                "graph": {
                    "entry": "s_nav",
                    "steps": [
                        _step(
                            "s_nav",
                            "Open sign-in page",
                            {"type": "navigate", "params": {"url": "{base_url}/login.html"}},
                            [{"kind": "url_matches", "args": {"pattern": r"login\.html"}}],
                        ),
                        _step(
                            "s_ghost",
                            "Wait for element that never appears",
                            {"type": "assert_only", "target_vector": _target("sign-in")},
                            [_visible("does-not-exist")],
                            timeout_ms=700,
                        ),
                    ],
                    "edges": [{"from_step": "s_nav", "to_step": "s_ghost"}],
                },
            }
        )
        console = ScriptedConsole(choices=[1])  # escalation: abort the run
        with ReferenceServer(DEFAULT_APP_DIR) as server:
            runner = Runner(envelope, console, {"base_url": server.base_url}, headless=True)
            report = asyncio.run(runner.run())

        assert report.outcome == "aborted"
        assert "s_ghost" in report.note
        assert [r.step_id for r in report.results] == ["s_nav"]
        assert any("faulted" in q for q in console.questions)

    def test_secret_never_reaches_report_or_console_on_fault(self) -> None:
        # Review finding: a secret interpolated into a URL must be
        # redacted from fault notes, notices, and the persisted report.
        envelope = ProcessEnvelope.model_validate(
            {
                "process_id": "leaky",
                "name": "Secret-in-URL fault",
                "version": 1,
                "ir_schema_version": IR_SCHEMA_VERSION,
                "graph": {
                    "entry": "s_cb",
                    "steps": [
                        _step(
                            "s_cb",
                            "Open callback with token",
                            {
                                "type": "navigate",
                                # Port 9 (discard) refuses connections fast.
                                "params": {"url": "http://127.0.0.1:9/cb?x={api_token}"},
                            },
                            [{"kind": "url_matches", "args": {"pattern": r"never"}}],
                            timeout_ms=5_000,
                        ),
                    ],
                    "edges": [],
                },
            }
        )
        console = ScriptedConsole(choices=[1])  # escalation: abort
        runner = Runner(
            envelope, console, {"api_token": "sk-live-SUPERSECRET"}, headless=True
        )
        report = asyncio.run(runner.run())

        assert report.outcome == "aborted"
        dump = json.dumps(report.to_dict())
        assert "sk-live-SUPERSECRET" not in dump
        assert "sk-live-SUPERSECRET" not in console.all_output()
        assert "{redacted}" in report.note

    def test_unsafe_step_retry_requires_operator_confirmation(self) -> None:
        # Review finding: automatic retry must not re-execute a
        # non-safe action without asking the operator.
        envelope = ProcessEnvelope.model_validate(
            {
                "process_id": "unsafe-retry",
                "name": "Unsafe retry confirmation",
                "version": 1,
                "ir_schema_version": IR_SCHEMA_VERSION,
                "graph": {
                    "entry": "s_nav",
                    "steps": [
                        _step(
                            "s_nav",
                            "Open sign-in page",
                            {"type": "navigate", "params": {"url": "{base_url}/login.html"}},
                            [{"kind": "url_matches", "args": {"pattern": r"login\.html"}}],
                        ),
                        _step(
                            "s_submit",
                            "Submit (unsafe, will fault)",
                            {"type": "click", "target_vector": _target("sign-in")},
                            [_visible("does-not-exist")],
                            timeout_ms=700,
                            idempotency="unsafe",
                            risk="write",
                            on_fault={"policy": "retry", "max_retries": 2},
                        ),
                    ],
                    "edges": [{"from_step": "s_nav", "to_step": "s_submit"}],
                },
            }
        )
        console = ScriptedConsole(choices=[1])  # decline the unsafe retry
        with ReferenceServer(DEFAULT_APP_DIR) as server:
            runner = Runner(envelope, console, {"base_url": server.base_url}, headless=True)
            report = asyncio.run(runner.run())

        assert report.outcome == "aborted"
        assert "unsafe retry" in report.note
        assert any("may have already taken effect" in q for q in console.questions)

    def test_missing_predicate_param_prompts_instead_of_crashing(self) -> None:
        # Review finding: a {param} in a predicate template used to
        # raise bare KeyError and crash the run with no report.
        envelope = ProcessEnvelope.model_validate(
            {
                "process_id": "predicate-param",
                "name": "Predicate placeholder binding",
                "version": 1,
                "ir_schema_version": IR_SCHEMA_VERSION,
                "graph": {
                    "entry": "s_nav",
                    "steps": [
                        _step(
                            "s_nav",
                            "Open sign-in page",
                            {"type": "navigate", "params": {"url": "{base_url}/login.html"}},
                            [{"kind": "url_matches", "args": {"pattern": "{expected_page}"}}],
                        ),
                    ],
                    "edges": [],
                },
            }
        )
        console = ScriptedConsole(values={"expected_page": r"login\.html"})
        with ReferenceServer(DEFAULT_APP_DIR) as server:
            runner = Runner(envelope, console, {"base_url": server.base_url}, headless=True)
            report = asyncio.run(runner.run())

        assert report.outcome == "completed"
        assert any("expected_page" in name for name, _ in console.value_requests)


class TestRunCli:
    def test_refuses_draft_without_flag(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ir_path = tmp_path / "envelope.json"
        ir_path.write_text(order_entry_envelope().model_dump_json(), encoding="utf-8")
        code = main(["run", "--ir", str(ir_path)])
        assert code == 2
        assert "draft" in capsys.readouterr().err

    def test_requires_process_or_ir(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(["run"])
        assert code == 2
        assert "--process" in capsys.readouterr().err

    def test_unknown_process_is_a_clean_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["--store", str(tmp_path / "store"), "run", "--process", "nope"])
        assert code == 2
        assert "no stored IR" in capsys.readouterr().err

    def test_missing_ir_file_is_a_clean_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["run", "--ir", str(tmp_path / "missing.json")])
        assert code == 2
        assert "cannot read" in capsys.readouterr().err

    def test_invalid_ir_file_is_a_clean_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text('{"process_id": "x"}', encoding="utf-8")
        code = main(["run", "--ir", str(bad)])
        assert code == 2
        assert "not a valid ProcessEnvelope" in capsys.readouterr().err
