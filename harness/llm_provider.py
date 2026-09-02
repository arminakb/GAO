"""Pluggable LLM provider factory.

Resolves the active LLM backend from explicit arguments or environment
variables and returns a :class:`~langchain_core.language_models.BaseChatModel`
ready for invocation.

Supported providers: ``openai``, ``anthropic``, ``google``, ``ollama``.

Usage::

    from harness.llm_provider import get_llm

    llm = get_llm()                               # reads LLM_PROVIDER / LLM_MODEL env vars
    llm = get_llm("anthropic", "claude-sonnet-4-20250514")  # explicit override
"""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel

load_dotenv()

# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

_PROVIDER_MAP: dict[str, tuple[str, str]] = {
    # provider_key → (module_path, class_name)
    "openai": ("langchain_openai", "ChatOpenAI"),
    "anthropic": ("langchain_anthropic", "ChatAnthropic"),
    "google": ("langchain_google_genai", "ChatGoogleGenerativeAI"),
    "ollama": ("langchain_ollama", "ChatOllama"),
}

_INSTALL_HINTS: dict[str, str] = {
    "openai": 'pip install "graph-agent-orchestrator[openai]"',
    "anthropic": 'pip install "graph-agent-orchestrator[anthropic]"',
    "google": 'pip install "graph-agent-orchestrator[google]"',
    "ollama": 'pip install "graph-agent-orchestrator[ollama]"',
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_llm(
    provider: str | None = None,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    **kwargs: Any,
) -> BaseChatModel:
    """Instantiate and return a :class:`BaseChatModel` for *provider*.

    Parameters
    ----------
    provider:
        One of ``"openai"``, ``"anthropic"``, ``"google"``, ``"ollama"``.
        Falls back to the ``LLM_PROVIDER`` environment variable.
    model:
        The model identifier (e.g. ``"gpt-4o"``).  Falls back to ``LLM_MODEL``.
    temperature:
        Sampling temperature.  Defaults to ``0.0`` for deterministic output.
    max_tokens:
        Maximum tokens in the completion.
    **kwargs:
        Forwarded to the underlying LangChain chat model constructor.

    Raises
    ------
    ValueError
        If *provider* is not recognised or not configured.
    ImportError
        If the required LangChain integration package is not installed.
    """
    provider = provider or os.getenv("LLM_PROVIDER")
    if not provider:
        raise ValueError(
            "No LLM provider specified. Pass `provider=` or set the "
            "LLM_PROVIDER environment variable."
        )

    provider = provider.lower().strip()
    if provider not in _PROVIDER_MAP:
        supported = ", ".join(sorted(_PROVIDER_MAP))
        raise ValueError(f"Unsupported LLM provider '{provider}'. Supported providers: {supported}")

    model = model or os.getenv("LLM_MODEL")
    if not model:
        raise ValueError(
            "No model specified. Pass `model=` or set the LLM_MODEL environment variable."
        )

    module_path, class_name = _PROVIDER_MAP[provider]
    try:
        import importlib

        mod = importlib.import_module(module_path)
    except ImportError as exc:
        hint = _INSTALL_HINTS.get(provider, f"pip install {module_path}")
        raise ImportError(
            f"The '{provider}' provider requires the '{module_path}' package. "
            f"Install it with: {hint}"
        ) from exc

    cls = getattr(mod, class_name)

    # Build constructor kwargs — each provider has slightly different param names
    ctor_kwargs: dict[str, Any] = {**kwargs}
    ctor_kwargs["temperature"] = temperature

    if provider == "ollama":
        ctor_kwargs["model"] = model
        # Ollama uses num_predict instead of max_tokens
        ctor_kwargs["num_predict"] = max_tokens
        base_url = os.getenv("OLLAMA_BASE_URL")
        if base_url:
            ctor_kwargs["base_url"] = base_url
    elif provider == "google":
        ctor_kwargs["model"] = model
        ctor_kwargs["max_output_tokens"] = max_tokens
    else:
        # OpenAI and Anthropic both accept model + max_tokens
        ctor_kwargs["model"] = model
        ctor_kwargs["max_tokens"] = max_tokens

    return cls(**ctor_kwargs)  # type: ignore[no-any-return]


def get_llm_with_structured_output(
    schema: type[BaseModel],
    provider: str | None = None,
    model: str | None = None,
    **kwargs: Any,
) -> BaseChatModel:
    """Return an LLM bound to emit structured output matching *schema*.

    Wraps :func:`get_llm` and calls ``.with_structured_output(schema)``
    on the result, returning a Runnable that guarantees Pydantic-validated
    output.
    """
    llm = get_llm(provider=provider, model=model, **kwargs)
    return llm.with_structured_output(schema)  # type: ignore[return-value]
