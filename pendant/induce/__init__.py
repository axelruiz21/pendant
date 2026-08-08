"""induce: prompt assembly, schema-constrained LLM calls, validation,
instrumentation.

The model never aligns traces (invariant 2); it receives an
AlignmentReport as evidence. Output is schema-validated before
persistence and rejected-and-retried on failure (invariant 6).
Uncertainty must surface as clarifying questions, never plausible
guesses. Every invocation logs the instrumentation metrics from
invariant 10.
"""

from pendant.induce.engine import (
    InductionFailed,
    InductionMetrics,
    induce,
)
from pendant.induce.providers import (
    AnthropicProvider,
    FileExchangeProvider,
    LLMProvider,
    OpenAICompatProvider,
    ReplayProvider,
    make_provider,
)
from pendant.induce.schema import (
    ConditionalQuestion,
    InducedProcess,
    InducedStep,
    PostconditionProposal,
)

__all__ = [
    "AnthropicProvider",
    "ConditionalQuestion",
    "FileExchangeProvider",
    "InducedProcess",
    "InducedStep",
    "InductionFailed",
    "InductionMetrics",
    "LLMProvider",
    "OpenAICompatProvider",
    "PostconditionProposal",
    "ReplayProvider",
    "induce",
    "make_provider",
]
