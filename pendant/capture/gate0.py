"""Gate 0: recorder qualification against a scripted ground truth.

Replays the deterministic reference process N times per browser build,
then reports, against the hand-written ground truth in script.json:

- event capture fidelity (multiset match over comparable events);
- drop rate and duplicate rate;
- redaction escapes: every byte written under the output directory is
  scanned for the seeded canaries (password, card number). One escape
  is a stop-the-line defect, severity 10, no waiver;
- token-sequence reproducibility across runs and across two distinct
  browser builds (full Chrome for Testing vs Chrome Headless Shell,
  docs/DECISIONS.md D-004);
- an alignment cross-check: the captured traces are fed to align/ and
  the column classification summary is reported.
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from dataclasses import dataclass, field
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import Page, Response, async_playwright

from pendant.align import align_runs
from pendant.capture.collector import Collector
from pendant.capture.redaction import RedactionRegistry
from pendant.capture.schema import Event, RunTrace

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_APP_DIR = REPO_ROOT / "tests" / "reference_process" / "app"
DEFAULT_SCRIPT = REPO_ROOT / "tests" / "reference_process" / "script.json"

_API_RESPONSES: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {
    ("POST", "/api/login"): (200, {"ok": True, "user": "opsuser"}),
    ("POST", "/api/orders"): (201, {"id": 48219, "status": "created"}),
    ("GET", "/api/orders/48219"): (200, {"id": 48219, "status": "created", "total": 140}),
    ("POST", "/api/logout"): (200, {"ok": True}),
}


class _RefHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass  # deterministic fixture server; silence access logs

    def _api(self, method: str) -> bool:
        path = urlsplit(self.path).path
        if path.startswith("/api/discount"):
            entry: tuple[int, dict[str, Any]] | None = (200, {"percent": 10})
        else:
            entry = _API_RESPONSES.get((method, path))
        if entry is None:
            if path.startswith("/api/"):
                self.send_error(HTTPStatus.NOT_FOUND)
                return True
            return False
        status, body = entry
        payload = json.dumps(body).encode("utf-8")
        if method == "POST":
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        return True

    def do_GET(self) -> None:
        if not self._api("GET"):
            super().do_GET()

    def do_POST(self) -> None:
        if not self._api("POST"):
            self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)


class ReferenceServer:
    """Serves the reference app on one fixed ephemeral port for all runs."""

    def __init__(self, app_dir: Path) -> None:
        handler = partial(_RefHandler, directory=str(app_dir))
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"

    def __enter__(self) -> ReferenceServer:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)


# -- ground truth comparison -------------------------------------------------


def _expected_keys(script: dict[str, Any]) -> list[tuple[Any, ...]]:
    keys: list[tuple[Any, ...]] = []
    for e in script["expected_events"]:
        match e["kind"]:
            case "navigate":
                keys.append(("navigate", e["path"]))
            case "input":
                keys.append(("input", e["name"], e["value"]))
            case "click":
                keys.append(("click", e["name"]))
            case "key":
                keys.append(("key", e["keys"]))
            case "network":
                keys.append(("network", e["method"], e["path"], e["status"]))
    return keys


def _captured_key(event: Event) -> tuple[Any, ...] | None:
    if event.kind == "navigate" and event.payload is not None:
        return ("navigate", urlsplit(event.payload.value_redacted or "").path)
    if event.kind == "click" and event.target is not None:
        return ("click", event.target.name)
    if event.kind == "input" and event.target is not None:
        value = event.payload.value_redacted if event.payload else None
        return ("input", event.target.name, value)
    if event.kind == "key" and event.payload is not None:
        return ("key", "+".join(event.payload.keys))
    if event.kind == "network" and event.network is not None:
        return (
            "network",
            event.network.method,
            urlsplit(event.network.url_template).path,
            event.network.status,
        )
    return None


@dataclass
class RunScore:
    run_id: str
    build: str
    expected: int
    matched: int
    drops: int
    duplicates: int
    unexpected: int
    sequence: tuple[tuple[Any, ...], ...]

    @property
    def fidelity(self) -> float:
        return self.matched / self.expected if self.expected else 1.0


@dataclass
class Gate0Result:
    scores: list[RunScore] = field(default_factory=list)
    escape_files: list[str] = field(default_factory=list)
    build_versions: dict[str, str] = field(default_factory=dict)
    alignment_summary: dict[str, int] = field(default_factory=dict)
    alignment_full_presence: bool = False

    @property
    def fidelity(self) -> float:
        total = sum(s.expected for s in self.scores)
        return sum(s.matched for s in self.scores) / total if total else 0.0

    @property
    def drop_rate(self) -> float:
        total = sum(s.expected for s in self.scores)
        return sum(s.drops for s in self.scores) / total if total else 0.0

    @property
    def duplicate_rate(self) -> float:
        total = sum(s.expected for s in self.scores)
        return sum(s.duplicates for s in self.scores) / total if total else 0.0

    @property
    def reproducible(self) -> bool:
        return len({s.sequence for s in self.scores}) == 1

    @property
    def passed(self) -> bool:
        return self.fidelity >= 0.995 and not self.escape_files and self.reproducible


def score_run(trace: RunTrace, script: dict[str, Any], build: str) -> RunScore:
    expected = _expected_keys(script)
    captured = [k for k in (_captured_key(e) for e in trace.events) if k is not None]
    expected_counts = Counter(expected)
    captured_counts = Counter(captured)
    matched = sum((expected_counts & captured_counts).values())
    drops = sum((expected_counts - captured_counts).values())
    duplicates = sum(
        count - expected_counts[key]
        for key, count in captured_counts.items()
        if key in expected_counts and count > expected_counts[key]
    )
    unexpected = sum(
        count for key, count in captured_counts.items() if key not in expected_counts
    )
    return RunScore(
        run_id=trace.run_id,
        build=build,
        expected=len(expected),
        matched=matched,
        drops=drops,
        duplicates=duplicates,
        unexpected=unexpected,
        sequence=tuple(captured),
    )


async def _execute_step(page: Page, base_url: str, step: dict[str, Any]) -> None:
    op = step["op"]
    if op == "goto":
        await page.goto(base_url + step["path"], wait_until="load")
        return
    if op == "fill":
        await page.fill(step["selector"], step["value"])
        return
    if op == "press":
        await page.press(step["selector"], step["key"])
        return
    if op == "click":
        wait = step.get("wait_response")
        if wait:

            def predicate(response: Response, w: dict[str, Any] = wait) -> bool:
                return (
                    response.request.method == w["method"] and w["path"] in response.url
                )

            async with page.expect_response(predicate, timeout=15000):
                await page.click(step["selector"])
        else:
            await page.click(step["selector"])
        if step.get("wait_nav"):
            await page.wait_for_url(step["wait_nav"], timeout=15000)
        return
    raise ValueError(f"unknown script op: {op}")


async def run_gate0(
    out_dir: Path,
    *,
    runs_primary: int = 10,
    runs_alt: int = 3,
    app_dir: Path = DEFAULT_APP_DIR,
    script_path: Path = DEFAULT_SCRIPT,
    screenshots: bool = True,
) -> Gate0Result:
    script = json.loads(script_path.read_text(encoding="utf-8"))
    registry = RedactionRegistry()
    result = Gate0Result()
    out_dir.mkdir(parents=True, exist_ok=True)
    traces_primary: list[RunTrace] = []

    with ReferenceServer(app_dir) as server:
        async with async_playwright() as pw:
            arms = [
                ("headless-shell", {}, runs_primary),
                ("chromium-full", {"channel": "chromium"}, runs_alt),
            ]
            for build, launch_kwargs, n_runs in arms:
                if n_runs <= 0:
                    continue
                browser = await pw.chromium.launch(**launch_kwargs)  # type: ignore[arg-type]
                result.build_versions[build] = browser.version
                try:
                    for i in range(1, n_runs + 1):
                        run_id = f"ref-{build}-{i:02d}"
                        context = await browser.new_context()
                        page = await context.new_page()
                        collector = Collector(
                            run_id,
                            out_dir,
                            registry,
                            screenshots=screenshots,
                        )
                        await collector.attach(context, page)
                        for step in script["steps"]:
                            await _execute_step(page, server.base_url, step)
                        trace = await collector.finalize()
                        await context.close()
                        result.scores.append(score_run(trace, script, build))
                        if build == "headless-shell":
                            traces_primary.append(trace)
                finally:
                    await browser.close()

    # Redaction escape scan: every byte written under out_dir.
    canaries = [c.encode("utf-8") for c in script["canaries"]]
    for path in sorted(out_dir.rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            if any(canary in data for canary in canaries):
                result.escape_files.append(str(path.relative_to(out_dir)))

    # Alignment cross-check on the primary arm.
    if len(traces_primary) >= 2:
        report = align_runs(traces_primary)
        summary: Counter[str] = Counter(c.classification for c in report.columns)
        result.alignment_summary = dict(sorted(summary.items()))
        result.alignment_full_presence = all(
            len(c.runs_present) == len(report.run_ids) for c in report.columns
        )
    return result


def render_report(result: Gate0Result, *, runs_primary: int, runs_alt: int) -> str:
    lines: list[str] = []
    lines.append("# Gate 0 Report — Recorder Qualification (Capture Fidelity Study)")
    lines.append("")
    lines.append(f"Result: **{'PASS' if result.passed else 'FAIL'}**")
    lines.append("")
    lines.append(
        f"- Event capture fidelity: **{result.fidelity * 100:.2f}%** — criterion: >= 99.5%"
    )
    lines.append(f"- Drop rate: {result.drop_rate * 100:.2f}%")
    lines.append(f"- Duplicate rate: {result.duplicate_rate * 100:.2f}%")
    lines.append(
        f"- Redaction escapes: **{len(result.escape_files)}** — criterion: zero, "
        "severity 10, no waiver"
    )
    lines.append(
        f"- Token sequences reproducible across all runs and both builds: "
        f"**{'yes' if result.reproducible else 'NO'}**"
    )
    lines.append("")
    lines.append("## Study design")
    lines.append("")
    lines.append(
        f"Deterministic reference process (28 ground-truth events, hand-written in "
        f"tests/reference_process/script.json) replayed {runs_primary}x on the primary "
        f"build and {runs_alt}x on a second distinct browser build (single-machine, "
        "dual-build reproducibility leg per docs/DECISIONS.md D-004)."
    )
    lines.append("")
    for build, version in result.build_versions.items():
        lines.append(f"- {build}: version {version}")
    lines.append("")
    lines.append(
        "Seeded canaries (password typed into the login form, card number typed into "
        "the order form) were scanned for in every byte of every persisted artifact "
        "(NDJSON traces and screenshot blobs)."
    )
    if result.escape_files:
        lines.append("")
        lines.append("**ESCAPED FILES (stop the line):**")
        for f in result.escape_files:
            lines.append(f"- {f}")
    lines.append("")
    lines.append("## Per-run results")
    lines.append("")
    lines.append("| run | build | expected | matched | drops | dups | unexpected | fidelity |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for s in result.scores:
        lines.append(
            f"| {s.run_id} | {s.build} | {s.expected} | {s.matched} | {s.drops} "
            f"| {s.duplicates} | {s.unexpected} | {s.fidelity * 100:.1f}% |"
        )
    lines.append("")
    lines.append("## Alignment cross-check (primary arm)")
    lines.append("")
    if result.alignment_summary:
        lines.append(
            f"Column classes from align/ over the captured traces: "
            f"{result.alignment_summary}; full run presence in every column: "
            f"{'yes' if result.alignment_full_presence else 'NO'}."
        )
    else:
        lines.append("Not computed (fewer than two primary-arm traces).")
    lines.append("")
    lines.append(
        "Screenshot masking is selector-driven (registry mask keywords); the byte-level "
        "canary scan cannot detect PII rendered as pixels, so mask coverage was "
        "verified by selector construction and a manual spot check of blobs remains a "
        "reviewer duty when the registry changes."
    )
    lines.append("")
    return "\n".join(lines)
