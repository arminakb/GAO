"""GOA MCP Server — the PRIMARY interface (TASK.md §1, §9, Mode A).

Exposes GOA's orchestration intelligence to external coding agents
(Claude Code, Codex, OpenCode, Cursor, ...) over MCP. The external agent
remains the execution engine; GOA contributes:

* task analysis + adaptive routing      → goa_analyze_task, goa_plan, goa_route
* verification + RED/GREEN evidence     → goa_verify
* recovery routing                      → goa_repair
* Graphify impact intelligence          → goa_get_impact, goa_refresh_graph
* project memory (selective retrieval)  → goa_get_memory, goa_record_memory
* session/status                        → goa_get_status, goa_session

No LLM API key is required in this mode: every tool is deterministic.

Start:
    python skills/servers/goa_mcp.py            # stdio (default)

Claude Code registration:
    claude mcp add goa -- python /path/to/skills/servers/goa_mcp.py

Codex registration (~/.codex/config.toml):
    [mcp_servers.goa]
    command = "python"
    args = ["/path/to/skills/servers/goa_mcp.py"]
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mcp.server.fastmcp import FastMCP

from harness.context import estimate_tokens, smart_excerpt
from harness.task_analyzer import TaskAnalyzer, build_plan
from harness.toolkit import ExecutionToolkit as ExecutionToolkit
from harness.verification import VerificationEngine as VerificationEngine
from harness.verification import VerificationRecord

# ---------------------------------------------------------------------------
# Server + workspace-scoped session state
# ---------------------------------------------------------------------------

server = FastMCP("goa-orchestrator")

_WORKSPACE = Path(os.getenv("GOA_WORKSPACE", Path.cwd())).resolve()
_TOOLKIT = ExecutionToolkit(
    _WORKSPACE,
    banned_patterns=[
        "rm -rf /",
        "sudo rm",
        "DROP DATABASE",
        "TRUNCATE TABLE",
        "rm -rf ~",
        "mkfs",
        "> /dev/sda",
        "git push --force",
        "git reset --hard",
    ],
    shell_timeout_s=int(os.getenv("GOA_SHELL_TIMEOUT_S", "300")),
)
_VERIFIER = VerificationEngine(_TOOLKIT)

#: Per-session orchestration state keyed by session id (TASK.md §33).
_SESSIONS: dict[str, dict[str, Any]] = {}


def _session(session_id: str) -> dict[str, Any]:
    return _SESSIONS.setdefault(
        session_id,
        {
            "created_at": datetime.now(UTC).isoformat(),
            "workspace": str(_WORKSPACE),
            "task": None,
            "analysis": None,
            "plan": None,
            "verification_record": VerificationRecord().model_dump(),
            "llm_calls": 0,  # always 0 in external mode; deterministic core
            "tool_calls": 0,
            "retries": 0,
            "history": [],
        },
    )


def _historical_failure_rate() -> float:
    """Failure rate across this session's verification runs (adaptive signal)."""
    reports = _evidence_record().entries
    if not reports:
        return 0.0
    return sum(1 for r in reports if not r.passed) / len(reports)


def _evidence_record() -> VerificationRecord:
    rec = VerificationRecord.model_validate(_SESSIONS.setdefault("_record", {"entries": []}))
    return rec


def _save_record(rec: VerificationRecord) -> None:
    _SESSIONS["_record"] = rec.model_dump()


def _retrieve_memory(query: str, limit: int = 3) -> list[dict[str, Any]]:
    """Best-effort selective memory retrieval for context injection (TASK §18)."""
    try:
        hits: list[dict[str, Any]] = _memory_get(query, limit)
        return hits
    except Exception:  # noqa: BLE001 — memory must never break analysis
        return []


def _auto_graph_refresh_enabled() -> bool:
    """Auto-refresh is best-effort and off by default (TASK §17, cost-aware)."""
    return os.getenv("GOA_AUTO_GRAPH_REFRESH", "0") == "1"


#: Code-enforced budget: max RED verify cycles before escalation (TASK §6).
_MAX_VERIFY_CYCLES = int(os.getenv("GOA_MAX_VERIFY_CYCLES", "3"))

#: Default excerpt sizes for MCP packets (chars; smart-excerpted, not destroyed).
_REVIEW_DIFF_CHARS = 8000
_REPAIR_EVIDENCE_CHARS = 3000


def _maybe_refresh_graph() -> dict[str, Any]:
    """Run goa_refresh_graph, swallowing all failures — never blocks verify."""
    try:
        return dict(goa_refresh_graph())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Task intelligence
# ---------------------------------------------------------------------------


