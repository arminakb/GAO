---
description: Verification and convergence loops — machine-checkable completion evidence, stop conditions, and anti-drift rules for agent work
tags: verification,agents,evidence,quality
---

# Verification & Convergence Loops

Verification loops keep autonomous agent work honest: an agent may only claim a
task complete when machine-checkable evidence says so. In GAO's multi-agent
LangGraph setting, a loop-operator node monitors each worker agent's outputs
against explicit completion criteria, detects stalls, and decides whether the
loop should continue, stop, or escalate to a human.

## When to Reference

- Writing loop-operator / supervisor node logic that gates agent completion claims
- Defining what "done" means for a delegated subtask (evidence, not vibes)
- Detecting stuck loops: repeated identical failures, oscillation, no progress
- Deciding between retry, stop-with-partial-result, and human escalation
- Running periodic verification sweeps during long-running graph executions

## Verify Before Claiming

Never report success without running the check that would prove it. The claim
follows the evidence, never the other way around.

For a Python project the gate sequence is:

1. **Build/import check** — the package imports and entry points load.
2. **Type check** — `pyright` or `mypy` clean on touched files.
3. **Lint** — `ruff check .`
4. **Tests** — targeted tests pass; report counts, not adjectives.
5. **Diff review** — inspect what actually changed for unintended edits.

Each phase runs only if the previous one passed; a failed phase stops the loop.

```python
from pydantic import BaseModel

class VerificationReport(BaseModel):
    imports: bool
    types: bool
    lint: bool
    tests_passed: int
    tests_total: int
    overall: bool  # all gates green
```

The loop-operator consumes this report, not the worker's prose summary.

## Evidence-Based Completion Criteria

Completion criteria must be defined **before** the work starts, and must be
machine-evaluable:

- A capability is done when a named check passes (test id, command exit code,
  schema validation of the output object).
- "Looks right", "should work", and "I believe it's done" are not evidence.
- Store criteria on the task object so the verifier is independent of the
  worker: the worker cannot grade its own homework.

```python
class CompletionCriteria(BaseModel):
    test_ids: list[str]          # pytest node ids that must pass
    commands: list[str]          # shell commands that must exit 0
    output_schema: type | None   # Pydantic model the result must validate against
```

## Decision Ledger for Repeated Rollouts

When a loop runs repeated attempts or rollouts (retries, search, ensembles),
append a ledger entry per rollout so convergence is auditable:

- rollout id, timestamp, prior accepted winner, fresh information ingested
- trial count, top candidates, decision mark (`accept | watch | reject | replay`)
- coherence mark against the prior ledger (did this rollout contradict the last?)
- promotion gate result

Rules:

- Append entries before summarizing (JSONL for the ledger, Markdown for humans).
- Downgrade a candidate when drift, stale data, or a failed replay invalidates
  its previous mark.
- Repeated confidence is not approval: destructive or side-effecting actions
  (deploys, migrations, writes outside the workspace) still require an explicit
  gate pass and, where applicable, human sign-off. Default to dry-run/read-only
  until every gate is satisfied.

## Stall Detection

The loop-operator watches for these patterns and treats each as a stall signal:

- **Identical failure repeats** — same error message/exception N times with no
  state change between attempts.
- **Oscillation** — the loop flips between two states (fix A breaks B, fix B
  breaks A) without net progress.
- **No-diff attempts** — an attempt produced no change to any artifact.
- **Metric plateau** — the health metric (tests passing, coverage, error count)
  has not improved over the last K attempts.
- **Budget exhaustion** — attempt count, wall-clock, or token budget exceeded
  before convergence.

```python
def is_stalled(history: list[VerificationReport], window: int = 3) -> bool:
    if len(history) < window:
        return False
    recent = history[-window:]
    return len({(r.types, r.lint, r.tests_passed, r.tests_total) for r in recent}) == 1
```

## Stop vs Escalate

- **Continue** — the metric improved on the last attempt and budget remains.
- **Stop with partial result** — stall detected or budget exhausted, but the
  current state is safe and self-consistent. Return what was verified plus an
  explicit list of unmet criteria. Never paper over with an optimistic summary.
- **Escalate to human** — required when: the same fix fails twice after a fresh
  approach; the blocker is outside the agent's permissions (credentials,
  external services, destructive operations); completion criteria themselves
  turn out to be wrong or contradictory; or a security issue is discovered
  (stop first, rotate/flag, then escalate).

## Continuous Verification

For long sessions, verify periodically rather than only at the end:

- After each completed unit of work (function, node, subtask), run the cheap
  gates (import, lint, targeted tests).
- Run the full gate sequence before declaring the overall task complete or
  opening a PR.
- Hooks/guards catch issues immediately at edit time; the verification loop is
  the comprehensive backstop — both are needed.

## Anti-Patterns

- Claiming completion from a worker's self-report without running the gates.
- Fuzzy criteria ("works correctly") that no verifier can evaluate.
- Retrying identically after a deterministic failure — change the approach or
  escalate.
- Letting a loop run past its budget "just in case" — bounded loops only.
- Treating a green gate suite as approval for irreversible actions.
