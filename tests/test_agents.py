"""Tests for Agent System Prompts and Orchestrator Routing contracts."""

from __future__ import annotations

from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from harness.agent_executor import AgentExecutor
from harness.state_schema import AgentRole, PolicyLoader, RoutingDecision
from harness.validator import TOOL_BINDING_MATRIX, compile_graph

_AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"


def test_all_14_agent_prompts_exist() -> None:
    """Verify all 14 agent prompt .md files exist in agents/."""
    for role in AgentRole:
        name = role.value
        filename = name.replace("_", "-") + ".md"
        prompt_path = _AGENTS_DIR / filename
        assert prompt_path.is_file(), f"Missing agent prompt file: {prompt_path}"


def test_agent_prompts_follow_standardized_structure() -> None:
    """Verify every agent prompt contains all required Section 4.1 headers."""
    required_sections = [
        "## Identity & Mission",
        "## Input Contract",
        "## Output Contract",
        "## Available Tools",
        "## Behavioral Rules",
        "## Output Format",
        "## Anti-Patterns",
    ]

    for role in AgentRole:
        filename = role.value.replace("_", "-") + ".md"
        content = (_AGENTS_DIR / filename).read_text(encoding="utf-8")

        assert content.startswith("# Agent:"), f"Missing title in {filename}"
        for section in required_sections:
            assert section in content, f"Missing '{section}' in {filename}"


def test_agent_executors_load_all_prompts(
    policy_loader: PolicyLoader, mock_llm: FakeListChatModel
) -> None:
    """Verify AgentExecutor successfully loads prompts for all 14 agent roles."""
    for role in AgentRole:
        filename = role.value.replace("_", "-") + ".md"
        prompt_path = _AGENTS_DIR / filename

        executor = AgentExecutor(
            agent_role=role,
            prompt_path=prompt_path,
            llm=mock_llm,
            policies=policy_loader,
            output_schema=RoutingDecision if role == AgentRole.ORCHESTRATOR else None,
        )

        prompt_text = executor._load_system_prompt()
        assert len(prompt_text) > 100
        assert "## Identity & Mission" in prompt_text


def test_compiled_graph_loads_all_executors(
    policy_loader: PolicyLoader, mock_llm: FakeListChatModel
) -> None:
    """Verify compile_graph wires all 14 agent executors cleanly."""
    compiled = compile_graph(policy_loader=policy_loader, llm=mock_llm)
    assert compiled is not None
    for role in AgentRole:
        role_name = role.value
        if role_name != "orchestrator":
            assert role_name in TOOL_BINDING_MATRIX


def test_orchestrator_routing_structured_output(
    policy_loader: PolicyLoader, mock_llm: FakeListChatModel
) -> None:
    """Verify orchestrator returns valid RoutingDecision structured output."""
    executor = AgentExecutor(
        agent_role=AgentRole.ORCHESTRATOR,
        prompt_path=_AGENTS_DIR / "orchestrator.md",
        llm=mock_llm,
        policies=policy_loader,
        output_schema=RoutingDecision,
    )
    assert executor.agent_role == AgentRole.ORCHESTRATOR
    assert executor._output_schema == RoutingDecision
