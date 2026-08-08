"""Runner (D-017): assisted execution against the reference process.

Integration tests drive a real headless Chromium against the same
local reference app Gate 0 uses; the operator is a scripted console,
so approvals, branch questions, and secret prompts are exercised
deterministically. No mocks of runner internals.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from pendant.capture.gate0 import DEFAULT_APP_DIR, ReferenceServer
from pendant.cli import main
from pendant.ir.models import IR_SCHEMA_VERSION, ProcessEnvelope, Step
from pendant.run import Runner
from pendant.run.predicates import resolve_template


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
                "target_vector": _target("username"),
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
                {"kind": "http_status", "args": {"url_template": "/api/login", "status": 200}},
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
        ),
        _step(
            "s_create",
            "Create order",
            {"type": "click", "target_vector": _target("create-order")},
            [
                {"kind": "http_status", "args": {"url_template": "/api/orders", "status": 201}},
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
