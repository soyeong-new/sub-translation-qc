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
