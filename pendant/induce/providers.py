"""LLM providers: pluggable model backends for the induction engine.

The engine is provider-independent (docs/DECISIONS.md D-009): its only
provider-facing surface is prompt -> completion text. D-016 generalizes
the backend set so any model can drive induction, all stdlib-only:

- AnthropicProvider     — Anthropic Messages API.
- OpenAICompatProvider  — any OpenAI-compatible /chat/completions
  endpoint: OpenAI, OpenRouter, local Ollama / LM Studio / vLLM, and
  vendor compatibility endpoints.
- FileExchangeProvider  — manual prompt/response exchange through
  files, for assistants without an inference API (e.g. Cursor: open
  the prompt file in the editor, ask its model for the JSON, save the
  raw reply as the response file).
- ReplayProvider        — deterministic canned responses for tests.
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit


class LLMProvider(Protocol):
    name: str

    def complete(self, prompt: str) -> str: ...


class ReplayProvider:
    """Deterministic provider for tests: returns canned responses in order."""

    name = "replay"

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    def complete(self, prompt: str) -> str:
        if not self._responses:
            raise RuntimeError("ReplayProvider exhausted")
        return self._responses.pop(0)


class AnthropicProvider:
    """Minimal Anthropic Messages API client (stdlib only, D-009)."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-5",
        max_tokens: int = 16000,
        base_url: str = "https://api.anthropic.com",
        api_key_env: str = "ANTHROPIC_API_KEY",
    ) -> None:
        self.name = f"anthropic:{model}"
        self.model = model
        self.max_tokens = max_tokens
        self.base_url = base_url.rstrip("/")
        self.api_key = os.environ.get(api_key_env, "")
        if not self.api_key:
            raise RuntimeError(f"{api_key_env} is not set")

    def complete(self, prompt: str) -> str:
        payload = json.dumps(
            {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/v1/messages",
            data=payload,
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        with urllib.request.urlopen(request, timeout=300) as response:
            body = json.loads(response.read().decode("utf-8"))
        return "".join(
            block.get("text", "") for block in body.get("content", []) if isinstance(block, dict)
        )


_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})


class OpenAICompatProvider:
    """Any OpenAI-compatible chat-completions endpoint (stdlib only, D-016).

    A missing API key is an error unless the endpoint host is local:
    Ollama, LM Studio, and vLLM serve without auth by default. No token
    cap is sent — `max_tokens` vs `max_completion_tokens` differs across
    servers, and induction output must not be truncated mid-JSON.
    """

    def __init__(
        self,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        api_key_env: str = "OPENAI_API_KEY",
    ) -> None:
        self.name = f"openai-compat:{model}"
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = os.environ.get(api_key_env, "")
        host = urlsplit(self.base_url).hostname or ""
        if not self.api_key and host not in _LOCAL_HOSTS:
            raise RuntimeError(f"{api_key_env} is not set and {host!r} is not a local endpoint")

    def _request_payload(self, prompt: str) -> dict[str, object]:
        return {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
        }

    @staticmethod
    def _extract_text(body: dict[str, object]) -> str:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("completion response has no choices")
        first = choices[0]
        message = first.get("message") if isinstance(first, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            return content
        if isinstance(content, list):  # some servers return typed content parts
            return "".join(p.get("text", "") for p in content if isinstance(p, dict))
        raise RuntimeError("completion response has no text content")

    def complete(self, prompt: str) -> str:
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(self._request_payload(prompt)).encode("utf-8"),
            headers=headers,
        )
        with urllib.request.urlopen(request, timeout=300) as response:
            body = json.loads(response.read().decode("utf-8"))
        if not isinstance(body, dict):
            raise RuntimeError("malformed completion response")
        return self._extract_text(body)


class FileExchangeProvider:
    """Manual prompt/response exchange through files (D-016).

    For assistants with no inference API — Cursor, chat UIs, air-gapped
    review. Attempt N writes ``prompt_NN.md``; the operator produces
    ``response_NN.json`` (the model's raw JSON reply, nothing else) with
    whatever model they like, and the provider reads it back. The
    engine's reject-and-retry loop works unchanged: a rejected attempt
    surfaces as the next prompt file.
    """

    def __init__(
        self,
        exchange_dir: Path,
        wait: Callable[[Path, Path], None] | None = None,
    ) -> None:
        self.name = f"file-exchange:{exchange_dir.name}"
        self._dir = exchange_dir
        self._wait = wait if wait is not None else self._wait_interactive
        self._attempt = 0

    @staticmethod
    def _wait_interactive(prompt_path: Path, response_path: Path) -> None:
        print(f"\nPrompt written to: {prompt_path}")
        print(f"Save the model's raw JSON reply to: {response_path}")
        input("Press Enter once the response file is saved... ")

    def complete(self, prompt: str) -> str:
        self._attempt += 1
        self._dir.mkdir(parents=True, exist_ok=True)
        prompt_path = self._dir / f"prompt_{self._attempt:02d}.md"
        response_path = self._dir / f"response_{self._attempt:02d}.json"
        prompt_path.write_text(prompt, encoding="utf-8")
        if not response_path.exists():
            self._wait(prompt_path, response_path)
        if not response_path.exists():
            raise RuntimeError(f"no response file at {response_path}")
        return response_path.read_text(encoding="utf-8")


# prefix -> (default base_url, default API-key env var)
_OPENAI_COMPAT_ALIASES: dict[str, tuple[str, str]] = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "ollama": ("http://localhost:11434/v1", "OLLAMA_API_KEY"),
}


def make_provider(
    spec: str,
    *,
    base_url: str | None = None,
    api_key_env: str | None = None,
    exchange_dir: Path | None = None,
) -> LLMProvider:
    """Build a provider from a model spec string.

    Spec forms::

        claude-sonnet-4-5        bare model -> Anthropic (back-compat)
        anthropic:<model>        Anthropic Messages API
        openai:<model>           OpenAI-compatible endpoint
        openrouter:<model>       OpenRouter (OpenAI-compatible)
        ollama:<model>           local Ollama (OpenAI-compatible, keyless)
        file                     manual file exchange (e.g. Cursor)

    ``base_url`` / ``api_key_env`` override the alias defaults, so any
    OpenAI-compatible server is reachable via ``openai:<model>`` plus a
    base URL. Model names may themselves contain colons (``ollama:qwen3:32b``);
    only the first colon separates the provider prefix.
    """
    if spec == "file":
        return FileExchangeProvider(exchange_dir or Path("induce_exchange"))
    prefix, _, rest = spec.partition(":")
    if not rest:  # bare model name
        return AnthropicProvider(model=spec, api_key_env=api_key_env or "ANTHROPIC_API_KEY")
    if prefix == "anthropic":
        return AnthropicProvider(
            model=rest,
            base_url=base_url or "https://api.anthropic.com",
            api_key_env=api_key_env or "ANTHROPIC_API_KEY",
        )
    if prefix in _OPENAI_COMPAT_ALIASES:
        default_base, default_env = _OPENAI_COMPAT_ALIASES[prefix]
        return OpenAICompatProvider(
            rest,
            base_url=base_url or default_base,
            api_key_env=api_key_env or default_env,
        )
    raise ValueError(
        f"unknown provider prefix {prefix!r}; expected one of: anthropic, "
        f"{', '.join(_OPENAI_COMPAT_ALIASES)}, file"
    )
