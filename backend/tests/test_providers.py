import os
import pytest
from app.providers.base import get_provider, ProviderNotConfiguredError


def test_mock_provider_blocked_outside_pytest(monkeypatch):
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    with pytest.raises(ProviderNotConfiguredError):
        get_provider()


def test_mock_provider_allowed_inside_pytest(monkeypatch):
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_providers.py::test")
    provider = get_provider()
    assert provider.__class__.__name__ == "MockProvider"


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("QC_PROVIDER", "nonexistent")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    with pytest.raises(ProviderNotConfiguredError):
        get_provider()


def test_live_provider_selected_when_configured(monkeypatch):
    monkeypatch.setenv("QC_PROVIDER", "live")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "c")
    monkeypatch.setenv("CLAUDE_MODEL", "cm")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setenv("GPT_MODEL", "om")
    provider = get_provider()
    assert provider.__class__.__name__ == "LiveModelProvider"


def test_live_provider_raises_when_api_key_missing(monkeypatch):
    monkeypatch.setenv("QC_PROVIDER", "live")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setenv("GPT_MODEL", "om")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProviderNotConfiguredError):
        get_provider()


def test_live_provider_defaults_transcribe_model_to_gpt_4o_mini_transcribe(monkeypatch):
    monkeypatch.setenv("QC_PROVIDER", "live")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "c")
    monkeypatch.setenv("CLAUDE_MODEL", "cm")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setenv("GPT_MODEL", "om")
    monkeypatch.delenv("GPT_TRANSCRIBE_MODEL", raising=False)
    provider = get_provider()
    assert provider._gpt._transcribe_model == "gpt-4o-mini-transcribe"


def test_live_provider_still_honors_explicit_transcribe_model_env_var(monkeypatch):
    monkeypatch.setenv("QC_PROVIDER", "live")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "c")
    monkeypatch.setenv("CLAUDE_MODEL", "cm")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setenv("GPT_MODEL", "om")
    monkeypatch.setenv("GPT_TRANSCRIBE_MODEL", "whisper-1")
    provider = get_provider()
    assert provider._gpt._transcribe_model == "whisper-1"


def test_mock_provider_check_grammar_necessity_flags_estoy_cansada_style_lines(monkeypatch):
    import asyncio
    from app.providers.mock import MockProvider
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    provider = MockProvider()
    result = asyncio.get_event_loop().run_until_complete(
        provider.check_grammar_necessity(
            pairs=[{"id": "p1", "target_text": "necesita ayuda"}],
            profile={},
        )
    )
    assert result == [{"id": "p1", "gender_check_needed": False, "formality_check_needed": False}]
