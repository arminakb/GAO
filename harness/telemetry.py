"""Telemetry, cost calculation, token budget enforcement, and performance tracking.

This module provides the observability and resource governance layer for the
Graph Agent Orchestrator.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from harness.state_schema import AgentPerformance, ExecutionState, PolicyLoader

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default pricing table (USD per 1M tokens)
# ---------------------------------------------------------------------------

DEFAULT_PRICING: dict[str, dict[str, float]] = {
    # model_prefix_or_name -> {"input": USD/1M, "output": USD/1M}
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3.5-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3-haiku": {"input": 0.25, "output": 1.25},
    "gemini-1.5-pro": {"input": 1.25, "output": 5.00},
    "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
    "ollama": {"input": 0.00, "output": 0.00},
    "default": {"input": 2.50, "output": 10.00},
}


# ---------------------------------------------------------------------------
# TokenTracker Callback Handler
# ---------------------------------------------------------------------------


class TokenTracker(BaseCallbackHandler):
    """LangChain callback handler that records token consumption from LLM calls."""

    def __init__(self, model_name: str = "default") -> None:
        super().__init__()
        self.model_name = model_name
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.total_tokens: int = 0

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """Extract token usage from LLM response metadata."""
        if response.llm_output:
            token_usage = response.llm_output.get("token_usage") or response.llm_output.get("usage")
            if isinstance(token_usage, dict):
                self.prompt_tokens += int(
                    token_usage.get("prompt_tokens") or token_usage.get("input_tokens") or 0
                )
                self.completion_tokens += int(
                    token_usage.get("completion_tokens") or token_usage.get("output_tokens") or 0
                )
                self.total_tokens += int(
                    token_usage.get("total_tokens")
                    or (self.prompt_tokens + self.completion_tokens)
                    or 0
                )
                return

        # Fallback to generation-level usage metadata if available
        for generations in response.generations:
            for gen in generations:
                gen_info = getattr(gen, "generation_info", None) or {}
                if isinstance(gen_info, dict) and "usage_metadata" in gen_info:
                    meta = gen_info["usage_metadata"]
                    self.prompt_tokens += int(meta.get("input_tokens", 0))
                    self.completion_tokens += int(meta.get("output_tokens", 0))
                    self.total_tokens += int(meta.get("total_tokens", 0))


# ---------------------------------------------------------------------------
# Cost Calculator
# ---------------------------------------------------------------------------


class CostCalculator:
    """Calculates USD cost for token consumption across different models."""

    def __init__(self, pricing_table: dict[str, dict[str, float]] | None = None) -> None:
        self._pricing = pricing_table or DEFAULT_PRICING

    def _resolve_pricing(self, model: str) -> dict[str, float]:
        """Resolve the input and output rate per 1M tokens for *model*."""
        model_lower = model.lower()

        # Local / Ollama models are free
        if (
            "ollama" in model_lower
            or "llama" in model_lower
            or "mistral" in model_lower
            or "qwen" in model_lower
            or "local" in model_lower
        ):
            return {"input": 0.00, "output": 0.00}

        for key, rates in self._pricing.items():
            if key in model_lower:
                return rates

        return self._pricing.get("default", {"input": 2.50, "output": 10.00})

    def calculate(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        """Calculate total USD cost for the given token quantities."""
        rates = self._resolve_pricing(model)
        input_cost = (prompt_tokens / 1_000_000.0) * rates["input"]
        output_cost = (completion_tokens / 1_000_000.0) * rates["output"]
        return round(input_cost + output_cost, 6)


# ---------------------------------------------------------------------------
# Budget Guard
# ---------------------------------------------------------------------------


class BudgetGuard:
    """Enforces global token budget and per-agent token limits."""

    def check_budget(self, state: ExecutionState) -> tuple[bool, str]:
        """Check whether the global token budget allows further execution.

        Returns:
            (True, "") if under budget, or (False, reason) if exhausted.
        """
        if state.budget_exhausted:
            return False, "Token budget is marked exhausted in state."

        if state.token_budget > 0 and state.telemetry.total_tokens >= state.token_budget:
            msg = (
                f"Token budget exhausted: "
                f"{state.telemetry.total_tokens:,} >= {state.token_budget:,}"
            )
            return (False, msg)

        return True, ""

    def check_agent_limit(
        self,
        agent_role: str,
        tokens_used: int,
        policies: PolicyLoader,
    ) -> tuple[bool, str]:
        """Verify whether an agent invocation exceeded its configured token limit."""
        limit = policies.get_token_limit(agent_role)
        if tokens_used > limit:
            return (
                False,
                f"Agent '{agent_role}' exceeded invocation limit: {tokens_used:,} > {limit:,}",
            )
        return True, ""

    def get_budget_status(
        self,
        state: ExecutionState,
        warning_threshold_percent: int = 80,
    ) -> dict[str, Any]:
        """Return a structured summary of budget consumption and warning status."""
        used = state.telemetry.total_tokens
        budget = state.token_budget
        pct = (used / budget * 100.0) if budget > 0 else 0.0

        is_warning = pct >= warning_threshold_percent and not state.budget_exhausted
        is_exhausted = used >= budget or state.budget_exhausted

        return {
            "used": used,
            "budget": budget,
            "percent": round(pct, 2),
            "warning": is_warning,
            "exhausted": is_exhausted,
            "total_cost_usd": round(state.telemetry.total_cost_usd, 4),
        }


# ---------------------------------------------------------------------------
# Performance Tracker
# ---------------------------------------------------------------------------


class PerformanceTracker:
    """Tracks historical success rates, invocations, and token averages per agent."""

    def record_success(
        self,
        state: ExecutionState,
        agent_role: str,
        tokens_used: int,
    ) -> dict[str, AgentPerformance]:
        """Record a successful execution for *agent_role* and return updated dict."""
        perf = dict(state.agent_performance)
        agent_perf = perf.get(agent_role, AgentPerformance()).model_copy(deep=True)
        agent_perf.record(success=True, tokens_used=tokens_used)
        perf[agent_role] = agent_perf
        return perf

    def record_failure(
        self,
        state: ExecutionState,
        agent_role: str,
        tokens_used: int,
    ) -> dict[str, AgentPerformance]:
        """Record a failed execution for *agent_role* and return updated dict."""
        perf = dict(state.agent_performance)
        agent_perf = perf.get(agent_role, AgentPerformance()).model_copy(deep=True)
        agent_perf.record(success=False, tokens_used=tokens_used)
        perf[agent_role] = agent_perf
        return perf

    def get_agent_ranking(self, state: ExecutionState) -> list[tuple[str, float]]:
        """Return agent role names sorted by success rate in descending order."""
        rankings = [(role, perf.success_rate) for role, perf in state.agent_performance.items()]
        # Sort by success rate descending, then total invocations descending
        rankings.sort(
            key=lambda item: (
                item[1],
                state.agent_performance[item[0]].success_count
                + state.agent_performance[item[0]].failure_count,
            ),
            reverse=True,
        )
        return rankings
