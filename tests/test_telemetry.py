"""Tests for Telemetry, CostCalculator, BudgetGuard, and PerformanceTracker."""

from __future__ import annotations

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from harness.state_schema import AgentPerformance, ExecutionState, PolicyLoader
from harness.telemetry import (
    BudgetGuard,
    CostCalculator,
    PerformanceTracker,
    TokenTracker,
)


def test_token_tracker_captures_usage() -> None:
    """Test TokenTracker records token counts on on_llm_end callback."""
    tracker = TokenTracker(model_name="gpt-4o")
    result = LLMResult(
        generations=[[ChatGeneration(message=AIMessage(content="Hello!"))]],
        llm_output={
            "token_usage": {"prompt_tokens": 150, "completion_tokens": 50, "total_tokens": 200}
        },
    )
    tracker.on_llm_end(result)
    assert tracker.prompt_tokens == 150
    assert tracker.completion_tokens == 50
    assert tracker.total_tokens == 200


def test_cost_calculator_openai() -> None:
    """Test CostCalculator returns accurate USD amounts for OpenAI models."""
    calc = CostCalculator()
    # gpt-4o: $2.50 / 1M in, $10.00 / 1M out
    # 1,000,000 in + 1,000,000 out = $12.50
    cost = calc.calculate("gpt-4o", 1_000_000, 1_000_000)
    assert cost == 12.50

    # 500 in, 200 out
    small_cost = calc.calculate("gpt-4o", 500, 200)
    expected = (500 / 1_000_000 * 2.50) + (200 / 1_000_000 * 10.00)
    assert round(small_cost, 6) == round(expected, 6)


def test_cost_calculator_local_is_free() -> None:
    """Test CostCalculator returns $0.00 for local / Ollama models."""
    calc = CostCalculator()
    assert calc.calculate("ollama/llama3", 10_000, 5_000) == 0.0
    assert calc.calculate("llama3:8b", 50_000, 20_000) == 0.0
    assert calc.calculate("mistral-7b", 100_000, 50_000) == 0.0


def test_budget_guard_allows_under_budget() -> None:
    """Test check_budget returns True when within token budget."""
    guard = BudgetGuard()
    state = ExecutionState(token_budget=100_000)
    state.telemetry.total_tokens = 50_000

    allowed, reason = guard.check_budget(state)
    assert allowed
    assert reason == ""


def test_budget_guard_blocks_over_budget() -> None:
    """Test check_budget returns False when at or over budget."""
    guard = BudgetGuard()
    state = ExecutionState(token_budget=100_000)
    state.telemetry.total_tokens = 100_000

    allowed, reason = guard.check_budget(state)
    assert not allowed
    assert "Token budget exhausted" in reason


def test_budget_warning_at_threshold() -> None:
    """Test get_budget_status raises warning when token usage reaches 80%."""
    guard = BudgetGuard()
    state = ExecutionState(token_budget=100_000)
    state.telemetry.total_tokens = 85_000

    status = guard.get_budget_status(state, warning_threshold_percent=80)
    assert status["percent"] == 85.0
    assert status["warning"] is True
    assert status["exhausted"] is False

    # Under threshold
    state.telemetry.total_tokens = 70_000
    status_under = guard.get_budget_status(state, warning_threshold_percent=80)
    assert status_under["warning"] is False


def test_budget_guard_agent_limit(policy_loader: PolicyLoader) -> None:
    """Test check_agent_limit verifies per-agent limits against policies."""
    guard = BudgetGuard()
    # orchestrator limit is 2500
    ok, _ = guard.check_agent_limit("orchestrator", 2000, policy_loader)
    assert ok

    not_ok, reason = guard.check_agent_limit("orchestrator", 3000, policy_loader)
    assert not not_ok
    assert "exceeded invocation limit" in reason


def test_performance_tracker_success(sample_state: ExecutionState) -> None:
    """Test PerformanceTracker correctly updates metrics on success."""
    tracker = PerformanceTracker()
    updated = tracker.record_success(sample_state, "coder", 1500)

    perf: AgentPerformance = updated["coder"]
    assert perf.success_count == 1
    assert perf.failure_count == 0
    assert perf.success_rate == 1.0
    assert perf.total_tokens_used == 1500


def test_performance_tracker_failure(sample_state: ExecutionState) -> None:
    """Test PerformanceTracker correctly updates metrics on failure."""
    tracker = PerformanceTracker()
    updated = tracker.record_failure(sample_state, "coder", 500)

    perf: AgentPerformance = updated["coder"]
    assert perf.success_count == 0
    assert perf.failure_count == 1
    assert perf.success_rate == 0.0


def test_agent_ranking_sorted() -> None:
    """Test get_agent_ranking returns agents sorted by success rate descending."""
    tracker = PerformanceTracker()
    state = ExecutionState()

    perf_coder = AgentPerformance(success_count=3, failure_count=1, total_tokens_used=4000)  # 75%
    perf_reviewer = AgentPerformance(
        success_count=5, failure_count=0, total_tokens_used=3000
    )  # 100%
    perf_planner = AgentPerformance(success_count=1, failure_count=1, total_tokens_used=2000)  # 50%

    state.agent_performance = {
        "coder": perf_coder,
        "reviewer": perf_reviewer,
        "planner": perf_planner,
    }

    rankings = tracker.get_agent_ranking(state)
    assert rankings[0] == ("reviewer", 1.0)
    assert rankings[1] == ("coder", 0.75)
    assert rankings[2] == ("planner", 0.5)
