# Cost Optimization

The core economic claim of GOA: **orchestration is a cost, and it must pay for
itself.** Fixed multi-agent pipelines pay maximum orchestration cost on every
task, including the trivial ones that dominate real usage.

## Model

| Track | Steps | GOA LLM calls (external mode) | Typical token envelope |
|---|---|---|---|
| solo (TRIVIAL/SIMPLE) | implement → verify | 0 | ≤30k |
| medium | plan → implement → verify → review | small | ≤80k |
| complex/critical | plan → (architect) → specialists → implement → verify → review → repair → final-verify | bounded | ≤150–250k |

Budgets (`OrchestrationBudget`: max_tokens, max_llm_calls, max_wall_time_s,
max_retries, max_cost_usd) are attached to every plan and enforced in code.

## Where the savings come from

1. **Deterministic-first analysis.** The classifier is regex/heuristic-based;
   an LLM is consulted only when confidence is low, and then only to nudge the
   class by one step. Most tasks: zero analysis LLM calls.
2. **Solo track skips the orchestrator LLM entirely.** A trivial task pays 0
   routing calls in external mode (the coding agent just implements and calls
   `goa_verify`).
3. **No redundant verification claims.** A failed verification is classified
   deterministically and routed once to the matching resolver — no
   discuss-the-failure rounds.
4. **Bounded retries + escalation.** 3 failed cycles stop the loop instead of
   burning tokens on a stuck task.
5. **Selective context.** Memory retrieval is top-k by relevance; agents get
   verification excerpts, not full logs; the reviewer gets the diff + evidence
   packet, not the whole history.
6. **Graphify targets review depth.** Only high-blast-radius changes get
   deeper review; local changes don't pay for it.

## What we measure

Per session (`goa_get_status`): task, track chosen, verification runs,
verdicts, retries, changed files. Per benchmark arm: total tokens, cost,
wall-clock, LLM calls, tool calls, retries (TASK.md §38). Caveman-style
compression applies to context *construction* — keep raw evidence in the
record, send the minimum sufficient context to each consumer.

## Honest accounting

Native mode still pays per-agent LLM calls; its economics are governed by
`policies.json` token limits and the budget guard. The external-mode numbers
above reflect GOA's own overhead only — the coding agent's tokens are the
user's normal cost and are measured in the benchmark, not hidden.
