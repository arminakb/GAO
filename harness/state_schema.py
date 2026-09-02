"""
Pydantic data models for the Graph Agent Orchestrator execution state.

This module defines the single source of truth (SSOT) for all execution context.
The root `ExecutionState` model is used as LangGraph's state annotation and is
serialized to `state.json` at checkpoint boundaries.

All models use strict Pydantic v2 validation with type annotations and defaults.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AgentRole(StrEnum):
    """All available agent nodes in the orchestrator graph."""

    ORCHESTRATOR = "orchestrator"
    PLANNER = "planner"
    ARCHITECT = "architect"
    REVIEWER = "reviewer"
    CODER = "coder"
    TDD_GUIDE = "tdd_guide"
    BUILD_ERROR_RESOLVER = "build_error_resolver"
    E2E_RUNNER = "e2e_runner"
    REFACTOR_CLEANER = "refactor_cleaner"
    DOC_UPDATER = "doc_updater"
    LOOP_OPERATOR = "loop_operator"
    HARNESS_OPTIMIZER = "harness_optimizer"
    DATABASE_REVIEWER = "database_reviewer"
    SECURITY_REVIEWER = "security_reviewer"


class WorkflowPhase(StrEnum):
    """Discrete phases of the execution lifecycle."""

    INITIALIZATION = "initialization"
    PLANNING = "planning"
    ARCHITECTURE = "architecture"
    IMPLEMENTATION = "implementation"
    REVIEW = "review"
    TESTING = "testing"
    DEPLOYMENT = "deployment"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


class AgentMessage(BaseModel):
    """A single message in the execution history."""

    role: AgentRole
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    token_count: int = 0
    cost_usd: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class RoutingDecision(BaseModel):
    """Structured output produced by the Orchestrator LLM.

    The orchestrator analyzes execution state and emits this schema to
    determine which agent should execute next.
    """

    target_agent: AgentRole
    reasoning: str
    priority: Literal["low", "medium", "high", "critical"] = "medium"
    parallel_targets: list[AgentRole] = Field(
        default_factory=list,
        description="Optional list of agents to invoke in parallel (fan-out).",
    )


class ExecutionTelemetry(BaseModel):
    """Per-session token and cost tracking metrics."""

    total_tokens: int = 0
    total_cost_usd: float = 0.0
    node_invocations: dict[str, int] = Field(default_factory=dict)
    node_token_usage: dict[str, int] = Field(default_factory=dict)
    node_latency_ms: dict[str, list[float]] = Field(default_factory=dict)

    def record_invocation(
        self,
        node_name: str,
        tokens_used: int,
        cost_usd: float,
        latency_ms: float,
    ) -> None:
        """Record metrics for a single agent node invocation."""
        self.total_tokens += tokens_used
        self.total_cost_usd += cost_usd
        self.node_invocations[node_name] = self.node_invocations.get(node_name, 0) + 1
        self.node_token_usage[node_name] = self.node_token_usage.get(node_name, 0) + tokens_used
        self.node_latency_ms.setdefault(node_name, []).append(latency_ms)


class AuditEntry(BaseModel):
    """Immutable log entry for every state transition.

    Every routing decision, agent completion, and interrupt generates
    an audit entry with a SHA-256 hash of the state at transition time.
    """

    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    from_node: str | None = None
    to_node: str
    rationale: str
    state_snapshot_hash: str


class AgentPerformance(BaseModel):
    """Historical performance metrics for an agent node.

    Tracks success/failure counts, token consumption, and timing
    to enable performance-aware routing by the orchestrator.
    """

    success_count: int = 0
    failure_count: int = 0
    total_tokens_used: int = 0
    avg_tokens_per_invocation: float = 0.0
    last_invoked: datetime | None = None

    @property
    def success_rate(self) -> float:
        """Return the success ratio (0.0–1.0). Returns 0.0 if never invoked."""
        total = self.success_count + self.failure_count
        return self.success_count / total if total > 0 else 0.0

    def record(self, *, success: bool, tokens_used: int) -> None:
        """Record an invocation outcome and update running averages."""
        if success:
            self.success_count += 1
        else:
            self.failure_count += 1
        self.total_tokens_used += tokens_used
        total = self.success_count + self.failure_count
        self.avg_tokens_per_invocation = self.total_tokens_used / total if total > 0 else 0.0
        self.last_invoked = datetime.now(UTC)


# ---------------------------------------------------------------------------
# Root State Model (LangGraph State Annotation)
# ---------------------------------------------------------------------------


class ExecutionState(BaseModel):
    """The single source of truth for execution context.

    This model serves as LangGraph's state annotation. It is passed through
    every node in the graph and updated via partial ``dict`` returns.
    At checkpoint boundaries it is serialized to ``state.json``.
    """

    # --- Core Context ---
    task_description: str = ""
    active_agent: AgentRole = AgentRole.ORCHESTRATOR
    workflow_phase: WorkflowPhase = WorkflowPhase.INITIALIZATION
    last_agent_output: str = ""

    # --- Message History (sliding window) ---
    message_history: list[AgentMessage] = Field(default_factory=list)
    max_history_size: int = 50

    # --- Error Handling & Circuit Breaker ---
    retry_count: int = 0
    max_retries: int = 3
    error_log: list[str] = Field(default_factory=list)
    is_interrupted: bool = False
    interrupt_reason: str | None = None

    # --- Telemetry ---
    telemetry: ExecutionTelemetry = Field(default_factory=ExecutionTelemetry)

    # --- Token Budget ---
    token_budget: int = 100_000
    budget_exhausted: bool = False

    # --- Semantic Audit Trail ---
    audit_trail: list[AuditEntry] = Field(default_factory=list)

    # --- Agent Performance Scores ---
    agent_performance: dict[str, AgentPerformance] = Field(default_factory=dict)

    # --- Routing ---
    routing_decision: RoutingDecision | None = None
    direct_handoff: str | None = None  # Bypass orchestrator for known transitions

    # ------------------------------------------------------------------
    # Helper Methods
    # ------------------------------------------------------------------

    def trim_history(self) -> None:
        """Enforce the sliding-window limit on ``message_history``.

        If the history exceeds ``max_history_size``, the oldest messages
        are discarded. This prevents unbounded context growth.
        """
        if len(self.message_history) > self.max_history_size:
            self.message_history = self.message_history[-self.max_history_size :]

    def compute_snapshot_hash(self) -> str:
        """Return a SHA-256 hex digest of the current serialized state.

        Used for audit trail entries to create tamper-evident state snapshots.
        """
        payload = self.model_dump_json(exclude={"audit_trail"})
        return hashlib.sha256(payload.encode()).hexdigest()

    def serialize_to_file(self, path: Path) -> None:
        """Atomically write the current state to *path* as formatted JSON.

        Writes to a temporary file in the same directory first, then renames
        to avoid corruption from interrupted writes.
        """
        data = self.model_dump_json(indent=2)
        dir_path = path.parent
        dir_path.mkdir(parents=True, exist_ok=True)

        fd, tmp_path = tempfile.mkstemp(dir=str(dir_path), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp_path, str(path))
        except BaseException:
            # Clean up the temp file on failure
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    @classmethod
    def load_from_file(cls, path: Path) -> ExecutionState:
        """Deserialize an ``ExecutionState`` from a JSON file.

        Raises:
            FileNotFoundError: If *path* does not exist.
            pydantic.ValidationError: If the JSON doesn't match the schema.
        """
        raw = path.read_text(encoding="utf-8")
        return cls.model_validate_json(raw)


# ---------------------------------------------------------------------------
# Policy Loader
# ---------------------------------------------------------------------------

_DEFAULT_POLICIES_PATH = Path(__file__).parent / "policies.json"


class PolicyLoader:
    """Loads and queries the governance policies defined in ``policies.json``.

    All routing guards, token limits, and access rules are resolved through
    this class. It is instantiated once at startup and shared across the
    graph engine.
    """

    def __init__(self, policies_path: Path | None = None) -> None:
        self._path = policies_path or _DEFAULT_POLICIES_PATH
        with open(self._path, encoding="utf-8") as f:
            self._data: dict[str, Any] = json.load(f)

    def _routing(self) -> dict[str, Any]:
        return cast("dict[str, Any]", self._data.get("routing_rules", {}))

    def _tokens(self) -> dict[str, Any]:
        return cast("dict[str, Any]", self._data.get("token_policies", {}))

    def _boundaries(self) -> dict[str, Any]:
        return cast("dict[str, Any]", self._data.get("execution_boundaries", {}))

    def _safety(self) -> dict[str, Any]:
        return cast("dict[str, Any]", self._data.get("safety_rules", {}))

    # -- Routing Rules -------------------------------------------------------

    def is_transition_allowed(self, from_agent: str, to_agent: str) -> tuple[bool, str]:
        """Check whether a transition from *from_agent* to *to_agent* is legal.

        Returns ``(True, "")`` if allowed, or ``(False, reason)`` if forbidden.
        """
        forbidden: list[dict[str, str]] = self._routing().get("forbidden_transitions", [])
        for rule in forbidden:
            if rule["from"] == from_agent and rule["to"] == to_agent:
                return False, rule["reason"]
        return True, ""

    def get_required_transitions(self) -> list[dict[str, str]]:
        """Return the list of required-transition rules."""
        result: list[dict[str, str]] = self._routing().get("required_transitions", [])
        return result

    def get_direct_handoff(self, from_agent: str) -> str | None:
        """Return the direct-handoff target for *from_agent*, if any.

        Direct handoffs allow certain agents to bypass the orchestrator and
        route directly to the next agent in a known workflow chain.
        """
        handoffs: list[dict[str, str]] = self._routing().get("direct_handoffs", [])
        for handoff in handoffs:
            if handoff["from"] == from_agent:
                return handoff["to"]
        return None

    # -- Token Policies ------------------------------------------------------

    def get_token_limit(self, agent_role: str) -> int:
        """Return the per-invocation token limit for *agent_role*.

        Falls back to the orchestrator's limit (2000) if the role is not
        explicitly configured.
        """
        limits: dict[str, int] = self._tokens().get("per_agent_limits", {})
        return int(limits.get(agent_role, 2000))

    def get_warning_threshold_percent(self) -> int:
        """Return the budget warning threshold as a percentage (0–100)."""
        return int(self._tokens().get("warning_threshold_percent", 80))

    def get_hard_limit_action(self) -> str:
        """Return the action to take when the hard token limit is hit."""
        return str(self._tokens().get("hard_limit_action", "interrupt"))

    # -- Access Rules --------------------------------------------------------

    def get_access_rules(self, agent_role: str) -> dict[str, bool]:
        """Return file-system access permissions for *agent_role*.

        Returns a dict with ``read``, ``write``, and ``delete`` boolean keys.
        Falls back to the ``default`` rules if the role is not configured.
        """
        fs_rules: dict[str, Any] = cast("dict[str, Any]", self._data.get("access_rules", {})).get(
            "file_system", {}
        )
        default: dict[str, bool] = {"read": True, "write": False, "delete": False}
        result: dict[str, bool] = fs_rules.get(agent_role, fs_rules.get("default", default))
        return result

    # -- Execution Boundaries ------------------------------------------------

    def get_max_retries_per_agent(self) -> int:
        """Return the maximum consecutive retries before circuit break."""
        return int(self._boundaries().get("max_retries_per_agent", 3))

    def get_max_parallel_branches(self) -> int:
        """Return the maximum number of concurrent parallel agent branches."""
        return int(self._boundaries().get("max_parallel_branches", 3))

    def get_token_budget_default(self) -> int:
        """Return the default global token budget."""
        return int(self._boundaries().get("token_budget_default", 100_000))

    # -- Safety Rules --------------------------------------------------------

    def get_safety_rules(self) -> dict[str, Any]:
        """Return the full safety rules configuration."""
        return self._safety()

    def get_banned_operations(self) -> list[str]:
        """Return the list of banned operation patterns."""
        result: list[str] = self._safety().get("banned_operations", [])
        return result

    def requires_human_approval(self, action: str) -> bool:
        """Check if *action* requires explicit human approval."""
        approval_list: list[str] = self._safety().get("require_human_approval_for", [])
        return action in approval_list

    # -- Raw Access ----------------------------------------------------------

    @property
    def raw(self) -> dict[str, Any]:
        """Return the raw policies dictionary."""
        return self._data
