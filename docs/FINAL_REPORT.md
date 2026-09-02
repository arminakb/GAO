# GOA 2.0 Final Report: Intelligent Task-Adaptive Orchestrator

**Date:** 2026-09-02  
**Reference:** TASK.md §47 (Final Report Required), §44 (Migration Strategy), §45 (Acceptance Criteria)  
**Status:** All 9 Phases Complete · 170 Unit & Integration Tests Passing · Full Verification Clean

---

## 1. What Changed

GOA has been transformed from a fixed-sequence multi-agent conceptual pipeline into a production-grade, **Intelligent Task-Adaptive Orchestrator**. 

Key architectural transformations:
1. **Primary Operational Paradigm Shift (External Agent Mode over MCP):**
   GOA does not compete with or duplicate Claude Code, Codex, or OpenCode. Instead, the external coding agent serves as the execution engine, while GOA provides the intelligence, verification, recovery, memory, and Graphify impact layer. In this primary mode, **zero additional LLM provider API keys are required**.
2. **Deterministic Task-Adaptive Routing:**
   Replaced rigid graph traversal with deterministic task classification (`TRIVIAL`, `SIMPLE`, `MEDIUM`, `COMPLEX`, `CRITICAL`). Simple tasks route **solo** (implement → verify) with zero extra LLM calls or latency overhead; complex tasks dynamically activate planning, specialist fan-out, and deeper reviews.
3. **Deterministic Verification & Closed-Loop Recovery:**
   Eliminated natural-language claims of "tests pass." The system mechanically discovers project toolchains (`uv`, `pytest`, `mypy`, `npm`, `cargo`, `go`), runs verification commands, captures exit codes and stdout/stderr, classifies failure types (`test_failure`, `type_error`, `build_error`, `syntax_error`), and drives bounded recovery routes with verifiable RED→GREEN evidence.
4. **Context & Token Optimization (Minimum Sufficient Context):**
   Implemented relevance-preserving smart excerpting (`smart_excerpt`) keeping both header context and tail failure evidence. Introduced programmatic token estimation and hard budget caps (`GOA_MAX_VERIFY_CYCLES = 3`) that prevent runaway loops in code.
5. **Graphify & Memory Operationalization:**
   Connected project memory (`.goa_memory.json`) and architecture graphs (`graph.json`) directly into session routing and review packets, automatically escalating review depth when high-centrality "god nodes" are touched.

---

## 2. Files Changed

| File / Component | Purpose & Architectural Role |
|---|---|
| `skills/servers/goa_mcp.py` | Primary FastMCP server exposing `goa_analyze_task`, `goa_route`, `goa_verify`, `goa_repair`, `goa_review`, `goa_get_impact`, `goa_refresh_graph`, `goa_get_memory`, `goa_record_memory`, `goa_get_status`. |
| `harness/task_analyzer.py` | Deterministic complexity/risk classifier, keyword-signal evaluator, and minimum-orchestration plan generator. |
| `harness/toolkit.py` | Sandboxed filesystem, shell, and git execution layer with code-level capability enforcement (`ToolGate`). |
| `harness/verification.py` | Toolchain detector, deterministic command runner, failure classifier, and machine-readable RED→GREEN record tracker. |
| `harness/context.py` | Relevance-preserving smart excerpting (head + tail preservation) and chars/4 token estimation. |
| `harness/validator.py` | Secondary Native Mode LangGraph graph, policy validator, and tool binder. |
| `goa_cli.py` | Unified CLI front-end for agent initialization, MCP serving, manual analysis, verification, and status checks. |
| `benchmark/v4_runner.py` | Agent-agnostic v4 benchmark harness running mechanical verification gates on fresh sandboxes. |
| `benchmark/v4_arms.py` | Arm executors driving Solo, ECC, and GOA Orchestrator live against OpenCode. |
| `tests/` | 170 tests across 16 test files verifying routing, security gates, verification, context optimization, and benchmark harnesses. |
| `docs/` | Complete documentation suite (`ARCHITECTURE.md`, `MCP.md`, `AGENT_MODEL.md`, `VERIFICATION.md`, `COST_OPTIMIZATION.md`, `MEMORY.md`, `GRAPHIFY.md`, `BENCHMARK.md`). |

---

## 3. Current Architecture

The final execution architecture operates across two distinct modes:

