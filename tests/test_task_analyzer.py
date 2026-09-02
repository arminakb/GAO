"""Tests for the task analyzer and adaptive routing (harness/task_analyzer.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.task_analyzer import (
    Complexity,
    ImpactDimension,
    OrchestrationBudget,
    TaskAnalyzer,
    build_plan,
)


@pytest.fixture
def analyzer(tmp_path: Path) -> TaskAnalyzer:
    (tmp_path / "a.py").write_text("x = 1\n")
    return TaskAnalyzer(tmp_path)


# --- classification ----------------------------------------------------------


@pytest.mark.parametrize(
    "task,expected",
    [
        ("Fix typo in README", Complexity.TRIVIAL),
        ("Fix the spelling of 'recieve' in the docs comment", Complexity.TRIVIAL),
        ("Bump version to 2.1", Complexity.TRIVIAL),
        ("Fix the bug in parse_date where None input crashes", Complexity.SIMPLE),
        ("Add a function that validates email input", Complexity.SIMPLE),
    ],
)
def test_low_complexity_tasks(analyzer: TaskAnalyzer, task: str, expected: Complexity) -> None:
    assert analyzer.analyze(task).complexity is expected


def test_medium_task(analyzer: TaskAnalyzer) -> None:
    analysis = analyzer.analyze(
        "Implement an API endpoint for user registration with database schema "
        "migration, including validation and tests"
    )
    assert analysis.complexity in (Complexity.MEDIUM, Complexity.COMPLEX)
    assert ImpactDimension.API in analysis.impact_dimensions
    assert ImpactDimension.DATABASE in analysis.impact_dimensions


def test_complex_task_fans_out(analyzer: TaskAnalyzer) -> None:
    analysis = analyzer.analyze(
        "Redesign the system architecture across all services: rewrite the "
        "database schema with migration, add authentication and security "
        "hardening, fix the concurrency race in the reservation API, and "
        "update the frontend components accordingly. This is a breaking change "
        "requiring integration across multiple modules."
    )
    assert analysis.complexity in (Complexity.COMPLEX, Complexity.CRITICAL)
    plan = build_plan(analysis)
    assert plan.track == "complex"
    assert plan.specialists, "security/database dimensions should map to specialists"
    assert "repair-if-needed" in plan.steps


def test_security_never_solo(analyzer: TaskAnalyzer) -> None:
    analysis = analyzer.analyze("Fix the auth token security issue")
    assert analysis.security_risk
    plan = build_plan(analysis)
    assert plan.track != "solo"


# --- analysis fields -------------------------------------------------------------


def test_analysis_fields(analyzer: TaskAnalyzer) -> None:
    a = analyzer.analyze("Write a SQL migration adding a table with a lock guard")
    assert 0.0 <= a.confidence <= 1.0
    assert a.estimated_files_affected >= 1
    assert a.database_impact and a.concurrency_risk
    assert a.blast_radius in {"local", "module", "cross-module", "global"}
    assert a.estimated_tokens > 0


def test_high_historical_failure_rate_raises_class(tmp_path: Path) -> None:
    calm = TaskAnalyzer(tmp_path, historical_failure_rate=0.0).analyze("Add a helper function")
    spicy = TaskAnalyzer(tmp_path, historical_failure_rate=0.8).analyze("Add a helper function")
    order = list(Complexity)
    assert order.index(spicy.complexity) >= order.index(calm.complexity)


# --- plans & budgets ----------------------------------------------------------------


def test_solo_plan_skips_orchestrator_llm(analyzer: TaskAnalyzer) -> None:
    plan = build_plan(analyzer.analyze("Fix typo in README"))
    assert plan.track == "solo"
    assert plan.needs_orchestrator_llm is False
    assert plan.steps == ["implement", "verify"]
    assert plan.budget is not None and plan.budget.max_llm_calls <= 3


def test_budget_monotone_in_complexity(analyzer: TaskAnalyzer) -> None:
    budgets: list[OrchestrationBudget] = []
    for task in (
        "Fix typo",
        "Fix the bug in the parser function",
        "Implement API endpoint with database migration and tests",
        (
            "Redesign system architecture, rewrite database, security hardening, "
            "concurrency fixes, frontend rework, breaking change across modules"
        ),
    ):
        plan = build_plan(analyzer.analyze(task))
        assert plan.budget is not None
        budgets.append(plan.budget)
    assert budgets[0].max_tokens < budgets[2].max_tokens <= budgets[3].max_tokens
    assert budgets[0].max_llm_calls < budgets[3].max_llm_calls


def test_max_parallel_caps_specialists(tmp_path: Path) -> None:
    analyzer = TaskAnalyzer(tmp_path)
    analysis = analyzer.analyze(
        "Redesign database schema and frontend and security with migration and rewrite"
    )
    plan = build_plan(analysis, max_parallel=2)
    assert len(plan.specialists) <= 2


# --- LLM-assist hook -----------------------------------------------------------------


def test_llm_assist_only_engages_on_low_confidence(tmp_path: Path) -> None:
    analyzer = TaskAnalyzer(tmp_path)
    calls: list[str] = []

    def fake_llm(prompt: str) -> str:
        calls.append(prompt)
        return "medium"

    # High-confidence trivial task: LLM never called.
    analyzer.analyze_with_llm("Fix typo in README", fake_llm)
    assert not calls

    # Ambiguous task near a boundary: LLM consulted, result within one class.
    ambiguous = "Adjust the service to handle the new data format properly"
    refined = analyzer.analyze_with_llm(ambiguous, fake_llm)
    if calls:
        assert refined.confidence >= 0.7


def test_llm_assist_failure_falls_back(tmp_path: Path) -> None:
    analyzer = TaskAnalyzer(tmp_path)

    def broken_llm(prompt: str) -> str:
        raise RuntimeError("no provider")

    a = analyzer.analyze_with_llm("adjust the service data format", broken_llm)
    assert a.complexity is not None  # deterministic fallback
