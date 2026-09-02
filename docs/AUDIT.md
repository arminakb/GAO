# GOA Architectural Audit (TASK.md §2) — 2026-09-01

Code-as-source-of-truth audit. Full execution path traced end-to-end.

## 1. Actual execution path (as implemented)

1. **Entry**: `harness/validator.py::run_graph(task_description)` — builds `PolicyLoader`, compiles graph via `compile_graph()`, invokes LangGraph with a `thread_id`, serializes final `ExecutionState` to `state.json`.
2. **State**: `harness/state_schema.py::ExecutionState` — strict Pydantic v2, single source of truth: task, phase, history (sliding window), errors, telemetry, token budget, audit trail (SHA-256 snapshots), agent performance, routing decision.
3. **Routing**: START → `orchestrator` node (LLM, structured output `RoutingDecision`) → conditional `route_to_agent` (validates forbidden/required transitions from `policies.json`, parallel fan-out via `Send`) → agent node → `route_after_agent` (circuit breaker on `max_retries`, budget check, direct handoffs) → orchestrator | END | `interrupt_handler` (LangGraph `interrupt()`).
4. **Agent execution**: `agent_executor.py::AgentExecutor.execute` — budget guard → prompt from `agents/<role>.md` + compressed state summary (last 5 msgs, last 3 errors, 500-char output cap) → LLM invoke with `TokenTracker` callback → cost calc, telemetry, message history, audit entry, performance update.
5. **Tools**: `TOOL_BINDING_MATRIX` (validator.py) partitions MCP tools per agent from two stdio MCP servers: `knowledge_mcp.py` (read_knowledge, search_knowledge, get_knowledge_list) and `graph_memory_mcp.py` (query_architecture, explain_symbol, find_impact_path, get_community_members, list_god_nodes, record_agent_memory, refresh_graph).
6. **Governance**: `policies.json` — forbidden/required transitions, direct handoffs, per-agent token limits, filesystem access rules (read/write/delete per role), safety rules (banned ops, human-approval list).
7. **Providers**: `llm_provider.py` — openai/anthropic/google/ollama via env `LLM_PROVIDER`/`LLM_MODEL`. **No API keys present in this environment; no `.env` file exists.**
8. **Graphify**: `graphify-out/graph.json` loaded into NetworkX in graph_memory MCP; learning persisted via `.graphify_learning.json` + Graphify CLI. `refresh_graph` tool exists.
9. **Tests**: 73 passing (`pytest tests -q`, 2.1s). Drift tests enforce prompt↔matrix sync (`_KNOWN_TOOLS`) and token ceilings.
10. **Benchmark**: `benchmark/` v3 harness (specs, judge_v3, anonymize, runs) already compares methodologies; sandboxes exist under `benchmark/sandboxes/{ecc,gao}`.

## 2. Answers to the 20 TASK questions (gaps marked ✗)

| # | Question | Status |
|---|---|---|
| 1–5 | entry / classify / select / route / state flow | Working, but **classification is the orchestrator LLM's implicit judgment only** — no deterministic complexity/risk classifier (✗ §4) |
| 6 | tools each agent can use | Read-only knowledge/graph MCP tools only |
| 7 | how code changes are produced | LLM text output only — **no tool call execution loop** (`bind_tools` binds but no ToolMessage round-trip) ✗ |
| 8 | how changes are applied | **Never** — no filesystem tool exists ✗ |
| 9–10 | tests executed / failures detected | **Never executed by GOA**; failure detection is prompt-level only ✗ |
| 11 | recovery | Routing to build_error_resolver on LLM judgment; no deterministic failure-category routing ✗ |
| 12 | memory | record_agent_memory + .graphify_learning.json; not injected into agent context ✗ |
| 13 | Graphify | refresh_graph tool exists but nothing in the graph calls it after code changes ✗ |
| 14–15 | MCP | Inbound: yes (2 servers). Outbound (goa.* server for Claude Code etc.): ✗ |
| 16–17 | wasted tokens / unnecessary LLM calls | Every task pays: orchestrator LLM routing even for trivial tasks; no solo path (✗ §5); orchestrator re-invoked after every agent |
| 18 | duplicated state | `last_agent_output` duplicates last message_history entry (minor) |
| 19 | too little context | Agents never see: diffs, file contents, verification output, memory — they see only a 5-message summary ✗ |
| 20 | too much context | Fixed 200-char message truncation regardless of relevance; whole state summary for every agent |

