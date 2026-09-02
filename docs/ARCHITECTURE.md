# GOA Architecture

> GOA 2.0 — Intelligent Task-Adaptive Orchestrator.

GOA is **not** a coding agent. It is an intelligence and orchestration layer
that sits on top of Claude Code, Codex, OpenCode, Cursor, or any MCP-compatible
agent, deciding how much orchestration a task actually deserves.

```
Developer
   ↓
Claude Code / Codex / OpenCode        ← execution engine (unchanged)
   ↓ MCP
GOA
   ├── Task Analysis      (deterministic classifier, no API key)
   ├── Adaptive Routing   (solo | medium | complex)
   ├── Skills             (skills/knowledge/*.md via Knowledge MCP)
   ├── Memory             (.goa_memory.json, relevance-based retrieval)
   ├── Graphify           (graph.json impact analysis)
   ├── Policies           (policies.json, enforced in code)
   ├── Verification       (real pytest/mypy/lint/build runs)
   ├── Review             (evidence packets: diff + verification history)
   └── Recovery           (failure-category → resolver, bounded retries)
   ↓
Verified Result
```

## Two execution modes

| | Mode A: External Agent (PRIMARY) | Mode B: Native GOA (SECONDARY) |
|---|---|---|
| Execution engine | Claude Code / Codex / OpenCode | GOA's own LangGraph agents |
| Model API keys | none extra | required (`LLM_PROVIDER`, `LLM_MODEL`) |
| Interface | `skills/servers/goa_mcp.py` | `harness/validator.py::run_graph` |
| LLM calls by GOA core | 0 (deterministic) | per-agent LLM calls |

The deterministic core (task analyzer, toolkit, verification engine, memory,
Graphify) is shared by both modes and needs no API key.

## Components

| Module | Purpose |
|---|---|
| `skills/servers/goa_mcp.py` | PRIMARY MCP interface (`goa_*` tools) |
| `harness/task_analyzer.py` | TRIVIAL→CRITICAL classifier + minimum-orchestration planner |
| `harness/toolkit.py` | sandboxed fs/shell/git tools; `ToolGate` enforces capabilities in code |
| `harness/verification.py` | toolchain detection, real command execution, failure classification, RED/GREEN evidence |
| `harness/validator.py` | native-mode LangGraph graph (policy routing, circuit breaker, fan-out) |
| `harness/state_schema.py` | Pydantic SSOT, audit trail (SHA-256 snapshots), telemetry |
| `harness/agent_executor.py` | native-mode agent invocation (prompt + structured output + budget guard) |
| `harness/llm_provider.py` | provider abstraction (openai/anthropic/google/ollama) |
| `skills/servers/knowledge_mcp.py` | skills/knowledge/*.md as MCP tools |
| `skills/servers/graph_memory_mcp.py` | Graphify graph queries + agent memory |
| `goa_cli.py` | `goa init / mcp serve / analyze / verify / status` |
| `policies.json` (via harness) | transitions, token limits, access rules, safety |

## Adaptive routing

`TaskAnalyzer` scores the task from deterministic signals (keyword dimensions,
scope, repo size, historical verification-failure rate) and maps it:

```
TRIVIAL/SIMPLE  → solo:     implement → verify            (0 orchestrator LLM calls)
MEDIUM          → plan → implement → verify → review
COMPLEX/CRITICAL→ plan (→ architect) → specialist fan-out → implement → verify
                  → review → repair-if-needed → final-verify
```

Budgets (`max_tokens`, `max_llm_calls`, `max_wall_time_s`, `max_retries`,
`max_cost_usd`) scale with the class and are enforced by code.

## Security model

- `ToolGate`: a tool is only reachable by an agent if the agent's capability
  set (READ/WRITE/EXECUTE/NETWORK/DESTRUCTIVE) covers it — checked in code,
  not prompts. Reviewer cannot write; coder cannot delete.
- All tool paths are confined to the workspace root (symlink escapes rejected).
- Banned operation patterns from `safety_rules` are matched before any shell
  command runs; `git_show` refs are validated against injection.
- Destructive ops (`delete_file`) are a separate capability from write.
- User work is never destroyed: GOA only appends evidence; resets/deletes of
  unrelated state are banned patterns in the MCP server.

## Auditability

Every run reconstructs from: `audit_trail` (per-transition SHA-256 state
snapshots), telemetry (tokens/cost/latency per node), verification records
(command/exit/output/timestamp), and the MCP session history — answering
"why did GOA activate this agent / skip that one / retry?"
