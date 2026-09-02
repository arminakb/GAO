"""Graph Memory MCP Server.

Provides architectural knowledge and memory via Graphify's knowledge graph.
Loads ``graphify-out/graph.json`` into a NetworkX ``DiGraph`` for fast
in-memory traversal.

Supports:
- Static queries (architecture, symbols, communities, impact paths)
- Dynamic learning (save-result / reflect via Graphify CLI)

Transport: stdio (default)

Start:
    python skills/servers/graph_memory_mcp.py
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

import networkx as nx
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

server = FastMCP("graph-memory-server")

GRAPH_PATH = Path(os.getenv("GRAPHIFY_GRAPH_PATH", "graphify-out/graph.json"))
LEARNING_PATH = Path(os.getenv("GRAPHIFY_LEARNING_PATH", ".graphify_learning.json"))

# Thread-safe graph state
_graph_lock = threading.Lock()
_graph: nx.DiGraph | None = None
_learning: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Graph loading
# ---------------------------------------------------------------------------


def _load_graph() -> nx.DiGraph | None:
    """Load graph.json into a NetworkX DiGraph."""
    if not GRAPH_PATH.is_file():
        logger.warning("Graph file not found: %s", GRAPH_PATH)
        return None
    try:
        with open(GRAPH_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if "links" in data and "edges" not in data:
            return nx.node_link_graph(data, edges="links")
        return nx.node_link_graph(data)
    except (json.JSONDecodeError, nx.NetworkXError, KeyError) as exc:
        logger.error("Failed to load graph: %s", exc)
        return None


def _load_learning() -> dict[str, Any]:
    """Load the Graphify learning overlay if it exists."""
    if not LEARNING_PATH.is_file():
        return {}
    try:
        with open(LEARNING_PATH, encoding="utf-8") as f:
            return json.load(f)  # type: ignore[no-any-return]
    except (json.JSONDecodeError, OSError):
        return {}


def _get_graph() -> nx.DiGraph | None:
    """Get the current graph, loading on first access."""
    global _graph, _learning
    with _graph_lock:
        if _graph is None:
            _graph = _load_graph()
            _learning = _load_learning()
        return _graph


def _node_to_dict(g: nx.DiGraph, node_id: str) -> dict[str, Any]:
    """Convert a graph node to a serializable dict."""
    if node_id not in g:
        return {"id": node_id, "error": "Node not found"}
    data = dict(g.nodes[node_id])
    data["id"] = node_id
    data["in_degree"] = g.in_degree(node_id)
    data["out_degree"] = g.out_degree(node_id)
    # Merge learning overlay if available
    if node_id in _learning:
        data["learned"] = _learning[node_id]
    return data


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@server.tool()
def query_architecture(concept: str) -> dict[str, Any]:
    """Query the architecture graph for a concept or symbol.

    Returns connected components, call hierarchies, and community
    membership for the given concept.

    Args:
        concept: The symbol, class, function, or module name to query.

    Returns:
        Dict with ``found``, ``node``, ``callers``, ``callees``, and
        ``community_members`` keys.
    """
    g = _get_graph()
    if g is None:
        return _no_graph_response()

    # Try exact match first, then case-insensitive prefix match
    node_id = _find_node(g, concept)
    if node_id is None:
        return {"found": False, "query": concept, "suggestion": "Try a more specific symbol name."}

    node = _node_to_dict(g, node_id)
    callers = [_node_to_dict(g, n) for n in g.predecessors(node_id)]
    callees = [_node_to_dict(g, n) for n in g.successors(node_id)]

    # Community members (if community attribute exists)
    community_id = g.nodes[node_id].get("community")
    community_members: list[dict[str, Any]] = []
    if community_id is not None:
        community_members = [
            _node_to_dict(g, n)
            for n in g.nodes
            if g.nodes[n].get("community") == community_id and n != node_id
        ][:20]  # Limit to 20

    return {
        "found": True,
        "node": node,
        "callers": callers[:15],
        "callees": callees[:15],
        "community_members": community_members,
    }


@server.tool()
def explain_symbol(symbol: str) -> dict[str, Any]:
    """Return full metadata for a specific symbol.

    Includes file location, type, community, degree, docstring, and
    any attached learning tags.

    Args:
        symbol: The exact symbol name (class, function, module).
    """
    g = _get_graph()
    if g is None:
        return _no_graph_response()

    node_id = _find_node(g, symbol)
    if node_id is None:
        return {"found": False, "symbol": symbol}

    return {"found": True, "symbol": _node_to_dict(g, node_id)}


@server.tool()
def find_impact_path(source: str, target: str) -> list[dict[str, Any]]:
    """Compute the shortest path between two symbols.

    Useful for understanding how changes to *source* propagate to *target*.

    Args:
        source: The starting symbol.
        target: The destination symbol.

    Returns:
        A list of node dicts along the shortest path, or an error dict.
    """
    g = _get_graph()
    if g is None:
        return [_no_graph_response()]

    src = _find_node(g, source)
    tgt = _find_node(g, target)
    if src is None or tgt is None:
        return [{"error": f"Could not find: {source if src is None else target}"}]

    try:
        path = nx.shortest_path(g, src, tgt)
        return [_node_to_dict(g, n) for n in path]
    except nx.NetworkXNoPath:
        return [{"error": f"No path from '{source}' to '{target}'."}]


@server.tool()
def get_community_members(community_id: int) -> list[dict[str, Any]]:
    """Return all symbols in a functional community (Leiden clustering).

    Args:
        community_id: The community/cluster identifier.

    Returns:
        List of node dicts belonging to the community.
    """
    g = _get_graph()
    if g is None:
        return [_no_graph_response()]

    members = [_node_to_dict(g, n) for n in g.nodes if g.nodes[n].get("community") == community_id]
    return members[:50]  # Cap at 50


@server.tool()
def list_god_nodes() -> list[dict[str, Any]]:
    """Return high-degree centrality nodes (potential refactoring targets).

    "God nodes" are symbols with unusually high connectivity — they may
    indicate design coupling issues.

    Returns:
        List of the top 15 most-connected nodes sorted by total degree.
    """
    g = _get_graph()
    if g is None:
        return [_no_graph_response()]

    degree_list = [(n, g.degree(n)) for n in g.nodes]
    degree_list.sort(key=lambda x: x[1], reverse=True)

    return [{**_node_to_dict(g, n), "total_degree": d} for n, d in degree_list[:15]]


@server.tool()
def record_agent_memory(
    question: str,
    decision: str,
    symbols: list[str],
    outcome: str,
) -> str:
    """Record an agent's reasoning outcome to Graphify's learning overlay.

    Uses ``graphify save-result`` to persist the learning for future recall.

    Args:
        question: The question the agent was trying to answer.
        decision: The decision or answer the agent arrived at.
        symbols: List of symbol names referenced in the reasoning.
        outcome: Must be one of: ``useful``, ``dead_end``, ``corrected``.

    Returns:
        Confirmation message or error description.
    """
    valid_outcomes = {"useful", "dead_end", "corrected"}
    if outcome not in valid_outcomes:
        return f"Invalid outcome '{outcome}'. Must be one of: {', '.join(sorted(valid_outcomes))}"

    cmd = [
        "graphify",
        "save-result",
        "--question",
        question,
        "--answer",
        decision,
        "--outcome",
        outcome,
    ]
    for sym in symbols:
        cmd.extend(["--nodes", sym])

    # Always persist to learning overlay file as well
    global _learning
    learning_data = _load_learning()
    entry = {
        "question": question,
        "decision": decision,
        "symbols": symbols,
        "outcome": outcome,
    }
    for sym in symbols:
        learning_data.setdefault(sym, []).append(entry)

    try:
        LEARNING_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LEARNING_PATH, "w", encoding="utf-8") as f:
            json.dump(learning_data, f, indent=2)
        _learning = learning_data
    except OSError:
        pass

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode == 0:
            # Reload learning overlay if graphify produced changes
            _learning = _load_learning()
            return f"Memory recorded successfully: {outcome} for '{question[:80]}'"
        return f"Memory recorded locally: {outcome} for '{question[:80]}'"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return f"Memory recorded locally: {outcome} for '{question[:80]}'"


@server.tool()
def refresh_graph() -> str:
    """Re-parse changed files and reload the in-memory graph.

    Runs ``graphify update .`` to incrementally update the knowledge graph,
    then reloads the NetworkX ``DiGraph`` from the updated ``graph.json``.

    Returns:
        Confirmation message or error description.
    """
    global _graph, _learning

    try:
        result = subprocess.run(
            ["graphify", "update", "."],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            return f"graphify update failed (code {result.returncode}): {result.stderr[:200]}"
    except FileNotFoundError:
        return "graphify CLI not found. Install with: pip install 'graphifyy[mcp]>=0.8'"
    except subprocess.TimeoutExpired:
        return "graphify update timed out (120s)."

    with _graph_lock:
        _graph = _load_graph()
        _learning = _load_learning()

    node_count = len(_graph.nodes) if _graph else 0
    return f"Graph refreshed: {node_count} nodes loaded."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_node(g: nx.DiGraph, query: str) -> str | None:
    """Find a node by exact match or case-insensitive prefix."""
    if query in g:
        return query

    query_lower = query.lower()
    # Exact case-insensitive match
    for n in g.nodes:
        if str(n).lower() == query_lower:
            return str(n)

    # Prefix match
    candidates = [str(n) for n in g.nodes if str(n).lower().startswith(query_lower)]
    return candidates[0] if len(candidates) == 1 else None


def _no_graph_response() -> dict[str, Any]:
    """Standard response when graph.json is not loaded."""
    return {
        "found": False,
        "error": f"Graph not loaded. File not found: {GRAPH_PATH}",
        "suggestion": (
            "Run 'graphify init .' then 'graphify build . --code-only' to generate the graph."
        ),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    server.run()
