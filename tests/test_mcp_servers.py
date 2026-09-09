"""Tests for Knowledge MCP Server and Graph Memory MCP Server."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.tools import tool

import skills.servers.graph_memory_mcp as gm
import skills.servers.knowledge_mcp as km
from harness.validator import partition_tools_by_agent


def test_knowledge_server_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test list_knowledge returns list of available .md knowledge resources."""
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    (knowledge_dir / "python-best-practices.md").write_text(
        "# Python Best Practices\nUse type hints and strict linters.", encoding="utf-8"
    )
    (knowledge_dir / "architecture-guide.md").write_text(
        "# Architecture Guide\nEvent-driven microservices.", encoding="utf-8"
    )

    monkeypatch.setattr(km, "KNOWLEDGE_DIR", knowledge_dir)

    resources = km.list_knowledge()
    names = [r["name"] for r in resources]
    assert "python-best-practices" in names
    assert "architecture-guide" in names
    assert len(resources) == 2


def test_knowledge_server_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test read_knowledge returns full markdown content for a given file name."""
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    sample_content = "# Python Best Practices\nUse type hints and strict linters."
    (knowledge_dir / "python-best-practices.md").write_text(sample_content, encoding="utf-8")

    monkeypatch.setattr(km, "KNOWLEDGE_DIR", knowledge_dir)

    content = km.read_knowledge("python-best-practices")
    assert content == sample_content

    missing = km.read_knowledge("nonexistent-file")
    assert "not found" in missing


def test_knowledge_server_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test search_knowledge returns matching line excerpts."""
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    (knowledge_dir / "async-patterns.md").write_text(
        "Line 1\nUse asyncio for concurrency.\nLine 3", encoding="utf-8"
    )
    (knowledge_dir / "sync-patterns.md").write_text(
        "Line 1\nSynchronous execution is blocking.\nLine 3", encoding="utf-8"
    )

    monkeypatch.setattr(km, "KNOWLEDGE_DIR", knowledge_dir)

    results = km.search_knowledge("asyncio")
    assert len(results) == 1
    assert results[0]["name"] == "async-patterns"
    assert results[0]["line_number"] == "2"
    assert "asyncio" in results[0]["excerpt"]


