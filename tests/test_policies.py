"""Tests for PolicyLoader and governance rules enforcement."""

from __future__ import annotations

from harness.state_schema import PolicyLoader


def test_policies_load_valid_json(policy_loader: PolicyLoader) -> None:
    """Test that policies.json loads and validates without error."""
    assert policy_loader.raw is not None
    assert "$schema_version" in policy_loader.raw


def test_forbidden_self_loop(policy_loader: PolicyLoader) -> None:
    """Test that is_transition_allowed('coder', 'coder') returns False with reason."""
    allowed, reason = policy_loader.is_transition_allowed("coder", "coder")
    assert not allowed
    assert "Self-loop prevention" in reason

    allowed_refactor, reason_refactor = policy_loader.is_transition_allowed(
        "refactor_cleaner", "refactor_cleaner"
    )
    assert not allowed_refactor
    assert "Self-loop prevention" in reason_refactor


def test_allowed_transition(policy_loader: PolicyLoader) -> None:
    """Test that is_transition_allowed('orchestrator', 'planner') returns True."""
    allowed, reason = policy_loader.is_transition_allowed("orchestrator", "planner")
    assert allowed
    assert reason == ""


def test_token_limit_per_agent(policy_loader: PolicyLoader) -> None:
    """Test that get_token_limit returns the correct configured limit for each agent."""
    assert policy_loader.get_token_limit("coder") == 15000
    assert policy_loader.get_token_limit("orchestrator") == 2500
    assert policy_loader.get_token_limit("architect") == 10000
    assert policy_loader.get_token_limit("nonexistent_agent") == 2000  # default fallback


def test_access_rules_readonly_reviewer(policy_loader: PolicyLoader) -> None:
    """Test that get_access_rules('reviewer') returns read-only permissions."""
    rules = policy_loader.get_access_rules("reviewer")
    assert rules == {"read": True, "write": False, "delete": False}

    coder_rules = policy_loader.get_access_rules("coder")
    assert coder_rules == {"read": True, "write": True, "delete": False}

    refactor_rules = policy_loader.get_access_rules("refactor_cleaner")
    assert refactor_rules == {"read": True, "write": True, "delete": True}


def test_direct_handoff_lookup(policy_loader: PolicyLoader) -> None:
    """Test that direct handoff lookups return correct target or None."""
    assert policy_loader.get_direct_handoff("coder") == "reviewer"
    assert policy_loader.get_direct_handoff("tdd_guide") == "coder"
    assert policy_loader.get_direct_handoff("build_error_resolver") == "coder"
    assert policy_loader.get_direct_handoff("orchestrator") is None


def test_safety_rules_banned_operations(policy_loader: PolicyLoader) -> None:
    """Test that all entries in banned_operations are present."""
    banned = policy_loader.get_banned_operations()
    assert "rm -rf /" in banned
    assert "DROP DATABASE" in banned
    assert "sudo rm" in banned


def test_human_approval_required(policy_loader: PolicyLoader) -> None:
    """Test that require_human_approval_for actions are correctly identified."""
    assert policy_loader.requires_human_approval("database_migration")
    assert policy_loader.requires_human_approval("production_deployment")
    assert not policy_loader.requires_human_approval("format_code")
