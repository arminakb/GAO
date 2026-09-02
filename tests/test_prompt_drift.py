"""Drift tests: agent prompts must stay synchronized with harness code.

These tests encode the contract between the human-readable agent prompt files
(``agents/*.md``) and the executable harness configuration. If a test here
fails, either the prompt or the policy has drifted and one of them is lying.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from harness.state_schema import AgentRole
from harness.validator import TOOL_BINDING_MATRIX

_AGENTS_DIR = Path(__file__).parent.parent / "agents"

_KNOWN_TOOLS = {
    "read_knowledge",
    "search_knowledge",
    "query_architecture",
    "explain_symbol",
    "find_impact_path",
    "get_community_members",
    "list_god_nodes",
    "record_agent_memory",
    "refresh_graph",
}


def _prompt_file(role: str) -> Path:
    """Resolve a role name to its prompt file (tdd_guide → tdd-guide.md)."""
    return _AGENTS_DIR / (role.replace("_", "-") + ".md")


def _prompt_tool_section(role: str) -> str:
    """Return the '## Available Tools' section of an agent prompt."""
    prompt_file = _prompt_file(role)
    assert prompt_file.exists(), f"Missing prompt file for role '{role}'"
    text = prompt_file.read_text(encoding="utf-8")
    assert "## Available Tools" in text, f"'{role}.md' lacks an Available Tools section"
    return text.split("## Available Tools", 1)[1].split("\n## ", 1)[0]


def test_every_agent_role_has_prompt_file() -> None:
    """Each AgentRole enum value has a corresponding agents/<name>.md file."""
    for role in AgentRole:
        prompt_file = _prompt_file(role.value)
        assert prompt_file.exists(), f"AgentRole '{role.value}' has no prompt file"
        assert "## Identity & Mission" in prompt_file.read_text(encoding="utf-8")


def test_prompt_tools_match_binding_matrix() -> None:
    """Tools listed in each prompt's Available Tools section == binding matrix."""
    for role_name, matrix_tools in TOOL_BINDING_MATRIX.items():
        section = _prompt_tool_section(role_name)
        listed = {m for m in re.findall(r"`([a-z_]+)`", section) if m in _KNOWN_TOOLS}

        if not matrix_tools:
            assert "None" in section, (
                f"'{role_name}' binds no tools but its prompt implies otherwise: {sorted(listed)}"
            )
        else:
            assert listed == set(matrix_tools), (
                f"'{role_name}' prompt/matrix drift: prompt={sorted(listed)} "
                f"matrix={sorted(matrix_tools)}"
            )


def test_prompt_files_cover_exactly_the_matrix() -> None:
    """No prompt file exists that has no entry in the binding matrix."""
    matrix_files = {_prompt_file(name).name for name in TOOL_BINDING_MATRIX}
    prompt_files = {p.name for p in _AGENTS_DIR.glob("*.md")}
    assert prompt_files == matrix_files, (
        f"Prompt/matrix file set drift: only-in-prompts={sorted(prompt_files - matrix_files)} "
        f"only-in-matrix={sorted(matrix_files - prompt_files)}"
    )


@pytest.mark.parametrize(
    "role,limit",
    [
        ("orchestrator", 2500),
        ("coder", 15000),
        ("architect", 8000),
    ],
)
def test_system_prompt_within_token_budget(role: str, limit: int) -> None:
    """A role's system prompt fits inside its per-agent token limit."""
    prompt_file = _AGENTS_DIR / f"{role}.md"
    text = prompt_file.read_text(encoding="utf-8")
    # Conservative estimate: ~3.5 chars per token for English markdown.
    estimated_tokens = len(text) / 3.5
    # The prompt must leave room for the state summary + output. Cap the
    # system prompt at 40% of the per-agent budget.
    assert estimated_tokens < limit * 0.40, (
        f"'{role}' system prompt ~{estimated_tokens:.0f} tokens exceeds 40% of its "
        f"{limit}-token budget; shrink the prompt or raise the policy limit"
    )
