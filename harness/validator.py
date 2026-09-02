"""LangGraph state machine for the Graph Agent Orchestrator.

This module constructs the full :class:`StateGraph`, wires all agent nodes,
defines conditional routing edges, and implements the circuit breaker.

The graph structure is::

    START → orchestrator → [conditional] → target_agent → [conditional] → orchestrator | END
                                                                        → interrupt_handler → END

Key entry points:

* :func:`build_graph` — construct and compile the graph.
* :func:`compile_graph` — high-level helper that wires executors + tools.
* :func:`run_graph` — top-level async function to execute a full task.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from harness.agent_executor import AgentExecutor
from harness.state_schema import (
    AgentRole,
    AuditEntry,
    ExecutionState,
    PolicyLoader,
    RoutingDecision,
    WorkflowPhase,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Agents directory
# ---------------------------------------------------------------------------

_AGENTS_DIR = Path(__file__).parent.parent / "agents"

# ---------------------------------------------------------------------------
# Audit trail helper
# ---------------------------------------------------------------------------


def _create_audit_entry(
    state: ExecutionState,
    from_node: str | None,
    to_node: str,
    rationale: str,
) -> AuditEntry:
    """Create an immutable audit log entry for a state transition."""
    snapshot = state.model_dump_json(exclude={"audit_trail"})
    snapshot_hash = hashlib.sha256(snapshot.encode()).hexdigest()
    return AuditEntry(
        from_node=from_node,
        to_node=to_node,
        rationale=rationale,
        state_snapshot_hash=snapshot_hash,
    )


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------


def _make_route_to_agent(
    policy_loader: PolicyLoader,
) -> Any:
    """Return the routing function used after the orchestrator node."""

    def route_to_agent(state: ExecutionState) -> list[Send] | str:
        """Determine which agent to invoke based on the routing decision.

        Validates transitions against policies before allowing them.
        """
        # Budget exhaustion → end
        if state.budget_exhausted:
            logger.info("Budget exhausted — ending graph.")
            return "__end__"

        # Already interrupted → handle interrupt
        if state.is_interrupted:
            return "interrupt_handler"

        # Read routing decision
        decision = state.routing_decision
        if decision is None:
            logger.warning("No routing decision — returning to orchestrator.")
            return "orchestrator"

        # Parallel fan-out
        if decision.parallel_targets:
            sends = []
            for target_role in decision.parallel_targets:
                target_val = (
                    target_role.value if isinstance(target_role, AgentRole) else str(target_role)
                )
                sends.append(Send(target_val, state))
            if sends:
                return sends

        target = (
            decision.target_agent.value
            if isinstance(decision.target_agent, AgentRole)
            else str(decision.target_agent)
        )

        # Check required transitions
        for rule in policy_loader.get_required_transitions():
            after_agent = rule.get("after")
            must_visit = rule.get("must_visit")
            before_target = rule.get("before")

            roles_in_history = [
                m.role.value if isinstance(m.role, AgentRole) else str(m.role)
                for m in state.message_history
            ]
            if after_agent in roles_in_history and must_visit:
                last_after_idx = max(i for i, r in enumerate(roles_in_history) if r == after_agent)
                has_visited = any(
                    i > last_after_idx and roles_in_history[i] == must_visit
                    for i in range(len(roles_in_history))
                )
                if not has_visited:
                    if before_target == "end" and (
                        target in ("__end__", AgentRole.ORCHESTRATOR.value)
                        or state.workflow_phase == WorkflowPhase.COMPLETED
                    ):
                        logger.warning(
                            "Required transition not met: must visit '%s' after '%s' before end.",
                            must_visit,
                            after_agent,
                        )
                        return must_visit
                    if before_target == target:
                        logger.warning(
                            "Required transition not met: must visit '%s' after '%s' before '%s'.",
                            must_visit,
                            after_agent,
                            before_target,
                        )
                        return must_visit

        # Completion signal — orchestrator decided task is done
        if target == AgentRole.ORCHESTRATOR.value:
            return "__end__"

        # Validate transition against policies
        previous = (
            state.active_agent.value
            if isinstance(state.active_agent, AgentRole)
            else str(state.active_agent)
        )
        if previous == AgentRole.ORCHESTRATOR.value:
            for msg in reversed(state.message_history):
                if msg.role != AgentRole.ORCHESTRATOR:
                    previous = msg.role.value if isinstance(msg.role, AgentRole) else str(msg.role)
                    break

        allowed, reason = policy_loader.is_transition_allowed(previous, target)
        if not allowed:
            logger.warning(
                "Forbidden transition %s → %s: %s. Re-routing via orchestrator.",
                previous,
                target,
                reason,
            )
            return "orchestrator"

        return target

    return route_to_agent


def _make_route_after_agent(
    policy_loader: PolicyLoader,
) -> Any:
    """Return the routing function used after any non-orchestrator agent."""

    def route_after_agent(state: ExecutionState) -> str:
        """Determine where to go after an agent completes.

        Checks circuit breaker, budget, completion, and direct handoffs.
        """
        # Circuit breaker
        if state.retry_count >= state.max_retries:
            logger.warning(
                "Circuit breaker: retry_count=%d >= max_retries=%d",
                state.retry_count,
                state.max_retries,
            )
            return "interrupt_handler"

        # Budget exhaustion
        if state.budget_exhausted:
            return "__end__"

        # Completion
        if state.workflow_phase == WorkflowPhase.COMPLETED:
            return "__end__"

        # Direct handoff
        current = state.active_agent.value
        handoff_target = policy_loader.get_direct_handoff(current)
        if handoff_target is not None:
            logger.info("Direct handoff: %s → %s", current, handoff_target)
            return handoff_target

        # Default: back to orchestrator
        return "orchestrator"

    return route_after_agent


# ---------------------------------------------------------------------------
# Interrupt handler node
# ---------------------------------------------------------------------------


async def _handle_interrupt(state: ExecutionState) -> dict[str, Any]:
    """Circuit breaker node — pauses execution and waits for human input.

    Calls :func:`langgraph.types.interrupt` with full diagnostic context
    so the human operator can understand what went wrong and decide how
    to proceed.
    """
    reason_parts: list[str] = []
    if state.retry_count >= state.max_retries:
        reason_parts.append(f"Retry limit reached ({state.retry_count}/{state.max_retries})")
    if state.budget_exhausted:
        reason_parts.append("Token budget exhausted")
    if state.interrupt_reason:
        reason_parts.append(state.interrupt_reason)

    reason = "; ".join(reason_parts) or "Manual interrupt requested"

    logger.warning("INTERRUPT: %s", reason)

    # Log audit entry
    entry = _create_audit_entry(state, state.active_agent.value, "interrupt_handler", reason)
    audit_trail = list(state.audit_trail) + [entry]

    # Trigger LangGraph interrupt — execution pauses here
    interrupt(
        {
            "reason": reason,
            "active_agent": state.active_agent.value,
            "retry_count": state.retry_count,
            "recent_errors": state.error_log[-3:],
            "telemetry_snapshot": {
                "total_tokens": state.telemetry.total_tokens,
                "total_cost_usd": state.telemetry.total_cost_usd,
            },
        }
    )

    return {
        "is_interrupted": True,
        "interrupt_reason": reason,
        "workflow_phase": WorkflowPhase.INTERRUPTED,
        "audit_trail": audit_trail,
    }


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

# All agent roles except orchestrator (orchestrator is handled separately)
_AGENT_ROLES = [role for role in AgentRole if role != AgentRole.ORCHESTRATOR]


def build_graph(
    agent_executors: dict[str, AgentExecutor],
    policy_loader: PolicyLoader,
) -> Any:
    """Construct and compile the LangGraph state machine.

    Parameters
    ----------
    agent_executors:
        Mapping of agent role names to their :class:`AgentExecutor` instances.
        Must include ``"orchestrator"`` and all 13 specialist agents.
    policy_loader:
        Shared :class:`PolicyLoader` for governance queries.

    Returns
    -------
    CompiledStateGraph
        The compiled graph ready for invocation.
    """
    graph = StateGraph(ExecutionState)

    # --- Add nodes ---

    # Orchestrator node
    orchestrator_exec = agent_executors["orchestrator"]
    graph.add_node("orchestrator", orchestrator_exec.execute)

    # Agent nodes
    for role in _AGENT_ROLES:
        name = role.value
        executor = agent_executors.get(name)
        if executor is None:
            # Create a passthrough placeholder for agents without executors yet
            logger.warning("No executor for agent '%s' — using passthrough.", name)

            async def _passthrough(state: ExecutionState, _name: str = name) -> dict[str, Any]:
                return {"last_agent_output": f"[{_name}] Passthrough — no executor configured."}

            graph.add_node(name, _passthrough)
        else:
            graph.add_node(name, executor.execute)

    # Interrupt handler node
    graph.add_node("interrupt_handler", _handle_interrupt)

    # --- Add edges ---

    # START → orchestrator
    graph.add_edge(START, "orchestrator")

    # orchestrator → conditional → target agent | END | interrupt
    route_to = _make_route_to_agent(policy_loader)
    agent_destinations: dict[str, str] = {role.value: role.value for role in _AGENT_ROLES}
    agent_destinations["orchestrator"] = "orchestrator"
    agent_destinations["interrupt_handler"] = "interrupt_handler"
    agent_destinations["__end__"] = END

    graph.add_conditional_edges("orchestrator", route_to, agent_destinations)  # type: ignore[arg-type]

    # Each agent → conditional → orchestrator | direct_handoff | interrupt | END
    route_after = _make_route_after_agent(policy_loader)
    post_agent_destinations: dict[str, str] = {
        "orchestrator": "orchestrator",
        "interrupt_handler": "interrupt_handler",
        "__end__": END,
    }
    # Add all possible direct handoff targets
    for role in _AGENT_ROLES:
        post_agent_destinations[role.value] = role.value

    for role in _AGENT_ROLES:
        graph.add_conditional_edges(role.value, route_after, post_agent_destinations)  # type: ignore[arg-type]

    # interrupt_handler → END
    graph.add_edge("interrupt_handler", END)

    # --- Compile ---
    from langgraph.checkpoint.memory import MemorySaver

    return graph.compile(checkpointer=MemorySaver())


# ---------------------------------------------------------------------------
# Tool Binding Matrix & MCP Bridge
# ---------------------------------------------------------------------------

TOOL_BINDING_MATRIX: dict[str, list[str]] = {
    "orchestrator": [],
    "planner": ["read_knowledge", "query_architecture"],
    "architect": [
        "read_knowledge",
        "search_knowledge",
        "query_architecture",
        "explain_symbol",
        "find_impact_path",
        "get_community_members",
    ],
    "reviewer": ["read_knowledge", "query_architecture", "explain_symbol"],
    "coder": ["read_knowledge", "search_knowledge", "explain_symbol"],
    "tdd_guide": ["read_knowledge"],
    "refactor_cleaner": ["query_architecture", "list_god_nodes", "explain_symbol"],
    "doc_updater": ["read_knowledge", "search_knowledge", "query_architecture"],
    "database_reviewer": ["read_knowledge", "query_architecture", "explain_symbol"],
    "security_reviewer": ["read_knowledge", "query_architecture", "find_impact_path"],
    "harness_optimizer": ["list_god_nodes"],
    "loop_operator": [],
    "build_error_resolver": ["read_knowledge", "explain_symbol"],
    "e2e_runner": [],
}


def partition_tools_by_agent(tools: list[BaseTool]) -> dict[str, list[BaseTool]]:
    """Partition a list of LangChain BaseTool objects according to the Tool Binding Matrix."""
    tools_by_name = {tool.name: tool for tool in tools}
    partitioned: dict[str, list[BaseTool]] = {}
    for agent_name, allowed_tool_names in TOOL_BINDING_MATRIX.items():
        partitioned[agent_name] = [
            tools_by_name[name] for name in allowed_tool_names if name in tools_by_name
        ]
    return partitioned


async def create_mcp_tools(
    server_params: dict[str, Any] | None = None,
) -> dict[str, list[BaseTool]]:
    """Start MCP servers and convert their tools to partitioned LangChain Tool objects."""
    from langchain_mcp_adapters.client import MultiServerMCPClient  # type: ignore[import-not-found]

    base_dir = Path(__file__).resolve().parent.parent
    knowledge_script = str(base_dir / "skills" / "servers" / "knowledge_mcp.py")
    graph_memory_script = str(base_dir / "skills" / "servers" / "graph_memory_mcp.py")

    default_params: dict[str, Any] = {
        "knowledge": {
            "command": "python",
            "args": [knowledge_script],
            "transport": "stdio",
        },
        "graph_memory": {
            "command": "python",
            "args": [graph_memory_script],
            "transport": "stdio",
        },
    }
    params = server_params or default_params

    client = MultiServerMCPClient(params)
    all_tools = await client.get_tools()
    return partition_tools_by_agent(all_tools)


# ---------------------------------------------------------------------------
# High-level compilation helper
# ---------------------------------------------------------------------------


def compile_graph(
    policy_loader: PolicyLoader | None = None,
    llm: Any | None = None,
    mcp_tools: dict[str, list[Any]] | None = None,
) -> Any:
    """High-level graph compilation that wires up all agent executors.

    Parameters
    ----------
    policy_loader:
        Governance policy loader.  Defaults to loading ``harness/policies.json``.
    llm:
        The :class:`BaseChatModel` to use.  If ``None``, loaded via
        :func:`~harness.llm_provider.get_llm`.
    mcp_tools:
        Mapping of agent role names to their assigned MCP tools.
        If ``None``, agents are created without tools.
    """
    if policy_loader is None:
        policy_loader = PolicyLoader()

    if llm is None:
        from harness.llm_provider import get_llm

        llm = get_llm()

    mcp_tools = mcp_tools or {}

    # Create agent executors
    executors: dict[str, AgentExecutor] = {}

    # Orchestrator — uses structured output (RoutingDecision)
    orchestrator_prompt = _AGENTS_DIR / "orchestrator.md"
    executors["orchestrator"] = AgentExecutor(
        agent_role=AgentRole.ORCHESTRATOR,
        prompt_path=orchestrator_prompt,
        llm=llm,
        policies=policy_loader,
        output_schema=RoutingDecision,
    )

    # All other agents
    for role in _AGENT_ROLES:
        name = role.value
        # Convert role enum value to filename (e.g. tdd_guide → tdd-guide.md)
        filename = name.replace("_", "-") + ".md"
        prompt_path = _AGENTS_DIR / filename
        tools = mcp_tools.get(name, [])
        executors[name] = AgentExecutor(
            agent_role=role,
            prompt_path=prompt_path,
            llm=llm,
            policies=policy_loader,
            mcp_tools=tools,
        )

    return build_graph(executors, policy_loader)


# ---------------------------------------------------------------------------
# Top-level execution entry point
# ---------------------------------------------------------------------------


async def run_graph(
    task_description: str,
    config: dict[str, Any] | None = None,
) -> ExecutionState:
    """Execute the full orchestration loop for a given task.

    Parameters
    ----------
    task_description:
        The user's intent / task to execute.
    config:
        Optional LangGraph config (e.g. thread_id for checkpointing).

    Returns
    -------
    ExecutionState
        The final execution state after the graph completes or interrupts.
    """
    from dotenv import load_dotenv

    load_dotenv()

    policy_loader = PolicyLoader()
    compiled = compile_graph(policy_loader=policy_loader)

    initial_state = ExecutionState(
        task_description=task_description,
        token_budget=policy_loader.get_token_budget_default(),
        max_retries=policy_loader.get_max_retries_per_agent(),
    )

    run_config = config or {"configurable": {"thread_id": "default"}}

    result = await compiled.ainvoke(initial_state.model_dump(), config=run_config)

    # Reconstruct and persist final state
    final_state = ExecutionState.model_validate(result)
    final_state.serialize_to_file(Path("state.json"))

    return final_state