def test_knowledge_server_search_multi_term(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Multi-term queries use OR semantics; files matching more terms rank first."""
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    frontmatter = (
        "---\ndescription: FastAPI, SQLModel backend patterns\n"
        "tags: fastapi, sqlmodel, backend\n---\n"
    )
    (knowledge_dir / "web-stack.md").write_text(
        frontmatter + "# Web Stack\nFastAPI and SQLModel and Backend patterns live here.\n",
        encoding="utf-8",
    )
    (knowledge_dir / "partial.md").write_text(
        "# Partial\nOnly mentions FastAPI once.\n", encoding="utf-8"
    )
    (knowledge_dir / "unrelated.md").write_text(
        "# Unrelated\nNothing relevant in this file.\n", encoding="utf-8"
    )

    monkeypatch.setattr(km, "KNOWLEDGE_DIR", knowledge_dir)

    results = km.search_knowledge("fastapi sqlmodel backend")
    names = [r["name"] for r in results]
    # The file matching all three terms outranks the partial match.
    assert names[0] == "web-stack"
    assert "partial" in names
    assert "unrelated" not in names

    # Metadata matches (name/description/tags) still outrank body matches.
    meta = [r for r in results if r["match"] == "metadata"]
    body = [r for r in results if r["match"] == "body"]
    assert meta and body

    # Single-term and phrase behavior preserved.
    single = km.search_knowledge("sqlmodel")
    assert {r["name"] for r in single} == {"web-stack"}
    assert km.search_knowledge("   ") == []


def test_knowledge_server_no_write() -> None:
    """Verify that knowledge server exposes no write, create, or delete tools."""
    # List of all tools registered on the server
    tool_names = [t.name for t in km.server._tool_manager.list_tools()]
    for write_kw in ["write", "create", "delete", "remove", "update", "edit"]:
        for name in tool_names:
            assert write_kw not in name.lower(), f"Forbidden tool found: {name}"


def test_knowledge_server_subdirectory_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Knowledge files in subdirectories are discovered with slash-joined names."""
    knowledge_dir = tmp_path / "knowledge"
    (knowledge_dir / "languages").mkdir(parents=True)
    (knowledge_dir / "languages" / "rust-review.md").write_text(
        "# Rust Code Review Guide\nOwnership and borrowing.", encoding="utf-8"
    )

    monkeypatch.setattr(km, "KNOWLEDGE_DIR", knowledge_dir)

    names = [r["name"] for r in km.list_knowledge()]
    assert names == ["languages/rust-review"]
    assert "Rust Code Review Guide" in km.read_knowledge("languages/rust-review")


def test_knowledge_server_rejects_path_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resource names cannot escape the knowledge directory."""
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    (tmp_path / "secret.md").write_text("secret", encoding="utf-8")

    monkeypatch.setattr(km, "KNOWLEDGE_DIR", knowledge_dir)

    assert "not found" in km.read_knowledge("../secret")
    assert "not found" in km.knowledge_resource("../secret")


def test_knowledge_server_frontmatter_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """YAML frontmatter description/tags feed listing; body excludes them."""
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    (knowledge_dir / "tagged.md").write_text(
        "---\ndescription: Concurrency patterns\ntags: async, parallelism\n---\n"
        "# Tagged Guide\nBody content.",
        encoding="utf-8",
    )

    monkeypatch.setattr(km, "KNOWLEDGE_DIR", knowledge_dir)

    resources = km.list_knowledge()
    assert resources[0]["description"] == "Concurrency patterns"
    assert resources[0]["tags"] == "async, parallelism"
    body = km.read_knowledge("tagged")
    assert "Body content." in body


def test_knowledge_server_ranked_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Metadata matches rank above body line matches."""
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    # "security-review" has owasp in its *description* (parsed from the
    # title line) → metadata match; "other" only mentions owasp in its body.
    (knowledge_dir / "security-review.md").write_text(
        "# Security Review Checklist (OWASP)\nBody content about hardening.",
        encoding="utf-8",
    )
    (knowledge_dir / "other.md").write_text(
        "# Other Guide\nSome owasp checklist detail.", encoding="utf-8"
    )

    monkeypatch.setattr(km, "KNOWLEDGE_DIR", knowledge_dir)

    results = km.search_knowledge("owasp")
    assert results[0]["name"] == "security-review"
    assert results[0]["match"] == "metadata"
    assert {r["name"] for r in results} == {"security-review", "other"}


def test_knowledge_resource_handler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test knowledge_resource endpoint."""
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    (knowledge_dir / "sample.md").write_text("# Sample Content", encoding="utf-8")
    monkeypatch.setattr(km, "KNOWLEDGE_DIR", knowledge_dir)

    content = km.knowledge_resource("sample")
    assert content == "# Sample Content"

    missing = km.knowledge_resource("missing")
    assert "not found" in missing


def test_graph_memory_fallback_no_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test graceful fallback response when graph.json does not exist."""
    fake_graph_path = tmp_path / "graphify-out" / "nonexistent.json"
    monkeypatch.setattr(gm, "GRAPH_PATH", fake_graph_path)
    monkeypatch.setattr(gm, "_graph", None)

    res = gm.query_architecture("auth")
    assert not res["found"]
    assert "Graph not loaded" in res["error"]

    exp = gm.explain_symbol("UserService")
    assert not exp["found"]
    assert "Graph not loaded" in exp["error"]


def test_graph_memory_query_and_explain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test query_architecture and explain_symbol with an in-memory graph."""
    graph_data = {
        "directed": True,
        "multigraph": False,
        "graph": {},
        "nodes": [
            {
                "id": "AuthService",
                "community": 1,
                "file": "auth.py",
                "docstring": "Handles authentication",
            },
            {"id": "UserService", "community": 1, "file": "user.py", "docstring": "User models"},
            {"id": "DBPool", "community": 2, "file": "db.py", "docstring": "Database pool"},
        ],
        "links": [
            {"source": "AuthService", "target": "UserService", "type": "calls"},
            {"source": "UserService", "target": "DBPool", "type": "uses"},
        ],
    }
    graph_file = tmp_path / "graph.json"
    graph_file.write_text(json.dumps(graph_data), encoding="utf-8")

    monkeypatch.setattr(gm, "GRAPH_PATH", graph_file)
    monkeypatch.setattr(gm, "_graph", None)

    # Test query_architecture
    query_res = gm.query_architecture("AuthService")
    assert query_res["found"]
    assert query_res["node"]["id"] == "AuthService"
    assert len(query_res["callees"]) == 1
    assert query_res["callees"][0]["id"] == "UserService"

    # Test explain_symbol
    explain_res = gm.explain_symbol("UserService")
    assert explain_res["found"]
    assert explain_res["symbol"]["file"] == "user.py"
    assert explain_res["symbol"]["in_degree"] == 1
    assert explain_res["symbol"]["out_degree"] == 1

    # Test impact path
    path_res = gm.find_impact_path("AuthService", "DBPool")
    assert len(path_res) == 3
    assert [n["id"] for n in path_res] == ["AuthService", "UserService", "DBPool"]

    # Test community members
    comm_res = gm.get_community_members(1)
    assert len(comm_res) == 2
    assert {n["id"] for n in comm_res} == {"AuthService", "UserService"}

    # Test god nodes
    god_nodes = gm.list_god_nodes()
    assert len(god_nodes) == 3
    # UserService has degree 2 (1 in + 1 out)
    assert god_nodes[0]["id"] == "UserService"
    assert god_nodes[0]["total_degree"] == 2


def test_graph_memory_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test record_agent_memory writes to .graphify_learning.json."""
    learning_file = tmp_path / ".graphify_learning.json"
    monkeypatch.setattr(gm, "LEARNING_PATH", learning_file)
    monkeypatch.setattr(gm, "_learning", {})

    msg = gm.record_agent_memory(
        question="How does token refresh work?",
        decision="Use background rotation before expiry.",
        symbols=["AuthService", "TokenManager"],
        outcome="useful",
    )

    assert "Memory recorded" in msg
    assert learning_file.exists()
    data = json.loads(learning_file.read_text(encoding="utf-8"))
    assert "AuthService" in data
    assert "TokenManager" in data
    assert data["AuthService"][0]["outcome"] == "useful"
    assert data["AuthService"][0]["decision"] == "Use background rotation before expiry."


def test_tool_binding_matrix_enforcement() -> None:
    """Verify tool partitioning strictly follows the Tool Binding Matrix."""

    @tool
    def read_knowledge() -> str:
        """Read knowledge."""
        return ""

    @tool
    def search_knowledge() -> str:
        """Search knowledge."""
        return ""

    @tool
    def query_architecture() -> str:
        """Query architecture."""
        return ""

    @tool
    def explain_symbol() -> str:
        """Explain symbol."""
        return ""

    @tool
    def find_impact_path() -> str:
        """Find impact path."""
        return ""

    @tool
    def get_community_members() -> str:
        """Get community members."""
        return ""

    @tool
    def list_god_nodes() -> str:
        """List god nodes."""
        return ""

    all_tools = [
        read_knowledge,
        search_knowledge,
        query_architecture,
        explain_symbol,
        find_impact_path,
        get_community_members,
        list_god_nodes,
    ]

    partitioned = partition_tools_by_agent(all_tools)

    # Orchestrator has NO tools
    assert partitioned["orchestrator"] == []

    # Coder has read_knowledge, search_knowledge, explain_symbol (NO list_god_nodes)
    coder_tools = [t.name for t in partitioned["coder"]]
    assert "read_knowledge" in coder_tools
    assert "search_knowledge" in coder_tools
    assert "explain_symbol" in coder_tools
    assert "list_god_nodes" not in coder_tools
    assert "query_architecture" not in coder_tools

    # Refactor cleaner has query_architecture, list_god_nodes, explain_symbol
    refactor_tools = [t.name for t in partitioned["refactor_cleaner"]]
    assert "list_god_nodes" in refactor_tools
    assert "query_architecture" in refactor_tools
    assert "read_knowledge" not in refactor_tools
