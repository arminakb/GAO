"""Tests for the GOA MCP server (skills/servers/goa_mcp.py).

Tools are exercised as plain functions (FastMCP decorators return the same
callable); a stdio smoke test verifies the server actually boots.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

import skills.servers.goa_mcp as goa

BIN = sys.executable


@pytest.fixture(autouse=True)
def clean_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Isolate workspace, memory file, and sessions per test."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (repo / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    monkeypatch.setattr(goa, "_WORKSPACE", repo.resolve())
    monkeypatch.setattr(goa, "_MEMORY_PATH", tmp_path / ".goa_memory.json")
    fresh_toolkit = goa.ExecutionToolkit(repo)
    fresh_verifier = goa.VerificationEngine(fresh_toolkit)
    monkeypatch.setattr(goa, "_TOOLKIT", fresh_toolkit)
    monkeypatch.setattr(goa, "_VERIFIER", fresh_verifier)
    monkeypatch.setattr(goa, "_SESSIONS", {})
    yield
    goa._SESSIONS.clear()


# --- task intelligence ---------------------------------------------------------


def test_analyze_task_trivial() -> None:
    out = goa.goa_analyze_task("Fix typo in README", "s1")
    assert out["complexity"] == "trivial"
    assert out["confidence"] > 0.5


def test_plan_solo_skips_orchestrator() -> None:
    goa.goa_analyze_task("Fix typo in README", "s2")
    plan = goa.goa_plan("Fix typo in README", "s2")
    assert plan["track"] == "solo"
    assert plan["needs_orchestrator_llm"] is False


def test_route_complex_fans_out() -> None:
    task = (
        "Redesign the architecture, rewrite the database schema with migration, "
        "harden security auth, fix the concurrency race, rework frontend components"
    )
    out = goa.goa_route(task, "s3")
    assert out["track"] == "complex"
    assert out["specialists"]


# --- verification + recovery ------------------------------------------------------


def test_verify_green_and_review_recommendation() -> None:
    verdict = goa.goa_verify("s4")
    assert verdict["passed"] is True
    assert verdict["verdict"] == "GREEN"
    review = goa.goa_review(session_id="s4")
    assert "APPROVE-eligible" in review["recommendation"]


def test_verify_red_routes_to_test_resolver() -> None:
    (goa._WORKSPACE / "test_bad.py").write_text("def test_bad():\n    assert False\n")
    verdict = goa.goa_verify("s5")
    assert verdict["passed"] is False
    assert verdict["failure_category"] == "test_failure"
    repair = goa.goa_repair("s5")
    assert repair["resolver"] == "test_failure_resolver"
    assert repair["cycles_failed"] == 1


def test_repair_escalates_after_three_failures() -> None:
    (goa._WORKSPACE / "test_bad.py").write_text("def test_bad():\n    assert False\n")
    for _ in range(3):
        goa.goa_verify("s6")
    repair = goa.goa_repair("s6")
    assert repair["verdict"] == "escalate"


def test_repair_without_verify() -> None:
    assert goa.goa_repair("s7")["action"] == "call goa_verify first"


# --- memory ------------------------------------------------------------------------


def test_memory_roundtrip_selective() -> None:
    goa.goa_record_memory("recurring_bug", "parser crashes on empty input", ["parser"], "s8")
    goa.goa_record_memory("convention", "use ruff for linting", ["lint"], "s8")
    hits = goa.goa_get_memory("parser crash")
    assert len(hits) == 1
    assert hits[0]["kind"] == "recurring_bug"


def test_memory_query_no_match() -> None:
    goa.goa_record_memory("convention", "use ruff", [], "s9")
    assert goa.goa_get_memory("quantum entanglement") == []


# --- phase 5: memory injection + graphify wiring -------------------------------------


def test_analyze_injects_relevant_memory() -> None:
    goa.goa_record_memory("recurring_bug", "parser crashes on empty input", ["parser"], "m1")
    out = goa.goa_analyze_task("Fix the parser crash", "m1")
    assert out["relevant_memory"]
    assert out["relevant_memory"][0]["kind"] == "recurring_bug"
    # Session state carries it for the later review packet.
    assert goa._SESSIONS["m1"]["relevant_memory"]


def test_analyze_memory_absent_is_empty_not_error() -> None:
    out = goa.goa_analyze_task("Refactor the auth module", "m2")
    assert out["relevant_memory"] == []


def test_review_packet_includes_memory_and_impact() -> None:
    goa.goa_verify("m3")
    goa.goa_record_memory("successful_fix", "auth token refresh race fixed", ["auth"], "m3")
    goa.goa_analyze_task("fix auth token refresh", "m3")
    review = goa.goa_review(session_id="m3")
    assert "relevant_memory" in review
    assert "graphify_impact" in review
    assert review["graphify_impact"]["available"] is False  # no graph in sandbox
    assert review["relevant_memory"]  # analyze-time memory carried through


def _git_init_commit_all() -> None:
    """Make the sandbox a git repo and commit the baseline so changed-file
    detection works (toolkit git tools degrade gracefully without git)."""
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=goa._WORKSPACE, check=True)
    subprocess.run(
        ["git", "-C", str(goa._WORKSPACE), "add", "-A"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(goa._WORKSPACE),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-qm",
            "init",
        ],
        check=True,
        capture_output=True,
    )


def test_review_impact_probes_changed_symbols(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _git_init_commit_all()
    graph = {
        "nodes": [{"id": "cart"}, {"id": "billing"}, {"id": "cart.total"}],
        "edges": [
            {"source": "billing", "target": "cart"},
            {"source": "cart.total", "target": "cart"},
            {"source": "cart", "target": "cart.total"},
        ],
    }
    p = tmp_path / "graph.json"
    p.write_text(json.dumps(graph))
    monkeypatch.setenv("GRAPHIFY_GRAPH_PATH", str(p))
    (goa._WORKSPACE / "billing.py").write_text("x = 1\n")
    review = goa.goa_review(session_id="m4")
    imp = review["graphify_impact"]
    assert imp["available"] is True
    assert imp["found"] is True
    assert "cart" in imp["affected_downstream"]
    assert imp["symbols_probed"] == ["billing"]


def test_historical_failure_rate_reads_live_record() -> None:
    """Regression: the adaptive signal must see the saved verification record."""
    (goa._WORKSPACE / "test_bad.py").write_text("def test_bad():\n    assert False\n")
    goa.goa_verify("m5")
    assert goa._historical_failure_rate() == 1.0


def test_auto_graph_refresh_disabled_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GOA_AUTO_GRAPH_REFRESH", raising=False)
    _git_init_commit_all()
    (goa._WORKSPACE / "billing.py").write_text("x = 1\n")
    out = goa.goa_verify("m6")
    assert out["graph_refresh"] is None


def test_auto_graph_refresh_runs_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GOA_AUTO_GRAPH_REFRESH", "1")
    _git_init_commit_all()
    (goa._WORKSPACE / "billing.py").write_text("x = 1\n")
    out = goa.goa_verify("m7")
    gr = out["graph_refresh"]
    assert isinstance(gr, dict)
    assert "ok" in gr  # graphify CLI may be present or absent; must not crash


# --- graphify -----------------------------------------------------------------------


def test_impact_without_graph() -> None:
    out = goa.goa_get_impact("foo")
    assert out["available"] is False


def test_impact_with_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    graph = {
        "nodes": [{"id": "auth.login"}, {"id": "auth.session"}, {"id": "api.router"}],
        "edges": [
            {"source": "auth.login", "target": "auth.session"},
            {"source": "api.router", "target": "auth.login"},
            {"source": "auth.session", "target": "auth.login"},
        ],
    }
    p = tmp_path / "graph.json"
    p.write_text(json.dumps(graph))
    monkeypatch.setenv("GRAPHIFY_GRAPH_PATH", str(p))
    out = goa.goa_get_impact("auth.login")
    assert out["found"] is True
    assert "auth.session" in out["affected_downstream"]
    assert out["blast_radius"] in {"local", "medium", "high"}


def test_refresh_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """graphify update runs (or fails cleanly when the CLI is absent)."""
    monkeypatch.chdir(tmp_path)
    out = goa.goa_refresh_graph()
    assert isinstance(out.get("ok"), bool)
    assert "output" in out or "error" in out


# --- status + stdio smoke --------------------------------------------------------------


def test_status_shape() -> None:
    goa.goa_analyze_task("Fix typo", "s10")
    st = goa.goa_get_status("s10")
    assert st["task"] == "Fix typo"
    assert st["complexity"] == "trivial"
    assert st["mode"].startswith("external-agent")


def test_mcp_server_boots_over_stdio(tmp_path: Path) -> None:
    """The server must start and answer an initialize handshake over stdio."""
    proc = subprocess.Popen(
        [BIN, str(Path(goa.__file__).resolve())],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        req = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            }
        )
        out, err = proc.communicate(input=req + "\n", timeout=30)
        assert '"goa-orchestrator"' in out or '"serverInfo"' in out, err[-500:]
    finally:
        if proc.poll() is None:
            proc.kill()
