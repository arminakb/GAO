"""Tests for pluggable LLM provider factory."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.language_models import BaseChatModel

from harness.llm_provider import get_llm, get_llm_with_structured_output
from harness.state_schema import RoutingDecision


def test_get_llm_openai() -> None:
    """Test get_llm returns a ChatOpenAI instance when mocked."""
    mock_chat = MagicMock()
    mock_mod = MagicMock(ChatOpenAI=mock_chat)
    with patch.dict(sys.modules, {"langchain_openai": mock_mod}):
        llm = get_llm(provider="openai", model="gpt-4o", api_key="sk-test")
        mock_chat.assert_called_once_with(
            model="gpt-4o", max_tokens=4096, temperature=0.0, api_key="sk-test"
        )
        assert llm == mock_chat.return_value


def test_get_llm_anthropic() -> None:
    """Test get_llm returns a ChatAnthropic instance when mocked."""
    mock_chat = MagicMock()
    mock_mod = MagicMock(ChatAnthropic=mock_chat)
    with patch.dict(sys.modules, {"langchain_anthropic": mock_mod}):
        llm = get_llm(provider="anthropic", model="claude-3-5-sonnet-20241022")
        mock_chat.assert_called_once_with(
            model="claude-3-5-sonnet-20241022", max_tokens=4096, temperature=0.0
        )
        assert llm == mock_chat.return_value


def test_get_llm_google() -> None:
    """Test get_llm returns a ChatGoogleGenerativeAI instance when mocked."""
    mock_chat = MagicMock()
    mock_mod = MagicMock(ChatGoogleGenerativeAI=mock_chat)
    with patch.dict(sys.modules, {"langchain_google_genai": mock_mod}):
        llm = get_llm(provider="google", model="gemini-1.5-pro")
        mock_chat.assert_called_once_with(
            model="gemini-1.5-pro", max_output_tokens=4096, temperature=0.0
        )
        assert llm == mock_chat.return_value


def test_get_llm_ollama() -> None:
    """Test get_llm returns a ChatOllama instance when mocked."""
    mock_chat = MagicMock()
    mock_mod = MagicMock(ChatOllama=mock_chat)
    with patch.dict(sys.modules, {"langchain_ollama": mock_mod}):
        llm = get_llm(provider="ollama", model="llama3:8b")
        mock_chat.assert_called_once_with(model="llama3:8b", num_predict=4096, temperature=0.0)
        assert llm == mock_chat.return_value


def test_get_llm_unknown_raises() -> None:
    """Test unknown provider raises ValueError."""
    with pytest.raises(ValueError, match="Unsupported LLM provider 'unknown_provider'"):
        get_llm(provider="unknown_provider", model="test-model")


def test_get_llm_missing_package_raises() -> None:
    """Test missing provider package raises ImportError with installation hint."""
    with (
        patch.dict(sys.modules, {"langchain_openai": None}),
        pytest.raises(ImportError, match="The 'openai' provider requires"),
    ):
        get_llm(provider="openai", model="gpt-4o")


def test_env_var_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test get_llm falls back to LLM_PROVIDER and LLM_MODEL environment variables."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")

    mock_chat = MagicMock()
    mock_mod = MagicMock(ChatOpenAI=mock_chat)
    with patch.dict(sys.modules, {"langchain_openai": mock_mod}):
        get_llm()
        mock_chat.assert_called_once_with(model="gpt-4o-mini", max_tokens=4096, temperature=0.0)


def test_structured_output_wrapper() -> None:
    """Test get_llm_with_structured_output wraps model with with_structured_output."""
    mock_llm = MagicMock(spec=BaseChatModel)

    with patch("harness.llm_provider.get_llm", return_value=mock_llm):
        runnable = get_llm_with_structured_output(
            schema=RoutingDecision, provider="openai", model="gpt-4o"
        )
        mock_llm.with_structured_output.assert_called_once_with(RoutingDecision)
        assert runnable == mock_llm.with_structured_output.return_value
