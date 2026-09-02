"""Base agent execution engine for the Graph Agent Orchestrator.

Each LangGraph node delegates to an :class:`AgentExecutor` instance which:

1. Loads the agent's system prompt from its ``agents/*.md`` file.
2. Binds only the MCP tools assigned to this agent (per the Tool Binding Matrix).
3. Builds a compressed context window from the current :class:`ExecutionState`.
4. Invokes the LLM (with optional structured output).
5. Records telemetry, updates performance metrics, and returns a partial state dict.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from harness.state_schema import (
    AgentMessage,
    AgentRole,
    AuditEntry,
    ExecutionState,
    PolicyLoader,
    RoutingDecision,
)
from harness.telemetry import (
    BudgetGuard,
    CostCalculator,
    PerformanceTracker,
    TokenTracker,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Context builder helpers
# ---------------------------------------------------------------------------

_CONTEXT_HISTORY_WINDOW = 5
_CONTEXT_ERROR_WINDOW = 3


def _build_state_summary(state: ExecutionState) -> str:
    """Build a compact textual summary of the current execution state.

    This is injected as a HumanMessage so the agent understands the current
    context without receiving the full (potentially enormous) state object.
    """
    recent_msgs = state.message_history[-_CONTEXT_HISTORY_WINDOW:]
    history_block = "\n".join(f"  [{m.role.value}]: {m.content[:200]}" for m in recent_msgs)
    errors_block = "\n".join(f"  - {e}" for e in state.error_log[-_CONTEXT_ERROR_WINDOW:])

    budget_pct = (
        (state.telemetry.total_tokens / state.token_budget * 100) if state.token_budget else 0
    )

    return (
        f"## Current Execution State\n"
        f"**Task:** {state.task_description}\n"
        f"**Phase:** {state.workflow_phase.value}\n"
        f"**Active Agent:** {state.active_agent.value}\n"
        f"**Retry Count:** {state.retry_count}/{state.max_retries}\n"
        f"**Token Budget:** {state.telemetry.total_tokens:,}/{state.token_budget:,} "
        f"({budget_pct:.1f}%)\n\n"
        f"### Recent Messages\n{history_block or '  (none)'}\n\n"
        f"### Recent Errors\n{errors_block or '  (none)'}\n\n"
        f"### Last Agent Output\n{state.last_agent_output[:500] or '(none)'}\n"
    )


# ---------------------------------------------------------------------------
# AgentExecutor
# ---------------------------------------------------------------------------


class AgentExecutor:
    """Loads an agent prompt, binds tools, invokes the LLM, returns state update.

    Parameters
    ----------
    agent_role:
        The :class:`AgentRole` this executor represents.
    prompt_path:
        Absolute or relative path to the ``agents/<name>.md`` file.
    llm:
        The :class:`BaseChatModel` instance to call.
    mcp_tools:
        MCP tools available to this agent (subset of all tools).
    policies:
        Shared :class:`PolicyLoader` for governance queries.
    output_schema:
        If set, the LLM is called with ``.with_structured_output(schema)``
        to guarantee Pydantic-validated output.
    """

    def __init__(
        self,
        agent_role: AgentRole,
        prompt_path: Path,
        llm: BaseChatModel,
        policies: PolicyLoader,
        mcp_tools: list[BaseTool] | None = None,
        output_schema: type[BaseModel] | None = None,
    ) -> None:
        self.agent_role = agent_role
        self.prompt_path = prompt_path
        self.policies = policies
        self._mcp_tools = mcp_tools or []
        self._output_schema = output_schema

        # Optionally bind structured output
        if output_schema is not None:
            try:
                self._llm: BaseChatModel = llm.with_structured_output(output_schema)  # type: ignore[assignment]
            except (NotImplementedError, AttributeError):
                self._llm = llm
        elif self._mcp_tools:
            try:
                self._llm = llm.bind_tools(self._mcp_tools)  # type: ignore[assignment]
            except (NotImplementedError, AttributeError):
                self._llm = llm
        else:
            self._llm = llm

        self._system_prompt: str | None = None

    # ------------------------------------------------------------------
    # Prompt loading
    # ------------------------------------------------------------------

    def _load_system_prompt(self) -> str:
        """Read the agent's ``.md`` file and cache it."""
        if self._system_prompt is None:
            if self.prompt_path.exists():
                self._system_prompt = self.prompt_path.read_text(encoding="utf-8")
            else:
                self._system_prompt = (
                    f"You are the {self.agent_role.value} agent in the multi-agent system."
                )
        return self._system_prompt

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(self, state: ExecutionState) -> dict[str, Any]:
        """Run the full agent invocation pipeline.

        Returns a partial ``dict`` that LangGraph merges into the state.
        """
        role_name = self.agent_role.value
        budget_guard = BudgetGuard()
        cost_calculator = CostCalculator()
        performance_tracker = PerformanceTracker()

        # 1. Budget check
        allowed, reason = budget_guard.check_budget(state)
        if not allowed:
            logger.warning("[%s] %s — skipping execution.", role_name, reason)
            return {"budget_exhausted": True}

        # 2. Build messages
        system_prompt = self._load_system_prompt()
        state_summary = _build_state_summary(state)
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=state_summary),
        ]

        # 3. Invoke LLM with TokenTracker and timing
        model_name = getattr(self._llm, "model_name", getattr(self._llm, "model", "default"))
        tracker = TokenTracker(model_name=str(model_name))
        start = time.perf_counter()
        try:
            try:
                response = await self._llm.ainvoke(messages, config={"callbacks": [tracker]})
            except (TypeError, ValueError):
                response = await self._llm.ainvoke(messages)
            elapsed_ms = (time.perf_counter() - start) * 1000
            success = True
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.exception("[%s] LLM invocation failed.", role_name)
            success = False
            return self._build_error_update(state, role_name, str(exc), elapsed_ms)

        # 4. Extract content and token usage
        if self._output_schema is not None:
            # Structured output — response IS the Pydantic model
            if isinstance(response, BaseModel):
                content = response.model_dump_json()
            else:
                content = str(response)
        else:
            raw = response.content if hasattr(response, "content") else str(response)
            content = str(raw)

        prompt_tokens = tracker.prompt_tokens
        completion_tokens = tracker.completion_tokens
        tokens_used = tracker.total_tokens

        # Try to get usage metadata from response if tracker is 0
        if tokens_used == 0 and hasattr(response, "usage_metadata") and response.usage_metadata:
            usage = response.usage_metadata
            prompt_tokens = int(getattr(usage, "input_tokens", 0) or 0)
            completion_tokens = int(getattr(usage, "output_tokens", 0) or 0)
            tokens_used = int(
                getattr(usage, "total_tokens", 0) or (prompt_tokens + completion_tokens)
            )

        # 5. Calculate cost
        cost_usd = cost_calculator.calculate(str(model_name), prompt_tokens, completion_tokens)

        # 6. Build state update
        update: dict[str, Any] = {
            "active_agent": self.agent_role,
            "last_agent_output": str(content)[:2000],
        }

        # 7. Update telemetry
        telemetry = state.telemetry.model_copy(deep=True)
        telemetry.record_invocation(role_name, tokens_used, cost_usd, elapsed_ms)
        update["telemetry"] = telemetry

        # 8. Check budget and agent limits
        _agent_ok, agent_warn = budget_guard.check_agent_limit(
            role_name, tokens_used, self.policies
        )
        if not _agent_ok:
            logger.warning("[%s] %s", role_name, agent_warn)

        temp_state = state.model_copy(update={"telemetry": telemetry})
        budget_status = budget_guard.get_budget_status(
            temp_state, self.policies.get_warning_threshold_percent()
        )
        if budget_status["exhausted"]:
            update["budget_exhausted"] = True
            logger.warning("[%s] Token budget exhausted after invocation.", role_name)

        # 9. Append message to history
        new_message = AgentMessage(
            role=self.agent_role,
            content=str(content)[:500],
            token_count=tokens_used,
            cost_usd=cost_usd,
        )
        updated_history = list(state.message_history) + [new_message]
        if len(updated_history) > state.max_history_size:
            updated_history = updated_history[-state.max_history_size :]
        update["message_history"] = updated_history

        # 10. Update performance
        perf = performance_tracker.record_success(state, role_name, tokens_used)
        update["agent_performance"] = perf

        # 11. Reset retry on success
        if success:
            update["retry_count"] = 0

        # 12. If structured output, parse routing decision
        if self._output_schema is not None and isinstance(response, BaseModel):
            update["routing_decision"] = response

        # 13. Append audit trail entry
        if self.agent_role == AgentRole.ORCHESTRATOR and isinstance(response, RoutingDecision):
            entry = AuditEntry(
                from_node="orchestrator",
                to_node=response.target_agent.value
                if isinstance(response.target_agent, AgentRole)
                else str(response.target_agent),
                rationale=response.reasoning,
                state_snapshot_hash=state.compute_snapshot_hash(),
            )
        else:
            entry = AuditEntry(
                from_node=state.active_agent.value
                if isinstance(state.active_agent, AgentRole)
                else str(state.active_agent),
                to_node=role_name,
                rationale=f"Executed {role_name} node",
                state_snapshot_hash=state.compute_snapshot_hash(),
            )
        update["audit_trail"] = list(state.audit_trail) + [entry]

        logger.info(
            "[%s] Completed in %.0fms | tokens=%d | cost=$%.4f | success=%s",
            role_name,
            elapsed_ms,
            tokens_used,
            cost_usd,
            success,
        )
        return update

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    @staticmethod
    def _build_error_update(
        state: ExecutionState,
        role_name: str,
        error_msg: str,
        elapsed_ms: float,
    ) -> dict[str, Any]:
        """Build a partial state update for a failed invocation."""
        error_log = list(state.error_log)
        error_log.append(f"[{role_name}] {error_msg}")

        perf = PerformanceTracker().record_failure(state, role_name, 0)

        telemetry = state.telemetry.model_copy(deep=True)
        telemetry.record_invocation(role_name, 0, 0.0, elapsed_ms)

        return {
            "retry_count": state.retry_count + 1,
            "error_log": error_log,
            "telemetry": telemetry,
            "agent_performance": perf,
        }