```text
                                [ Developer ]
                                      ↓
                     [ External Coding Agent ]
                 (Claude Code / Codex / OpenCode)
                                      ↓ (MCP Protocol)
┌────────────────────────────────────────────────────────────────────────┐
│                        GOA MCP Interface                               │
│  (goa_analyze_task, goa_route, goa_verify, goa_repair, goa_review)     │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
       ┌───────────────────────────┴───────────────────────────┐
       ▼                                                       ▼
[ Mode A: External Execution ]                        [ Mode B: Native GOA ]
 - Coding agent writes code                             - LangGraph Agent Graph
 - GOA analyzes & plans route                           - Direct Provider APIs
 - GOA runs mechanical verification                     - Specialist fan-out
 - GOA routes recovery on RED                           - Policy-gated nodes
       │                                                       │
       └───────────────────────────┬───────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     GOA Deterministic Core Services                    │
│  ├── TaskAnalyzer         (Heuristic + Regex Signal Evaluator)         │
│  ├── ExecutionToolkit     (Confined Filesystem / Shell / Git)          │
│  ├── VerificationEngine   (Toolchain Discovery + Fail-Fast Gates)      │
│  ├── ContextOptimizer     (Smart Excerpting + Token Accounting)        │
│  ├── Graphify Engine      (Dependency Traversal + God-Node Detection)  │
│  ├── Project Memory       (Selective Top-K Relevance Retrieval)        │
│  └── Code Governance      (Hard Retry & Token Budget Enforcement)      │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 4. External Agent Integration

Normal developers connect GOA to their coding environment with zero LangGraph overhead and zero separate model credentials.

### A. Claude Code Setup
Run initialization:
```bash
python goa_cli.py --workspace . init
```
Add to Claude Code:
```bash
claude mcp add goa -- /path/to/GraphAgentOrchestrator/.venv/bin/python \
    /path/to/GraphAgentOrchestrator/skills/servers/goa_mcp.py --workspace .
```

### B. Codex Setup (`~/.codex/config.toml`)
```toml
[mcp_servers.goa]
command = "/path/to/GraphAgentOrchestrator/.venv/bin/python"
args = ["/path/to/GraphAgentOrchestrator/skills/servers/goa_mcp.py", "--workspace", "/your/project"]
```

### C. OpenCode Setup (`opencode.json`)
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

---

## 5. Native Mode

In Native GOA Mode (Mode B), GOA directly orchestrates LangGraph agents (`orchestrator`, `planner`, `architect`, `coder`, `reviewer`) using direct LLM provider abstractions.

### Provider Configuration:
```bash
# Provider Selection (openai | anthropic | google | ollama)
export LLM_PROVIDER=anthropic
export LLM_MODEL=claude-sonnet-4-20250514
export ANTHROPIC_API_KEY="sk-ant-..."

# Or OpenAI:
# export LLM_PROVIDER=openai
# export LLM_MODEL=gpt-4o
# export OPENAI_API_KEY="sk-..."

# Or Local Ollama:
# export LLM_PROVIDER=ollama
# export LLM_MODEL=llama3.1:8b
# export OLLAMA_BASE_URL="http://localhost:11434"
```

### Execution:
```bash
python -c "import asyncio; from harness.validator import run_graph; \
           asyncio.run(run_graph('implement user authentication middleware'))"
