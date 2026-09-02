# Agent Model

Two catalogs exist, one per mode. Rule for both (TASK.md §22): every agent
must have a purpose, capabilities, activation conditions, input/output
contracts, and expected value. No agent exists because it exists.

## External Agent Mode (PRIMARY)

There are **no LLM agents to run** — the coding agent (Claude Code / Codex /
OpenCode) executes. GOA's "agents" here are deterministic services:

| Service | Activation | Capability boundary |
|---|---|---|
| TaskAnalyzer | every task | read task text + repo manifests; no writes |
| Planner (`build_plan`) | after analysis | pure function: analysis → plan |
| VerificationEngine | after implementation | shell exec (verification commands only) |
| RecoveryRouter | on RED verdict | read evidence; recommend resolver |
| ReviewPacket (`goa_review`) | before approval | read git diff + evidence |
| Memory | on demand | read/write `.goa_memory.json` |
| Graphify | on demand / after changes | read graph.json; `graphify update` |

## Native Mode (SECONDARY)

LangGraph agents (`agents/*.md`, one file each, structure enforced by drift
tests):

| Agent | Responsibility | Tools (binding matrix) |
|---|---|---|
| orchestrator | route only, never implement | none (structured RoutingDecision) |
| planner | decompose into tasks | knowledge, architecture queries |
| architect | system design | knowledge, impact paths, communities |
| coder | implement | knowledge, symbols + toolkit (gated) |
| tdd_guide | tests-first discipline | knowledge + toolkit (gated) |
| reviewer | evidence-based approve/reject | knowledge + toolkit read-only |
| build_error_resolver | build/dependency repair | knowledge + toolkit (gated) |
| e2e_runner | end-to-end exercise | exec only |
| refactor_cleaner | cleanup | architecture queries + destructive-capable toolkit |
| doc_updater | docs | knowledge + write (docs only) |
| database_reviewer / security_reviewer | domain review | read-only |

Removed-in-spirit agents (loop_operator, harness_optimizer) remain for
backward compatibility of the native graph but are passthrough by default —
they earn their place only if telemetry shows value (TASK.md §22).

## Capability enforcement

`harness/toolkit.py::ToolGate` binds tools per role:

- READ: read_file, list_directory, search_files, git_status/diff/log/show
- WRITE: write_file, edit_file
- EXECUTE: run_command
- DESTRUCTIVE: delete_file

Reviewer = READ + EXECUTE (run tests, never modify). Coder = READ + WRITE +
EXECUTE. Refactor_cleaner additionally DESTRUCTIVE. Policy overrides live in
`policies.json → access_rules.capabilities`. Enforcement is in code; a prompt
cannot grant a capability an agent does not hold.
