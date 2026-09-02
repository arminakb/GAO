# GOA MCP

GOA exposes its orchestration intelligence over MCP (Model Context Protocol).
This is the PRIMARY integration path (TASK.md Mode A): your coding agent keeps
doing the coding; GOA adds analysis, routing, verification, review evidence,
recovery routing, memory, and Graphify impact. **No extra LLM API key needed.**

## Tools

| Tool | Purpose |
|---|---|
| `goa_analyze_task(task, session_id)` | Deterministic complexity/risk classification (TRIVIAL…CRITICAL), impact dimensions, blast radius, confidence, estimated tokens. |
| `goa_plan(task, session_id)` | Minimum orchestration plan: solo / medium / complex track, steps, specialists, budget. |
| `goa_route(task, session_id)` | One-shot analyze + plan. |
| `goa_verify(session_id)` | **Runs real verification** (pytest/mypy/ruff or npm/go/cargo equivalents) and returns machine-readable RED/GREEN evidence. Source of truth — agent claims of "tests pass" do not count. |
| `goa_repair(session_id)` | After a RED verdict: failure category → resolver routing + evidence excerpt. Bounded: 3 failed cycles → `escalate`. |
| `goa_review(session_id)` | Evidence packet for review: actual git diff, changed files, verification history, APPROVE-eligible / REJECT / REQUEST_REPAIR recommendation. |
| `goa_get_impact(symbol)` | Graphify impact analysis: downstream affected symbols, god-node detection, review-depth recommendation. |
| `goa_refresh_graph()` | `graphify update .` after meaningful changes so review sees fresh graph state. |
| `goa_record_memory(kind, content, tags)` / `goa_get_memory(query)` | Project memory with relevance-based retrieval (top-k, never a dump). |
| `goa_get_status(session_id)` | Session state: task, complexity, track, verification verdicts, retries, changed files, branch. |

## Setup

### Claude Code

```bash
cd /path/to/your/project
python /path/to/GraphAgentOrchestrator/goa_cli.py --workspace . init
# then run the printed command, e.g.:
claude mcp add goa -- /path/to/GraphAgentOrchestrator/.venv/bin/python \
    /path/to/GraphAgentOrchestrator/skills/servers/goa_mcp.py --workspace .
```

### Codex

```toml
# ~/.codex/config.toml
[mcp_servers.goa]
command = "/path/to/GraphAgentOrchestrator/.venv/bin/python"
args = ["/path/to/GraphAgentOrchestrator/skills/servers/goa_mcp.py", "--workspace", "/your/project"]
```

### OpenCode

```json
{
  "mcp": {
    "goa": {
      "type": "local",
      "command": {
        "command": "/path/to/GraphAgentOrchestrator/.venv/bin/python",
        "args": ["/path/to/GraphAgentOrchestrator/skills/servers/goa_mcp.py", "--workspace", "/your/project"]
      }
    }
  }
}
```

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `GOA_WORKSPACE` | cwd | Repository GOA operates on |
| `GOA_SHELL_TIMEOUT_S` | 300 | Shell command timeout |
| `GOA_MEMORY_PATH` | `.goa_memory.json` | Project memory file |
| `GRAPHIFY_GRAPH_PATH` | `graphify-out/graph.json` | Graphify graph for impact analysis |

## Recommended workflow (from Claude Code / Codex)

```
1. goa_analyze_task   → how much orchestration does this deserve?
2. goa_route          → get the plan (solo tasks: just implement, then step 3)
3. do the work        (you, the coding agent, as usual)
4. goa_verify         → real evidence; if RED:
5. goa_repair         → route to the right fix strategy, fix, back to 4
6. goa_refresh_graph  → if the change touched architecture-relevant code
7. goa_review         → approve/repair based on the evidence packet
```

For trivial tasks GOA explicitly recommends staying solo — that is the
cost model working as intended.