## 3. Key architectural findings

**F1 (critical)**: Agents cannot execute anything. The system is a prompt-routing pipeline, not an orchestration engine. No filesystem/shell/git tools exist anywhere in the repo.

**F2 (critical)**: No verification loop. Nothing ever runs pytest/mypy/builds; "tests pass" claims are unverifiable.

**F3 (critical)**: One-size-fits-all pipeline. A trivial typo fix pays full multi-agent routing cost. No TRIVIAL→CRITICAL classification, no budget-based graph construction.

**F4 (high)**: `bind_tools` without an agentic loop — if tools were bound, responses would contain tool_calls that are never executed.

**F5 (high)**: No deterministic recovery. Failure → resolver mapping exists in prose only.

**F6 (medium)**: Direct handoffs (`coder→reviewer`) exist but conditions ("code_changes_present") are not machine-checkable — nothing tracks changes.

**F7 (medium)**: Memory recorded but never retrieved into context.

**F8 (low)**: Reviewer must approve based on agent prose; no diff/test evidence contract.

**F9 (env)**: No LLM API keys in this environment → end-to-end LLM runs impossible here; deterministic layers (classifier, tools, verification, recovery) are fully testable with fake models.

## 4. Architecture correction (user directive, 2026-09-01 — OVERRIDES where it conflicts with TASK.md)

**PRIMARY mode = External Agent Mode.** GOA is an orchestration layer ON TOP OF
Claude Code / Codex / OpenCode, not a replacement for them. The external coding
agent remains the actual execution engine. GOA contributes: task analysis,
adaptive routing (solo vs multi-agent), skills, memory, Graphify impact,
policies, verification, review, recovery — all exposed via MCP
(`goa.analyze_task`, `goa.plan`, `goa.verify`, `goa.review`, `goa.get_status`,
`goa.get_impact`, `goa.get_memory`). Users must NOT need separate API keys:
their coding agent already has model access.

**SECONDARY mode = Native GOA** (direct LLM providers via llm_provider.py;
requires keys; standalone operation).

Consequences for the implementation plan:
- The goa MCP server (§P6) is the PRIMARY interface, not an add-on.
- The toolkit's deterministic layers (verification engine, tool gate, task
  analyzer) are exactly what the external agent consumes via MCP — they work
  without any API key.
- Native multi-agent LangGraph execution remains for the secondary mode.
- Benchmark arm C (GOA Orchestrator) should primarily measure
  "Claude Code + GOA MCP" vs "Claude Code solo".

## 5. What to preserve (TASK §30)

Policy-driven routing + policies.json, Pydantic SSOT, circuit breaker, budget guard, telemetry/cost attribution, SHA-256 audit snapshots, checkpointing, MCP infrastructure, skill architecture, drift tests. All good — extend, don't replace.

## 5. Implementation plan (phases per TASK §34)

- **P2 Tool layer**: `harness/toolkit.py` — READ/WRITE/EXECUTE/NETWORK/DESTRUCTIVE permission classes, sandboxed filesystem/shell/git tools, capability binding per agent enforced in code (not prompts). Local (in-process) tools; MCP exposure follows.
- **P3 Verification + recovery**: `harness/verification.py` — toolchain detection (pyproject/uv, package.json/npm, etc.), deterministic run + failure classification, RED/GREEN evidence records; recovery routing node keyed on failure category with bounded retries.
- **P4 Adaptive router**: `harness/task_analyzer.py` — deterministic complexity/risk classifier (signals: diff size, file count, keywords, repo toolchain, historical failure rate) → TRIVIAL/SIMPLE/MEDIUM/COMPLEX/CRITICAL → graph construction (solo path skips orchestrator LLM entirely).
- **P5 Graphify/memory**: auto refresh_graph after write-tool usage; retrieve memory into coder/reviewer context selectively.
- **P6 MCP out**: `skills/servers/goa_mcp.py` exposing goa.analyze_task / plan / execute / verify / get_status.
- **P7 Context**: replace fixed truncation with relevance-based context assembly; keep raw evidence in state, summarize per-agent.
- **P8 Benchmark**: extend benchmark/v3 harness; honest methodology, no post-hoc tuning.
- **P9 Docs**: ARCHITECTURE.md, MCP.md, AGENT_MODEL.md, VERIFICATION.md, COST_OPTIMIZATION.md, README quick-start.
