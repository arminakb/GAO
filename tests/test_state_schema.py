"""Tests for ExecutionState data contracts, serialization, and history trimming."""

from __future__ import annotations

import json
from pathlib import Path

from harness.state_schema import (
    AgentMessage,
    AgentPerformance,
    AgentRole,
    ExecutionState,
    WorkflowPhase,
)


def test_execution_state_instantiates_defaults() -> None:
    """Verify ExecutionState instantiates cleanly with all defaults."""
    state = ExecutionState()
    assert state.task_description == ""
    assert state.active_agent == AgentRole.ORCHESTRATOR
    assert state.workflow_phase == WorkflowPhase.INITIALIZATION
    assert state.message_history == []
    assert state.retry_count == 0
    assert state.max_retries == 3
    assert state.token_budget == 100_000
    assert not state.budget_exhausted
    assert not state.is_interrupted
    assert state.routing_decision is None


def test_state_model_dump_json() -> None:
    """Verify model_dump_json produces valid JSON string."""
    state = ExecutionState(task_description="Test task")
    dumped = state.model_dump_json(indent=2)
    parsed = json.loads(dumped)
    assert parsed["task_description"] == "Test task"
    assert parsed["active_agent"] == "orchestrator"


def test_state_serialization_roundtrip(tmp_path: Path, sample_state: ExecutionState) -> None:
    """Verify atomic serialization and deserialization preserves all fields."""
    target_file = tmp_path / "state.json"
    sample_state.serialize_to_file(target_file)

    assert target_file.exists()
    loaded_state = ExecutionState.load_from_file(target_file)

    assert loaded_state.task_description == sample_state.task_description
    assert loaded_state.active_agent == sample_state.active_agent
    assert loaded_state.workflow_phase == sample_state.workflow_phase
    assert len(loaded_state.message_history) == len(sample_state.message_history)
    assert loaded_state.message_history[0].content == sample_state.message_history[0].content
    assert loaded_state.routing_decision is not None
    assert loaded_state.routing_decision.target_agent == AgentRole.PLANNER


def test_sliding_window_trims_history() -> None:
    """Verify sliding window trims history to max_history_size (50 by default)."""
    state = ExecutionState(max_history_size=50)
    for i in range(60):
        state.message_history.append(AgentMessage(role=AgentRole.CODER, content=f"Message {i}"))

    assert len(state.message_history) == 60
    state.trim_history()
    assert len(state.message_history) == 50
    assert state.message_history[0].content == "Message 10"
    assert state.message_history[-1].content == "Message 59"


def test_compute_snapshot_hash() -> None:
    """Verify snapshot hash computation is deterministic and tamper-evident."""
    state1 = ExecutionState(task_description="Task 1")
    state2 = ExecutionState(task_description="Task 1")
    state3 = ExecutionState(task_description="Task 2")

    assert state1.compute_snapshot_hash() == state2.compute_snapshot_hash()
    assert state1.compute_snapshot_hash() != state3.compute_snapshot_hash()


def test_agent_performance_metrics() -> None:
    """Verify AgentPerformance correctly records invocation outcomes."""
    perf = AgentPerformance()
    assert perf.success_rate == 0.0

    perf.record(success=True, tokens_used=1000)
    assert perf.success_count == 1
    assert perf.failure_count == 0
    assert perf.success_rate == 1.0
    assert perf.avg_tokens_per_invocation == 1000.0

    perf.record(success=False, tokens_used=2000)
    assert perf.success_count == 1
    assert perf.failure_count == 1
    assert perf.success_rate == 0.5
    assert perf.avg_tokens_per_invocation == 1500.0
