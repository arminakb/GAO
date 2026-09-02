"""Tests for LangGraph state machine transitions, routing, and circuit breaker."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from harness.agent_executor import AgentExecutor
from harness.state_schema import (
    AgentMessage,
    AgentRole,
    AuditEntry,
    ExecutionState,
    PolicyLoader,
    RoutingDecision,
    WorkflowPhase,
)
from harness.validator import (
    _AGENT_ROLES,
    _make_route_after_agent,
    _make_route_to_agent,
    build_graph,
    compile_graph,
)


def test_compile_graph_structure(policy_loader: PolicyLoader, mock_llm: FakeListChatModel) -> None:
    """Verify compile_graph builds a valid graph with all required nodes."""
    compiled = compile_graph(policy_loader=policy_loader, llm=mock_llm)
    assert isinstance(compiled, CompiledStateGraph)

    # Check node presence: orchestrator + 13 specialist agents + interrupt_handler
    nodes = compiled.nodes
    assert "orchestrator" in nodes
    assert "interrupt_handler" in nodes
    for role in _AGENT_ROLES:
        assert role.value in nodes

    # User-defined nodes count is 15 (14 agent roles + 1 interrupt_handler)
    # Plus __start__ gives 16 nodes
    assert len(nodes) == 16


def test_start_routes_to_orchestrator(policy_loader: PolicyLoader) -> None:
    """Verify START node connects to the orchestrator node."""
    route_to = _make_route_to_agent(policy_loader)
    state = ExecutionState()
    # Default without routing_decision returns to orchestrator
    assert route_to(state) == "orchestrator"


def test_orchestrator_routes_to_planner(policy_loader: PolicyLoader) -> None:
    """Verify routing decision targeting planner routes to planner node."""
    route_to = _make_route_to_agent(policy_loader)
    state = ExecutionState(
        active_agent=AgentRole.ORCHESTRATOR,
        routing_decision=RoutingDecision(
            target_agent=AgentRole.PLANNER, reasoning="Need feature plan."
        ),
    )
    destination = route_to(state)
    assert destination == "planner"


def test_forbidden_transition_rejected(policy_loader: PolicyLoader) -> None:
    """Verify coder -> coder self-loop transition is blocked and re-routes to orchestrator."""
    route_to = _make_route_to_agent(policy_loader)
    state = ExecutionState(
        active_agent=AgentRole.ORCHESTRATOR,
        message_history=[AgentMessage(role=AgentRole.CODER, content="Wrote initial code")],
        routing_decision=RoutingDecision(
            target_agent=AgentRole.CODER, reasoning="Re-run coder without review"
        ),
    )
    destination = route_to(state)
    # Blocked self-loop returns to orchestrator for re-routing
    assert destination == "orchestrator"


def test_direct_handoff_coder_to_reviewer(policy_loader: PolicyLoader) -> None:
    """Verify coder output directly routes to reviewer bypassing orchestrator."""
    route_after = _make_route_after_agent(policy_loader)
    state = ExecutionState(
        active_agent=AgentRole.CODER,
        workflow_phase=WorkflowPhase.IMPLEMENTATION,
        last_agent_output="def feature(): pass",
    )
    destination = route_after(state)
    assert destination == "reviewer"


def test_circuit_breaker_fires_at_retry_3(policy_loader: PolicyLoader) -> None:
    """Verify retry_count >= max_retries routes to interrupt_handler."""
    route_after = _make_route_after_agent(policy_loader)
    state = ExecutionState(
        active_agent=AgentRole.CODER,
        retry_count=3,
        max_retries=3,
    )
    destination = route_after(state)
    assert destination == "interrupt_handler"


def test_budget_exhaustion_ends_graph(policy_loader: PolicyLoader) -> None:
    """Verify budget_exhausted=True routes to __end__."""
    route_to = _make_route_to_agent(policy_loader)
    route_after = _make_route_after_agent(policy_loader)

    state = ExecutionState(budget_exhausted=True)
    assert route_to(state) == "__end__"
    assert route_after(state) == "__end__"


def test_completion_ends_graph(policy_loader: PolicyLoader) -> None:
    """Verify workflow_phase=COMPLETED routes to __end__."""
    route_after = _make_route_after_agent(policy_loader)
    state = ExecutionState(
        active_agent=AgentRole.REVIEWER,
        workflow_phase=WorkflowPhase.COMPLETED,
    )
    assert route_after(state) == "__end__"


def test_required_transition_enforced(policy_loader: PolicyLoader) -> None:
    """Verify after coder, graph must visit reviewer before ending."""
    route_to = _make_route_to_agent(policy_loader)
    state = ExecutionState(
        active_agent=AgentRole.ORCHESTRATOR,
        workflow_phase=WorkflowPhase.COMPLETED,
        message_history=[AgentMessage(role=AgentRole.CODER, content="Code changes")],
        routing_decision=RoutingDecision(
            target_agent=AgentRole.ORCHESTRATOR, reasoning="Task looks finished"
        ),
    )
    destination = route_to(state)
    assert destination == "reviewer"


def test_parallel_fan_out(policy_loader: PolicyLoader) -> None:
    """Verify parallel_targets emits a list of Send objects for parallel execution."""
    route_to = _make_route_to_agent(policy_loader)
    state = ExecutionState(
        active_agent=AgentRole.ORCHESTRATOR,
        routing_decision=RoutingDecision(
            target_agent=AgentRole.PLANNER,
            reasoning="Run coder and tdd_guide in parallel",
            parallel_targets=[AgentRole.CODER, AgentRole.TDD_GUIDE],
        ),
    )
    sends = route_to(state)
    assert isinstance(sends, list)
    assert len(sends) == 2
    assert all(isinstance(s, Send) for s in sends)
    assert sends[0].node == "coder"
    assert sends[1].node == "tdd_guide"


def test_audit_entry_created_on_transition(
    policy_loader: PolicyLoader, mock_llm: FakeListChatModel
) -> None:
    """Verify orchestrator execution creates an AuditEntry in state."""
    state = ExecutionState(task_description="Test audit creation")
    executor = AgentExecutor(
        agent_role=AgentRole.ORCHESTRATOR,
        prompt_path=MagicMock(exists=lambda: False),
        llm=mock_llm,
        policies=policy_loader,
        output_schema=RoutingDecision,
    )

    decision = RoutingDecision(
        target_agent=AgentRole.PLANNER,
        reasoning="Starting planning phase",
    )
    executor._llm = AsyncMock()
    executor._llm.ainvoke.return_value = decision

    import asyncio

    update = asyncio.run(executor.execute(state))
    assert "audit_trail" in update
    audit_trail: list[AuditEntry] = update["audit_trail"]
    assert len(audit_trail) == 1
    assert audit_trail[0].from_node == "orchestrator"
    assert audit_trail[0].to_node == "planner"
    assert audit_trail[0].rationale == "Starting planning phase"


@pytest.mark.asyncio
async def test_mock_agent_end_to_end_routing(policy_loader: PolicyLoader) -> None:
    """End-to-end state machine execution with deterministic mock nodes.

    Execution flow:
    1. START -> orchestrator (routes to planner)
    2. planner -> orchestrator (routes to coder)
    3. coder -> direct handoff to reviewer
    4. reviewer -> orchestrator (signals completion -> __end__)
    """
    executors: dict[str, MagicMock] = {}
    step_count = 0

    async def mock_orchestrator(state: ExecutionState) -> dict[str, Any]:
        nonlocal step_count
        step_count += 1
        history_roles = [m.role for m in state.message_history]

        if AgentRole.PLANNER not in history_roles:
            return {
                "active_agent": AgentRole.ORCHESTRATOR,
                "routing_decision": RoutingDecision(
                    target_agent=AgentRole.PLANNER, reasoning="Route to planner"
                ),
                "message_history": state.message_history
                + [AgentMessage(role=AgentRole.ORCHESTRATOR, content="To planner")],
            }
        elif AgentRole.CODER not in history_roles:
            return {
                "active_agent": AgentRole.ORCHESTRATOR,
                "routing_decision": RoutingDecision(
                    target_agent=AgentRole.CODER, reasoning="Route to coder"
                ),
                "message_history": state.message_history
                + [AgentMessage(role=AgentRole.ORCHESTRATOR, content="To coder")],
            }
        else:
            return {
                "active_agent": AgentRole.ORCHESTRATOR,
                "workflow_phase": WorkflowPhase.COMPLETED,
                "routing_decision": RoutingDecision(
                    target_agent=AgentRole.ORCHESTRATOR, reasoning="Done"
                ),
                "message_history": state.message_history
                + [AgentMessage(role=AgentRole.ORCHESTRATOR, content="Workflow completed")],
            }

    async def mock_planner(state: ExecutionState) -> dict[str, Any]:
        return {
            "active_agent": AgentRole.PLANNER,
            "workflow_phase": WorkflowPhase.PLANNING,
            "message_history": state.message_history
            + [AgentMessage(role=AgentRole.PLANNER, content="Plan created")],
        }

    async def mock_coder(state: ExecutionState) -> dict[str, Any]:
        return {
            "active_agent": AgentRole.CODER,
            "workflow_phase": WorkflowPhase.IMPLEMENTATION,
            "last_agent_output": "Code written",
            "message_history": state.message_history
            + [AgentMessage(role=AgentRole.CODER, content="Code written")],
        }

    async def mock_reviewer(state: ExecutionState) -> dict[str, Any]:
        return {
            "active_agent": AgentRole.REVIEWER,
            "workflow_phase": WorkflowPhase.REVIEW,
            "last_agent_output": "Code approved",
            "message_history": state.message_history
            + [AgentMessage(role=AgentRole.REVIEWER, content="Code approved")],
        }

    for role in _AGENT_ROLES:
        exec_mock = MagicMock()
        if role == AgentRole.PLANNER:
            exec_mock.execute = mock_planner
        elif role == AgentRole.CODER:
            exec_mock.execute = mock_coder
        elif role == AgentRole.REVIEWER:
            exec_mock.execute = mock_reviewer
        else:

            async def _dummy(state: ExecutionState, r: str = role.value) -> dict[str, Any]:
                return {"last_agent_output": f"Executed {r}"}

            exec_mock.execute = _dummy
        executors[role.value] = exec_mock

    orch_mock = MagicMock()
    orch_mock.execute = mock_orchestrator
    executors["orchestrator"] = orch_mock

    graph = build_graph(executors, policy_loader)  # type: ignore[arg-type]

    initial_state = ExecutionState(task_description="Build feature end-to-end")
    result = await graph.ainvoke(
        initial_state.model_dump(), config={"configurable": {"thread_id": "test_e2e"}}
    )

    final_state = ExecutionState.model_validate(result)
    assert final_state.workflow_phase == WorkflowPhase.COMPLETED
    roles_visited = [m.role for m in final_state.message_history]
    assert AgentRole.PLANNER in roles_visited
    assert AgentRole.CODER in roles_visited
    assert AgentRole.REVIEWER in roles_visited
