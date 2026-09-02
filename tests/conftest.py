"""Shared test fixtures for the Graph Agent Orchestrator test suite."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from harness.state_schema import (
    AgentMessage,
    AgentPerformance,
    AgentRole,
    AuditEntry,
    ExecutionState,
    ExecutionTelemetry,
    PolicyLoader,
    RoutingDecision,
    WorkflowPhase,
)


@pytest.fixture
def mock_llm() -> FakeListChatModel:
    """Returns a FakeListChatModel that returns predetermined responses."""
    return FakeListChatModel(responses=["Hello, world!", "Task completed successfully."])


@pytest.fixture
def policy_loader() -> PolicyLoader:
    """Returns a PolicyLoader loaded with default policies."""
    return PolicyLoader()


@pytest.fixture
def mock_routing_decision() -> RoutingDecision:
    """Returns a RoutingDecision targeting the planner agent."""
    return RoutingDecision(
        target_agent=AgentRole.PLANNER,
        reasoning="Task requires initial planning and decomposition.",
        priority="medium",
    )


@pytest.fixture
def sample_audit_trail() -> list[AuditEntry]:
    """Returns a list of AuditEntry objects for visualization tests."""
    return [
        AuditEntry(
            timestamp=datetime.now(UTC),
            from_node="orchestrator",
            to_node="planner",
            rationale="Initial plan decomposition",
            state_snapshot_hash="hash-12345",
        ),
        AuditEntry(
            timestamp=datetime.now(UTC),
            from_node="planner",
            to_node="architect",
            rationale="Plan ready, moving to architecture",
            state_snapshot_hash="hash-67890",
        ),
    ]


@pytest.fixture
def sample_state(mock_routing_decision: RoutingDecision) -> ExecutionState:
    """Returns an ExecutionState with realistic sample data."""
    return ExecutionState(
        task_description="Build an intent-driven multi-agent orchestrator",
        active_agent=AgentRole.ORCHESTRATOR,
        workflow_phase=WorkflowPhase.PLANNING,
        last_agent_output="System prompt analyzed and ready for planning.",
        message_history=[
            AgentMessage(
                role=AgentRole.ORCHESTRATOR,
                content="Started orchestrator workflow.",
                token_count=150,
                cost_usd=0.001,
            )
        ],
        retry_count=0,
        max_retries=3,
        error_log=[],
        is_interrupted=False,
        interrupt_reason=None,
        telemetry=ExecutionTelemetry(
            total_tokens=150,
            total_cost_usd=0.001,
            node_invocations={"orchestrator": 1},
            node_token_usage={"orchestrator": 150},
            node_latency_ms={"orchestrator": [250.0]},
        ),
        token_budget=100_000,
        budget_exhausted=False,
        audit_trail=[],
        agent_performance={
            "orchestrator": AgentPerformance(
                success_count=1,
                failure_count=0,
                total_tokens_used=150,
                avg_tokens_per_invocation=150.0,
            )
        },
        routing_decision=mock_routing_decision,
        direct_handoff=None,
    )