```

---

## 6. Adaptive Routing

GOA evaluates keyword dimensions, blast radius, repository scale, and historical failure rate to assign tasks into dynamic tiers:

1. **TRIVIAL Task:**
   - *Example:* "Fix typo in docstring" or "Bump package version to 1.2.1"
   - *Route:* Track `solo` (`["implement", "verify"]`).
   - *Behavior:* Zero orchestrator LLM calls. The coding agent applies the edit; GOA executes mechanical verification.
2. **SIMPLE Task:**
   - *Example:* "Add docstrings and type annotations to math_utils.py"
   - *Route:* Track `solo` with standard verification gates.
3. **MEDIUM Task:**
   - *Example:* "Add pagination and filtering parameters to GET /users"
   - *Route:* Track `medium` (`["plan", "implement", "verify", "review"]`).
   - *Behavior:* Formulates a structured plan, executes changes, runs deterministic verification, and produces an evidence review packet.
4. **COMPLEX / CRITICAL Task:**
   - *Example:* "Refactor database connection pool to support multi-region replicas"
   - *Route:* Track `complex` (`["plan", "specialists", "implement", "verify", "review"]`).
   - *Behavior:* Deploys architectural and database specialist prompts, enforces maximum test coverage, activates god-node impact probes, and runs multi-cycle repair routing on failures.

---

## 7. Cost Analysis

| Strategy | Simple Tasks (80% of Volume) | Complex Tasks (20% of Volume) | Failure Handling | Overall Cost Efficiency |
|---|---|---|---|---|
| **Solo Execution** | Cheapest (0 overhead) | High failure rate, unverified claims | No recovery; requires manual user debugging | Cheap up front, expensive in developer time |
| **Fixed Multi-Agent (ECC / Heavy Graph)** | High overhead (full planning & review on typos) | Thorough but prone to unbounded self-reflection loops | Loops until timeout or prompt hallucination | Consistently expensive across all tasks |
| **Adaptive GOA 2.0** | **Cheapest** (0 orchestrator LLM calls, solo track) | **Targeted depth** (specialists & review enabled only when justified) | **Deterministic repair** with hard budget ceilings | **Optimized for total engineering outcome** |

---

## 8. Verification

GOA replaces subjective agent self-evaluation with **deterministic toolchain execution**:
1. **Discovery:** Dynamically inspects `uv.lock`, `pyproject.toml`, `package.json`, `go.mod`, or `Cargo.toml`.
2. **Execution:** Runs unit tests, linters, and type-checkers in confined workspace environments with strict timeouts and banned command filtering.
3. **Classification:** Parses outputs for explicit markers:
   - `AssertionError` / `FAILED` → `test_failure`
   - `SyntaxError` → `syntax_error`
   - `ModuleNotFoundError` → `dependency_error`
   - `mypy error` / `TS error` → `type_error`
4. **RED→GREEN Trail:** Tracks every execution in session history. An agent cannot complete a task unless a verified GREEN record exists.

---

## 9. Graphify

Graphify provides architectural static analysis (`graph.json`):
1. **Impact Paths:** Queries symbol dependencies via `find_impact_path` and `explain_symbol`.
2. **God-Node Detection:** Flags changes to symbols with high degrees ($d \ge \max(10, 5 \times \frac{E}{N})$). Touching a god-node automatically escalates `review_depth: "increased"`.
3. **Freshness Discipline:** `goa_refresh_graph` triggers incremental AST updates after code changes, ensuring review decisions evaluate latest repository states.

---

## 10. Memory

GOA stores engineering memory in `.goa_memory.json`:
- **Content:** Architecture decisions, recurring bugs, failed approaches, and verified fixes.
- **Selective Retrieval:** Retrieved via term-overlap scoring (`goa_get_memory`). Never dumps entire logs into agent prompts.
- **Adaptive Feedback:** Feeds the repository's `historical_failure_rate` back into `TaskAnalyzer`. If recent tasks repeatedly fail verification, future complexity ratings are automatically escalated.

---

## 11. MCP Protocol & Interface

GOA runs a FastMCP stdio server exposing 11 production tools:
- `goa_analyze_task`: Complexity and blast radius estimation.
- `goa_plan`: Minimum orchestration planning.
- `goa_route`: Combined analyze and route.
- `goa_verify`: Mechanical test/lint/type execution.
- `goa_repair`: Evidence-based error resolver routing.
- `goa_review`: Review packet synthesis (diff + test history + impact).
- `goa_get_impact`: Graphify symbol dependency and blast radius query.
- `goa_refresh_graph`: AST knowledge graph update.
- `goa_record_memory` / `goa_get_memory`: Project memory management.
- `goa_get_status`: Session diagnostic inspection.

---

## 12. Benchmark Results (Phase 8)

The benchmark evaluated **Solo vs ECC vs GOA Orchestrator** on 24 live completed runs across Backend, Frontend, and Database domains on fresh sandboxes:

| Metric | Solo (Bare Agent) | ECC (Checklist Prompt) | GOA Orchestrator (Adaptive) |
|---|:---:|:---:|:---:|
| **Clean Sweep (Tests + Lint + Types Pass)** | 0.0% (0/8) | 0.0% (0/8) | **75.0% (6/8)** |
| **Unit Tests Pass Rate** | 75.0% (6/8) | **87.5% (7/8)** | **87.5% (7/8)** |
| **Lint Pass Rate (Ruff/ESLint)** | 0.0% (0/8) | 25.0% (2/8) | **75.0% (6/8)** |
| **Typecheck Pass Rate (Mypy/TSC)** | 62.5% (5/8) | 37.5% (3/8) | **75.0% (6/8)** |
| **Composite Quality Score (0–10)** | 6.50 ± 4.07 | 7.12 ± 3.00 | **8.38 ± 3.54** |
| **Mean Duration (s)** | **115.7s** | 549.0s (Median 131s) | 136.4s (Median 129s) |
| **Average Guided Repair Retries** | 0.00 | 0.00 | **0.88** |

**Conclusion:** GOA outperformed Solo and ECC on multi-gate quality (75% clean sweep vs 0%) by enforcing closed-loop mechanical verification, while bounding retries to avoid ECC's unconstrained 3600s loop.

---

## 13. Remaining Weaknesses (Honest Appraisal)

1. **AST Graph Granularity:** Graphify operates primarily at import and module symbol levels; dynamic dispatch and cross-language runtime dependencies are not modeled.
2. **Auto Graph Refresh Cost:** Automatic graph updating after GREEN verification is disabled by default (`GOA_AUTO_GRAPH_REFRESH=0`) to save CPU time on large repositories.
3. **Session Resumption State:** Multi-turn MCP sessions store state in-memory and in `.goa_memory.json`; server process restarts clear active in-memory session dictionaries (persisted disk records remain).
4. **Toolchain Detection Scope:** Supports standard Python (`uv`, `pip`), Node (`npm`), Go, and Rust. Custom legacy build scripts (e.g., custom Makefiles) require explicit task instructions.

---

## 14. Recommended Next Steps

1. **Incremental Graphify Invalidation:** Implement selective AST graph updates on changed files rather than whole-repo re-indexing.
2. **Persistent MCP Session Cache:** Persist active session records to SQLite or disk JSON so server reboots do not reset in-flight session history.
3. **Language Server Protocol (LSP) Integration:** Bridge LSP symbol definitions and diagnostics into the impact analyzer alongside Graphify.
4. **CI/CD Action / Pre-commit Integration:** Package `goa_cli verify` as a GitHub Action and pre-commit hook to gate code merges on GOA verification records.
