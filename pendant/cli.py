"""pendant CLI: record, runs, align, induce, show, capture-msa, coverage.

stdlib argparse by decision D-002. The store root defaults to
./pendant_data, overridable with --store or PENDANT_STORE.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pendant.align import align_runs
from pendant.capture.collector import Collector
from pendant.capture.gate0 import render_report, run_gate0
from pendant.capture.redaction import RedactionRegistry
from pendant.induce.engine import InductionFailed, induce, log_corrections
from pendant.induce.providers import make_provider
from pendant.ir.graph import render_text
from pendant.ir.models import ProcessEnvelope
from pendant.run import Runner, StdioConsole
from pendant.store import Store


def _store(args: argparse.Namespace) -> Store:
    root = Path(args.store or os.environ.get("PENDANT_STORE", "pendant_data"))
    return Store(root)


# -- record --------------------------------------------------------------------


async def _record(args: argparse.Namespace) -> int:
    from playwright.async_api import async_playwright

    store = _store(args)
    if args.process not in [p for p, _ in store.list_processes()]:
        store.create_process(args.process, args.name or args.process)
    run_id = f"{args.process}-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
    out_dir = Path(tempfile.mkdtemp(prefix=f"pendant-{run_id}-"))
    print(f"Recording run {run_id}.")
    print("Type a note and press Enter to narrate; type 'stop' to finish.")
    print("In the browser, Alt+Shift+X blanks the last "
          f"{int(args.blank_window)} seconds.")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False, channel="chromium")
        context = await browser.new_context()
        page = await context.new_page()
        collector = Collector(
            run_id,
            out_dir,
            RedactionRegistry(),
            screenshots=not args.no_screenshots,
            blank_window_s=args.blank_window,
        )
        await collector.attach(context, page)
        await page.goto(args.url)
        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            text = line.strip()
            if not line or text.lower() == "stop":
                break
            if text:
                collector.narrate(text)
                print("  [narration anchored]")
        trace = await collector.finalize()
        await browser.close()
    store.add_run(args.process, trace, blob_dir=collector.blob_dir)
    print(f"Stored run {run_id} with {len(trace.events)} events.")
    cov = store.coverage(args.process)
    print(
        f"Estimated coverage: {(1 - cov.unseen_mass) * 100:.0f}% "
        f"(unseen variant mass {cov.unseen_mass * 100:.0f}%)"
    )
    return 0


# -- subcommand implementations ----------------------------------------------------


def cmd_record(args: argparse.Namespace) -> int:
    return asyncio.run(_record(args))


def cmd_runs(args: argparse.Namespace) -> int:
    store = _store(args)
    rows = store.list_runs(args.process)
    for run_id, created_at, event_count in rows:
        print(f"{run_id}  {created_at}  {event_count} events")
    cov = store.coverage(args.process)
    print(
        f"\nEstimated coverage: {(1 - cov.unseen_mass) * 100:.0f}% "
        f"({cov.distinct_variants} variants, {cov.singleton_variants} singletons, "
        f"unseen mass {cov.unseen_mass * 100:.0f}%)"
    )
    return 0


def cmd_align(args: argparse.Namespace) -> int:
    store = _store(args)
    traces = store.get_traces(args.process)
    if len(traces) < 2:
        print("need at least 2 runs to align", file=sys.stderr)
        return 2
    report = align_runs(traces)
    payload = report.model_dump_json(indent=2)
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
        print(f"written: {args.out}")
    else:
        print(payload)
    return 0


def cmd_induce(args: argparse.Namespace) -> int:
    store = _store(args)
    traces = store.get_traces(args.process)
    if len(traces) < 2:
        print("need at least 2 runs to induce", file=sys.stderr)
        return 2
    report = align_runs(traces)
    narration = [
        e.payload.value_redacted
        for t in traces
        for e in t.events
        if e.kind == "narration" and e.payload and e.payload.value_redacted
    ]
    exchange_dir = Path(args.exchange_dir) if args.exchange_dir else store.root / "induce_exchange"
    provider = make_provider(
        args.model,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
        exchange_dir=exchange_dir,
    )
    metrics_path = store.root / "induce_metrics.jsonl"
    try:
        process, metrics = induce(report, narration, provider, metrics_path=metrics_path)
    except InductionFailed as exc:
        print(f"induce failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(json.loads(process.model_dump_json()), indent=2))
    print(
        f"\n# attempts={metrics.attempts} first_pass={metrics.schema_first_pass} "
        f"steps={metrics.n_steps} "
        f"postcondition_rate={metrics.postcondition_proposal_rate:.2f} "
        f"questions={metrics.questions_emitted}/{metrics.conditional_regions}",
        file=sys.stderr,
    )
    if args.save_envelope:
        try:
            version = (store.latest_version(args.process) or 0) + 1
            envelope = process.to_envelope(args.process, version=version)
            store.save_envelope(envelope)
        except ValueError as exc:
            print(
                f"refusing to save IR envelope: {exc}\n"
                "Resolve null postcondition proposals (or keep the induced JSON) "
                "before --save-envelope.",
                file=sys.stderr,
            )
            return 1
        print(f"saved IR envelope {args.process} v{version} (draft)", file=sys.stderr)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    store = _store(args)
    envelope = store.get_envelope(args.process, args.version)
    print(render_text(envelope))
    return 0


def cmd_capture_msa(args: argparse.Namespace) -> int:
    out_dir = Path(args.out or "pendant_data/gate0")
    result = asyncio.run(
        run_gate0(
            out_dir,
            runs_primary=args.runs,
            runs_alt=args.runs_alt,
            screenshots=not args.no_screenshots,
        )
    )
    report = render_report(result, runs_primary=args.runs, runs_alt=args.runs_alt)
    if args.report:
        Path(args.report).write_text(report, encoding="utf-8")
    print(report)
    return 0 if result.passed else 1


def cmd_coverage(args: argparse.Namespace) -> int:
    store = _store(args)
    cov = store.coverage(args.process)
    print(json.dumps(cov.model_dump(mode="json"), indent=2))
    print(
        f"\nEstimated coverage {(1 - cov.unseen_mass) * 100:.0f}%: "
        + (
            "sufficient for promotion (threshold 10% unseen mass)."
            if cov.unseen_mass <= 0.10
            else "NOT sufficient; capture more demonstrations."
        )
    )
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    if args.ir:
        envelope = ProcessEnvelope.model_validate_json(
            Path(args.ir).read_text(encoding="utf-8")
        )
        report_root = Path(args.ir).resolve().parent
    elif args.process:
        store = _store(args)
        envelope = store.get_envelope(args.process, args.version)
        report_root = store.root
    else:
        print("run needs --process (store) or --ir (envelope JSON file)", file=sys.stderr)
        return 2
    if envelope.review_state == "draft" and not args.allow_draft:
        print(
            "refusing to run a draft graph. Review it first (pendant show), then pass "
            "--allow-draft to run the prototype anyway (Gate 3 is NOT claimed).",
            file=sys.stderr,
        )
        return 2
    params: dict[str, str] = {}
    for pair in args.param or []:
        if "=" not in pair:
            print(f"--param must be NAME=VALUE, got {pair!r}", file=sys.stderr)
            return 2
        name, value = pair.split("=", 1)
        params[name] = value
    runner = Runner(
        envelope,
        StdioConsole(),
        params,
        headless=args.headless,
        download_dir=report_root / "downloads",
    )
    report = asyncio.run(runner.run())
    report_dir = report_root / "run_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    report_path = Path(args.report_out) if args.report_out else (
        report_dir / f"{envelope.process_id}-v{envelope.version}-{stamp}.json"
    )
    report_path.write_text(
        json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8"
    )
    ok_steps = sum(1 for r in report.results if r.outcome == "ok")
    print(
        f"\nrun {report.outcome}: {ok_steps}/{len(report.results)} steps ok "
        f"({report.note or 'no faults'})"
    )
    print(f"report: {report_path}")
    return 0 if report.outcome == "completed" else 1


def cmd_log_corrections(args: argparse.Namespace) -> int:
    store = _store(args)
    per_10 = log_corrections(
        store.root / "induce_metrics.jsonl", args.process, args.corrections, args.nodes
    )
    print(f"corrections per 10 IR nodes: {per_10:.2f} (Gate 2 criterion: <= 3)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pendant",
        description="Programming-by-demonstration for browser processes (Phase 0).",
    )
    parser.add_argument("--store", help="store root (default ./pendant_data)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("record", help="record one demonstration in a headed browser")
    p.add_argument("--process", required=True)
    p.add_argument("--name", help="process display name (first recording)")
    p.add_argument("--url", required=True)
    p.add_argument("--no-screenshots", action="store_true")
    p.add_argument("--blank-window", type=float, default=10.0)
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("runs", help="list runs and coverage for a process")
    p.add_argument("--process", required=True)
    p.set_defaults(func=cmd_runs)

    p = sub.add_parser("align", help="align all runs into an AlignmentReport")
    p.add_argument("--process", required=True)
    p.add_argument("--out")
    p.set_defaults(func=cmd_align)

    p = sub.add_parser("induce", help="induce a draft process from aligned evidence")
    p.add_argument("--process", required=True)
    p.add_argument(
        "--model",
        default="claude-sonnet-4-5",
        help="model spec: bare Anthropic model, 'anthropic:<m>', 'openai:<m>', "
        "'openrouter:<m>', 'ollama:<m>', or 'file' (manual prompt/response "
        "exchange for assistants without an API, e.g. Cursor)",
    )
    p.add_argument("--base-url", help="override the provider endpoint base URL")
    p.add_argument("--api-key-env", help="name of the env var holding the API key")
    p.add_argument(
        "--exchange-dir",
        help="directory for --model file prompt/response exchange "
        "(default <store>/induce_exchange)",
    )
    p.add_argument("--save-envelope", action="store_true")
    p.set_defaults(func=cmd_induce)

    p = sub.add_parser("show", help="print the IR graph of a stored process")
    p.add_argument("--process", required=True)
    p.add_argument("--version", type=int)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("capture-msa", help="Gate 0 capture fidelity study")
    p.add_argument("--runs", type=int, default=10)
    p.add_argument("--runs-alt", type=int, default=3)
    p.add_argument("--out")
    p.add_argument("--report", default="docs/GATE0_REPORT.md")
    p.add_argument("--no-screenshots", action="store_true")
    p.set_defaults(func=cmd_capture_msa)

    p = sub.add_parser("coverage", help="Good-Turing demonstration sufficiency")
    p.add_argument("--process", required=True)
    p.set_defaults(func=cmd_coverage)

    p = sub.add_parser(
        "run", help="execute a stored IR graph in assisted mode (D-017 prototype)"
    )
    p.add_argument("--process")
    p.add_argument("--version", type=int)
    p.add_argument("--ir", help="run an envelope JSON file instead of the store")
    p.add_argument(
        "--param",
        action="append",
        metavar="NAME=VALUE",
        help="run parameter; secrets are better left out (the runner prompts without echo)",
    )
    p.add_argument("--headless", action="store_true")
    p.add_argument(
        "--allow-draft", action="store_true", help="run a draft (unreviewed) graph anyway"
    )
    p.add_argument("--report-out", help="path for the run report JSON")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser(
        "log-corrections", help="record operator corrections counted in review"
    )
    p.add_argument("--process", required=True)
    p.add_argument("--corrections", type=int, required=True)
    p.add_argument("--nodes", type=int, required=True)
    p.set_defaults(func=cmd_log_corrections)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