@server.tool()
def goa_analyze_task(task: str, session_id: str = "default") -> dict[str, Any]:
    """Analyze a coding task: complexity, risk, blast radius, confidence.

    Deterministic — no API key needed. Returns the TRIVIAL..CRITICAL
    classification, impact dimensions, estimated cost, and the reasoning
    signals. Call this FIRST for any non-trivial task.
    """
    s = _session(session_id)
    analyzer = TaskAnalyzer(_WORKSPACE, historical_failure_rate=_historical_failure_rate())
    analysis = analyzer.analyze(task)
    s["task"] = task
    s["analysis"] = analysis.model_dump(mode="json")
    memory = _retrieve_memory(task, limit=3)
    s["relevant_memory"] = memory
    s["history"].append({"ts": datetime.now(UTC).isoformat(), "event": "analyze"})
    out = analysis.model_dump(mode="json")
    out["relevant_memory"] = memory
    return out


@server.tool()
def goa_plan(task: str, session_id: str = "default") -> dict[str, Any]:
    """Build the MINIMUM orchestration plan for a task (adaptive routing).

    Solo tasks get: implement → verify. Complex tasks get planner →
    specialists → implement → verify → review → repair → final-verify.
    Returns the plan plus a rationale explaining why this depth was chosen.
    """
    s = _session(session_id)
    if s.get("analysis") and s["analysis"]["task"] == task:
        from harness.task_analyzer import TaskAnalysis

        task_analysis = TaskAnalysis.model_validate(s["analysis"])
    else:
        analyzer = TaskAnalyzer(_WORKSPACE)
        task_analysis = analyzer.analyze(task)
    plan = build_plan(task_analysis)
    s["plan"] = plan.model_dump(mode="json")
    s["history"].append({"ts": datetime.now(UTC).isoformat(), "event": "plan"})
    return plan.model_dump(mode="json")


@server.tool()
def goa_route(task: str, session_id: str = "default") -> dict[str, Any]:
    """One-shot analyze+plan: the routing decision GOA recommends.

    Returns {complexity, track, steps, specialists, budget, rationale}.
    """
    goa_analyze_task(task, session_id)
    plan = goa_plan(task, session_id)
    s = _session(session_id)
    return {
        "complexity": s["analysis"]["complexity"] if s.get("analysis") else None,
        "confidence": s["analysis"]["confidence"] if s.get("analysis") else None,
        **plan,
    }


# ---------------------------------------------------------------------------
# Verification + recovery (the deterministic core)
# ---------------------------------------------------------------------------


@server.tool()
def goa_verify(session_id: str = "default") -> dict[str, Any]:
    """Run REAL repository verification (tests, typecheck, lint, build).

    Detects the toolchain from manifests (uv/pyproject, package.json, go.mod,
    Cargo.toml), executes the appropriate commands, and returns machine-readable
    RED/GREEN evidence: command, exit code, output excerpt, duration.

    Never claim tests pass without calling this. This is the source of truth.
    """
    s = _session(session_id)
    record = _evidence_record()
    failed_cycles = sum(1 for r in record.entries if not r.passed)
    if failed_cycles >= _MAX_VERIFY_CYCLES:
        s["history"].append(
            {"ts": datetime.now(UTC).isoformat(), "event": "verify_blocked", "reason": "budget"}
        )
        return {
            "verdict": "BUDGET_EXHAUSTED",
            "passed": False,
            "summary": (
                f"verify-cycle budget exhausted ({failed_cycles}/{_MAX_VERIFY_CYCLES} RED "
                "cycles); escalate to human — further runs blocked by code, not prompt"
            ),
            "failure_category": None,
            "commands": [],
            "changed_files_hint": _changed_files(),
            "graph_refresh": None,
            "budget": {"max_verify_cycles": _MAX_VERIFY_CYCLES, "failed_cycles": failed_cycles},
        }
    report = _VERIFIER.run()
    record.add(report)
    _save_record(record)
    if not report.passed:
        s["retries"] = int(s.get("retries", 0)) + 1
    graph_refresh: dict[str, Any] | None = None
    if report.passed and _auto_graph_refresh_enabled():
        changed = _changed_files()
        if changed:
            graph_refresh = _maybe_refresh_graph()
            s["history"].append(
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "event": "graph_refresh",
                    "ok": bool(graph_refresh.get("ok", False)),
                }
            )
    s["history"].append(
        {"ts": datetime.now(UTC).isoformat(), "event": "verify", "passed": report.passed}
    )
    return {
        "verdict": report.red_green,
        "passed": report.passed,
        "summary": report.summary,
        "failure_category": report.failure_category.value if report.failure_category else None,
        "commands": [c.model_dump(mode="json") for c in report.commands],
        "changed_files_hint": _changed_files(),
        "graph_refresh": graph_refresh,
        "estimated_tokens": estimate_tokens("\n".join(c.output_excerpt for c in report.commands)),
        "budget": {"max_verify_cycles": _MAX_VERIFY_CYCLES},
    }


