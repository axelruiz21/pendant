<p align="center">
  <img src="docs/assets/logo.png" alt="PENDANT" width="280">
</p>

<h1 align="center">PENDANT</h1>

<p align="center">
  <strong>Programming by demonstration for browser processes.</strong><br>
  Teach a process the way you teach a robot: lead it through, then harden the frames,<br>
  guards, timeouts, and fault routines that survive variance the demo never showed.
</p>

<p align="center">
  <a href="#phase-0-status">Phase 0</a> ·
  <a href="#install">Install</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="docs/README.md">Docs</a>
</p>

---

A demonstrated trajectory is the least valuable part of a taught program. PENDANT is optimized for extracting structure from **multiple** demonstrations and eliciting decision logic from the operator—not for replaying a single recording.

**Phase 0 scope:** browser-only capture through a printable, schema-valid IR graph. No compiler, runner, or review web client yet.

## Phase 0 status

| Gate | Criterion | Result |
|------|-----------|--------|
| **0** Recorder | Fidelity ≥ 99.5%, zero redaction escapes, reproducible tokens | **PASS** — [report](docs/GATE0_REPORT.md) |
| **1** Aligner | ≥ 95% column accuracy, zero conditional→invariant | **PASS** — [report](docs/GATE1_REPORT.md) |
| **2** Inductor | Real pilot: corrections / postconditions / questions | **Pending** pilot + baseline |

## Install

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/axelruiz21/pendant.git
cd pendant
uv sync --all-groups
uv run playwright install chromium
```

## Quick start

```bash
# Qualify the recorder (Gate 0 harness)
uv run pendant capture-msa --runs 10 --runs-alt 3

# Record a demonstration (headed browser)
uv run pendant record --process orders --name "Order entry" --url https://example.com

# Coverage (Good-Turing; never use raw run count for promotion)
uv run pendant coverage --process orders

# Align → induce (default model is Anthropic; needs ANTHROPIC_API_KEY)
uv run pendant align --process orders --out alignment.json
uv run pendant induce --process orders --save-envelope

# Any other model works too (D-016): OpenAI-compatible endpoints,
# local models, or a manual file exchange for assistants without an
# API (e.g. Cursor — open the prompt file, save the JSON reply back)
uv run pendant induce --process orders --model openai:gpt-5
uv run pendant induce --process orders --model ollama:qwen3:32b
uv run pendant induce --process orders --model file

# Print the draft IR
uv run pendant show --process orders
```

CLI commands: `record`, `runs`, `align`, `induce`, `show`, `capture-msa`, `coverage`, `log-corrections`.

## Architecture

Data flows left to right. Each arrow is a serializable schema.

```
capture → store → align → induce → ir → (review) → (compile) → (run)
  Phase 0 ─────────────────────────────────────     Phase 1+
```

| Module | Role |
|--------|------|
| `capture/` | CDP recorder (Playwright + raw CDP); redaction before any disk write |
| `store/` | SQLite + content-addressed blobs; Good-Turing promotion gate |
| `align/` | Deterministic MSA (Needleman–Wunsch, affine gaps)—never an LLM |
| `induce/` | Schema-constrained LLM, any provider (Anthropic, OpenAI-compatible, file exchange); reject-and-retry; Gate 2 instrumentation |
| `ir/` | Pydantic v2 reliability contract: mandatory postconditions + finite timeouts |

**Binding invariants** (never relaxed under schedule pressure): immutable evidence, deterministic alignment, in-collector redaction, identity-vector locators, no empty postconditions / unbounded waits, schema-validated LLM output. Full list: [CLAUDE.md](CLAUDE.md).

## Documentation

| Doc | What it is |
|-----|------------|
| [docs/README.md](docs/README.md) | Documentation index |
| [CLAUDE.md](CLAUDE.md) | Governing document (Phase 0 bootstrap) |
| [docs/IR.md](docs/IR.md) | IR formal specification |
| [docs/CAPTURE.md](docs/CAPTURE.md) | Recorder design & qualification notes |
| [docs/FMEA.md](docs/FMEA.md) | Failure modes (redaction escape S10, silent success, …) |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Architectural decisions log |
| [docs/BASELINE.md](docs/BASELINE.md) | Pilot selection (Pareto + ECRS) & manual cycle-time study |

## Development

```bash
uv run pytest
uv run ruff check pendant tests
uv run mypy
```

Python 3.12+, uv, ruff, mypy strict, Pydantic v2, pytest. The aligner is pure and tested directly—never mocked.

## License

All rights reserved until a license is chosen. Ask before redistributing.
