"""Provider layer (D-016): factory parsing, key handling, file exchange.

No network: OpenAICompatProvider is tested at the payload/parsing
level; FileExchangeProvider is driven with a wait callback standing in
for the operator.
"""

from pathlib import Path

import pytest

from pendant.induce import (
    AnthropicProvider,
    FileExchangeProvider,
    OpenAICompatProvider,
    make_provider,
)


class TestMakeProvider:
    def test_bare_model_defaults_to_anthropic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        provider = make_provider("claude-sonnet-4-5")
        assert isinstance(provider, AnthropicProvider)
        assert provider.name == "anthropic:claude-sonnet-4-5"

    def test_anthropic_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        provider = make_provider("anthropic:claude-opus-5")
        assert isinstance(provider, AnthropicProvider)
        assert provider.model == "claude-opus-5"

    def test_openai_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        provider = make_provider("openai:gpt-5")
        assert isinstance(provider, OpenAICompatProvider)
        assert provider.base_url == "https://api.openai.com/v1"

    def test_openrouter_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        provider = make_provider("openrouter:meta-llama/llama-4")
        assert isinstance(provider, OpenAICompatProvider)
        assert provider.base_url == "https://openrouter.ai/api/v1"

    def test_ollama_is_keyless_and_keeps_model_colons(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
        provider = make_provider("ollama:qwen3:32b")
        assert isinstance(provider, OpenAICompatProvider)
        assert provider.model == "qwen3:32b"
        assert provider.base_url == "http://localhost:11434/v1"

    def test_file_spec(self, tmp_path: Path) -> None:
        provider = make_provider("file", exchange_dir=tmp_path / "xchg")
        assert isinstance(provider, FileExchangeProvider)

    def test_base_url_and_key_env_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_KEY", "k")
        provider = make_provider(
            "openai:local-model",
            base_url="https://llm.internal.example/v1/",
            api_key_env="MY_KEY",
        )
        assert isinstance(provider, OpenAICompatProvider)
        assert provider.base_url == "https://llm.internal.example/v1"
        assert provider.api_key == "k"

    def test_unknown_prefix_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown provider prefix"):
            make_provider("cursor:gpt-5")


class TestKeyHandling:
    def test_anthropic_requires_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            AnthropicProvider()

    def test_openai_compat_requires_key_for_remote(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            OpenAICompatProvider("gpt-5")

    def test_openai_compat_allows_keyless_localhost(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        provider = OpenAICompatProvider("m", base_url="http://localhost:8000/v1")
        assert provider.api_key == ""


class TestOpenAICompatWireFormat:
    def test_request_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        provider = OpenAICompatProvider("gpt-5")
        payload = provider._request_payload("hello")
        assert payload["model"] == "gpt-5"
        assert payload["messages"] == [{"role": "user", "content": "hello"}]

    def test_extract_text_string_content(self) -> None:
        body: dict[str, object] = {
            "choices": [{"message": {"role": "assistant", "content": "{}"}}]
        }
        assert OpenAICompatProvider._extract_text(body) == "{}"

    def test_extract_text_part_list_content(self) -> None:
        body: dict[str, object] = {
            "choices": [
                {"message": {"content": [{"type": "text", "text": "a"}, {"text": "b"}]}}
            ]
        }
        assert OpenAICompatProvider._extract_text(body) == "ab"

    def test_extract_text_rejects_empty_choices(self) -> None:
        with pytest.raises(RuntimeError, match="no choices"):
            OpenAICompatProvider._extract_text({"choices": []})

    def test_extract_text_rejects_missing_content(self) -> None:
        with pytest.raises(RuntimeError, match="no text content"):
            OpenAICompatProvider._extract_text({"choices": [{"message": {}}]})


class TestFileExchange:
    def test_round_trip_via_wait_callback(self, tmp_path: Path) -> None:
        def operator(prompt_path: Path, response_path: Path) -> None:
            assert prompt_path.read_text(encoding="utf-8") == "the prompt"
            response_path.write_text('{"ok": true}', encoding="utf-8")

        provider = FileExchangeProvider(tmp_path / "xchg", wait=operator)
        assert provider.complete("the prompt") == '{"ok": true}'
        assert (tmp_path / "xchg" / "prompt_01.md").exists()

    def test_attempts_get_numbered_files(self, tmp_path: Path) -> None:
        def operator(prompt_path: Path, response_path: Path) -> None:
            response_path.write_text(prompt_path.name, encoding="utf-8")

        provider = FileExchangeProvider(tmp_path, wait=operator)
        assert provider.complete("first") == "prompt_01.md"
        assert provider.complete("retry") == "prompt_02.md"

    def test_preplaced_response_skips_wait(self, tmp_path: Path) -> None:
        (tmp_path / "response_01.json").write_text("canned", encoding="utf-8")

        def operator(prompt_path: Path, response_path: Path) -> None:
            raise AssertionError("wait should not be called")

        provider = FileExchangeProvider(tmp_path, wait=operator)
        assert provider.complete("p") == "canned"

    def test_missing_response_raises(self, tmp_path: Path) -> None:
        provider = FileExchangeProvider(tmp_path, wait=lambda p, r: None)
        with pytest.raises(RuntimeError, match="no response file"):
            provider.complete("p")