@server.tool()
def goa_repair(session_id: str = "default") -> dict[str, Any]:
    """After a failed goa_verify, get the recovery routing.

    Maps the failure category (test_failure, type_error, build_error, ...)
    to the resolver that should fix it, with the captured evidence. Bounded:
    after 3 failed cycles the verdict becomes 'escalate'.
    """
    record = _evidence_record()
    last = record.last
    if last is None:
        return {"verdict": "no-verification-run", "action": "call goa_verify first"}
    if last.passed:
        return {"verdict": "GREEN", "action": "none — verification already passed"}
    target = _VERIFIER.recovery_target(last)
    cycles_failed = sum(1 for r in record.entries if not r.passed)
    if cycles_failed >= 3:
        return {
            "verdict": "escalate",
            "action": "bounded retries exhausted; escalate to human",
            "failure_category": last.failure_category.value if last.failure_category else None,
            "evidence": last.model_dump(mode="json"),
        }
    return {
        "verdict": "RED",
        "action": f"route to {target}",
        "resolver": target,
        "failure_category": last.failure_category.value if last.failure_category else None,
        "failure_command": last.failure_command,
        "evidence_excerpt": (
            smart_excerpt(last.commands[-1].output_excerpt, _REPAIR_EVIDENCE_CHARS)
            if last.commands
            else ""
        ),
        "cycles_failed": cycles_failed,
    }


@server.tool()
def goa_review(evidence_only: bool = True, session_id: str = "default") -> dict[str, Any]:
    """Evidence-based review packet: diff, changed files, verification history.

    Returns everything a reviewer needs to APPROVE / REJECT / REQUEST_REPAIR —
    actual git diff and verification evidence, not agent prose.
    """
    s = _session(session_id)
    diff = _TOOLKIT.git_diff()
    record = _evidence_record()
    changed = _changed_files()
    impact, memory = _review_intelligence(changed, s)
    diff_excerpt = smart_excerpt(diff.output, _REVIEW_DIFF_CHARS)
    return {
        "git_diff": diff_excerpt,
        "changed_files": changed,
        "verification_history": [
            {"passed": r.passed, "summary": r.summary, "commands": [c.check for c in r.commands]}
            for r in record.entries
        ],
        "tool_calls": s.get("tool_calls", 0),
        "recommendation": _review_recommendation(record),
        "graphify_impact": impact,
        "relevant_memory": memory,
        "estimated_tokens": estimate_tokens(diff_excerpt),
    }


def _review_intelligence(
    changed_files: list[str], s: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Graphify impact + relevant memory for the review packet (TASK §15, §16).

    Both are best-effort: any failure degrades to empty values, never an error.
    """
    impact: dict[str, Any] = {"available": False}
    if changed_files:
        symbols = [Path(f).stem for f in changed_files]
        try:
            impact = goa_get_impact(symbols[0])
            if impact.get("is_god_node"):
                impact["review_depth"] = "increased"
        except Exception:  # noqa: BLE001
            impact = {"available": False, "error": "impact lookup failed"}
        impact["symbols_probed"] = symbols[:5]
    memory = s.get("relevant_memory") or _retrieve_memory(" ".join(changed_files), limit=3)
    return impact, memory


def _review_recommendation(record: VerificationRecord) -> str:
    last = record.last
    if last is None:
        return "REQUEST_REPAIR: no verification evidence exists"
    if not last.passed:
        return f"REJECT: {last.summary}"
    return "APPROVE-eligible: latest verification GREEN — review diff before final approval"


def _changed_files() -> list[str]:
    st = _TOOLKIT.git_status()
    if not st.ok or not st.output.strip():
        return []
    files = []
    for line in st.output.splitlines():
        if len(line) > 3:
            files.append(line[3:].strip())
    return files


# ---------------------------------------------------------------------------
# Graphify intelligence
# ---------------------------------------------------------------------------


@server.tool()
def goa_get_impact(symbol: str) -> dict[str, Any]:
    """Graphify impact analysis for a changed/changed-to-be symbol.

    Returns affected modules, dependency paths, and whether the symbol is a
    high-connectivity 'god node' (which should raise review depth).
    """
    graph_path = Path(os.getenv("GRAPHIFY_GRAPH_PATH", "graphify-out/graph.json"))
    if not graph_path.is_file():
        return {"available": False, "reason": "no graphify-out/graph.json — run graphify build"}
    import networkx as nx

    with open(graph_path, encoding="utf-8") as f:
        data = json.load(f)
    g = nx.DiGraph()
    for n in data.get("nodes", []):
        g.add_node(n.get("id", n) if isinstance(n, dict) else n)
    edges = data.get("edges", data.get("links", []))
    for e in edges:
        src = e.get("source", e.get("from"))
        dst = e.get("target", e.get("to"))
        if src and dst:
            g.add_edge(src, dst)
    node_id = (
        symbol if symbol in g else next((n for n in g if symbol.lower() in str(n).lower()), None)
    )
    if node_id is None:
        return {"available": True, "found": False}
    in_deg, out_deg = g.in_degree(node_id), g.out_degree(node_id)
    god_threshold = max(10, int(len(g.edges) / max(len(g.nodes), 1) * 5))
    descendants = list(nx.descendants(g, node_id))[:50] if out_deg else []
    return {
        "available": True,
        "found": True,
        "symbol": str(node_id),
        "in_degree": in_deg,
        "out_degree": out_deg,
        "is_god_node": in_deg + out_deg >= god_threshold,
        "review_depth": "increased" if in_deg + out_deg >= god_threshold else "normal",
        "affected_downstream": sorted(str(d) for d in descendants)[:30],
        "blast_radius": "high" if len(descendants) > 20 else "medium" if descendants else "local",
    }


@server.tool()
def goa_refresh_graph() -> dict[str, Any]:
    """Refresh the Graphify knowledge graph after meaningful code changes.

    Runs `graphify update .` then reports success. Review must operate on
    fresh graph state — call after implementation, before review.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["graphify", "update", "."],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(_WORKSPACE),
            check=False,
        )
        ok = result.returncode == 0
        return {"ok": ok, "output": (result.stdout or result.stderr)[-2000:]}
    except FileNotFoundError:
        return {"ok": False, "error": "graphify CLI not installed"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "graphify update timed out"}


