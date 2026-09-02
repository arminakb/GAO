# Memory

GOA keeps project-level engineering memory so later tasks benefit from earlier
ones (TASK.md §18, §24).

## What is stored

| kind | example |
|---|---|
| architecture_decision | "reservation race guard lives in service layer, not DB" |
| recurring_bug | "parser crashes on empty input — regression-tested since" |
| failed_approach | "pinned global lock deadlocked under load" |
| successful_fix | "atomic UPDATE ... WHERE copies>0 fixed the race" |
| convention | "ruff for lint, uv for envs" |
| verification_failure | "mypy strict fails in generated sandboxes" |
| task_outcome | "LRS task: GREEN in 2 verify cycles, solo track" |

## Storage

`.goa_memory.json` in the workspace (path via `GOA_MEMORY_PATH`). Entries:
`{kind, content, tags, ts, session}`. Plain JSON — inspectable, portable,
no database.

## Retrieval is selective

`goa_get_memory(query, limit=5)` scores entries by term overlap across kind +
content + tags and returns **only top-k matches**. Nothing dumps the whole
memory into a context. Zero matches → empty list.

## Feeding decisions

- `TaskAnalyzer` consumes `historical_failure_rate` (fraction of recent
  verification runs that failed) — a repo that keeps failing verification
  gets its next task bumped one complexity class, i.e. more orchestration
  where history says it is needed (TASK.md §24).
- Agents (native mode) may record reasoning outcomes through the existing
  `record_agent_memory` Graphify tool; decisions + symbols + outcome
  (useful / dead_end / corrected) persist in the learning overlay.

## Not implemented (honesty note)

Cross-project memory federation and automatic policy rewriting from
statistics are deliberately out of scope: adaptivity flows only through
explicit, measurable signals (failure rate, verification verdicts), never
uncontrolled self-modification (TASK.md §24/§31).