# ---------------------------------------------------------------------------
# Memory (selective retrieval, TASK.md §18)
# ---------------------------------------------------------------------------

_MEMORY_PATH = Path(os.getenv("GOA_MEMORY_PATH", ".goa_memory.json"))


@server.tool()
def goa_record_memory(
    kind: str, content: str, tags: list[str] | None = None, session_id: str = "default"
) -> str:
    """Persist project-level engineering memory.

    kind: one of architecture_decision | recurring_bug | failed_approach |
    successful_fix | convention | verification_failure | task_outcome.
    Retrieval is relevance-based — do not dump everything into context.
    """
    entries: list[dict[str, Any]] = []
    if _MEMORY_PATH.is_file():
        try:
            entries = json.loads(_MEMORY_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            entries = []
    entries.append(
        {
            "kind": kind,
            "content": content,
            "tags": tags or [],
            "ts": datetime.now(UTC).isoformat(),
            "session": session_id,
        }
    )
    _MEMORY_PATH.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    return f"recorded {kind} memory ({len(entries)} total)"


@server.tool()
def goa_get_memory(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Selectively retrieve project memory relevant to *query*.

    Relevance = tag/keyword overlap + kind match. Only the top *limit*
    entries are returned — never the full memory dump.
    """
    if not _MEMORY_PATH.is_file():
        return []
    try:
        entries = json.loads(_MEMORY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    terms = set(query.lower().split())

    def score(e: dict[str, Any]) -> int:
        hay = " ".join(
            [e.get("kind", ""), e.get("content", ""), " ".join(e.get("tags", []))]
        ).lower()
        return len(terms & set(hay.split()))

    ranked = sorted(entries, key=score, reverse=True)
    return [e for e in ranked if score(e) > 0][: max(1, limit)]


# ---------------------------------------------------------------------------
# Status / session
# ---------------------------------------------------------------------------


#: Alias so pre-definition helpers can retrieve memory without depending on
#: decorator ordering — FastMCP decorators return the same callable.
_memory_get = goa_get_memory


@server.tool()
def goa_get_status(session_id: str = "default") -> dict[str, Any]:
    """Current orchestration session state: task, plan, verification, git."""
    s = _session(session_id)
    record = _evidence_record()
    return {
        "session_id": session_id,
        "workspace": str(_WORKSPACE),
        "task": s.get("task"),
        "complexity": (s.get("analysis") or {}).get("complexity"),
        "track": (s.get("plan") or {}).get("track"),
        "verification_runs": len(record.entries),
        "last_verdict": record.last.red_green if record.last else None,
        "retries": s.get("retries", 0),
        "changed_files": _changed_files(),
        "branch": _TOOLKIT.git_status().output and _branch() or None,
        "llm_calls": 0,
        "mode": "external-agent (deterministic core, no API key)",
    }


def _branch() -> str | None:
    res = _TOOLKIT.run_command("git rev-parse --abbrev-ref HEAD", timeout_s=10)
    return res.output.strip() or None


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
