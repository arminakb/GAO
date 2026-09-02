---
description: Eval-driven development for GAO — baselines, eval design and sample size, harness construction, and regression-detection gates
tags: evaluation, benchmarking, testing, agents, methodology
origin: ECC (enriched)
---

# Evaluation & Benchmark Methodology

Systematic evaluation turns tuning an agent orchestrator from guesswork into
measurement: define metrics and pass criteria **before** changing anything,
establish a baseline, change one variable at a time, and gate every change on
regression checks. In GAO this applies to anything you tune — token budgets,
retry limits, prompt templates, model routing, graph topology.

## When to Reference

- Tuning orchestrator parameters (token budgets, retry limits, timeouts, k in retry loops)
- Deciding whether a prompt, model, or graph-structure change actually helped
- Building a regression suite for agent behavior before a refactor
- Designing eval tasks: how many runs, which seeds, what statistics to trust
- Constructing or evolving the eval harness itself
- Comparing runs across model versions or config revisions

## Eval-Driven Development

Treat evals as the unit tests of orchestrator development:

1. **Define expected behavior first** — write the eval and its pass criteria
   before implementing the change. This forces clear success criteria.
2. **Run evals continuously** during development, not just at the end.
3. **Track regressions with every change** — a change that improves one metric
   while degrading another is not free. Always anchor regressions to a named
   baseline (config hash, git SHA, checkpoint). Capability evals ask "can the
   system do something new?"; regression evals ask "does existing behavior
   survive the change unchanged?"
4. **Version evals with code** — evals are first-class artifacts in the repo.

Two eval kinds, kept separate:

| Kind | Question | Artifact shape |
|---|---|---|
| Capability | Can the system do something new? | Task description + Success Criteria checklist + expected output |
| Regression | Does old behavior survive? | Baseline SHA/checkpoint + named existing tests + PASS/FAIL vs previous X/Y |

## Baseline-First Measurement

Never compare a change against your memory of past performance. Pick the
workload(s) and fixed task set the change is supposed to affect, then:

- Run the **current** configuration on that set and record results — that run
  is the baseline. Then run the modified configuration on the **same** task set
  under the **same** conditions (same model, same seeds/temperature where
  possible, same time window for anything rate-dependent) and compare against
  the recorded baseline, not against intuition.

```python
import json, statistics
from pathlib import Path

def summarize_run(run_log: Path) -> dict:
    rows = [json.loads(line) for line in run_log.read_text().splitlines()]
    return {
        "n": len(rows),
        "pass_rate": sum(r["success"] for r in rows) / len(rows),
        "mean_tokens": statistics.mean(r["total_tokens"] for r in rows),
        "mean_latency_s": statistics.mean(r["latency_s"] for r in rows),
    }
```

Store baselines in a file (`baselines.json`), keyed by config hash, and keep
run history per eval so trends are visible.

## Eval Design: n, Seeds, and Statistical Honesty

LLM outputs are stochastic; design the run plan before believing any delta:

- **Sample size**: for a binary pass metric, ~30+ task runs per arm gives a
  rough signal; below that, require a proportionally larger delta before
  believing it. Small-N wins are noise until shown otherwise.
- **Seeds/temperature**: fix decoding parameters (temperature, seed) where the
  provider allows; where it doesn't, run more trials instead. Record the
  sampling config with every result.
- **Distributions over means**: check p95 latency and tail failures, not just
  the mean — a change that lowers the mean but explodes the tail is a net loss.
- **Constant conditions across arms**: same model version, same load, same time
  window for rate-limited APIs — otherwise you measure the confound.
- **Held-out set**: if prompts are tuned against the exact eval tasks, scores
  stop predicting real performance. Keep a held-out task set and rotate tasks
  occasionally.
- **Honest claims**: say "best measured variant under budget", never "optimal",
  unless the search space was actually exhaustive.

## Single-Variable Changes

- Change **one thing per experiment**: one budget value, one retry limit, one
  prompt sentence. Multi-variable changes make the result unattributable.
- If you must explore several options, run them as separate arms against the
  same baseline, not stacked. Record the exact config diff with each run result
  so the experiment is reproducible later.
- Beware interaction effects: a setting that helped alone may hurt after the
  next change — re-verify important gains periodically.

## Metric Selection

Choose metrics per concern; no single number captures orchestrator quality:

- **Reliability** — pass@k: at least one success in k attempts. `pass@1` is
  direct reliability; `pass@3` is practical reliability under bounded retries.
- **Stability** — pass^k: all k runs must succeed. Suggested gates: capability
  evals `pass@3 >= 0.90`; release-critical paths `pass^3 = 1.00`.
- **Cost** — mean tokens per task, per successful task, and p95 tokens.
- **Latency** — mean and p95 wall-clock per task.
- **Efficiency** — attempts per success (retry-limit effectiveness), loop
  abort/stall rate.

Pick the primary metric for the change under test (a token-budget change is
judged on cost and pass-rate, not latency), but always also record the others
to catch collateral damage. Report metrics separately — no single blended score.

```python
def pass_at_k(outcomes: list[list[bool]], k: int) -> float:
    """Fraction of tasks with at least one success in the first k attempts."""
    return sum(any(arm[:k]) for arm in outcomes) / len(outcomes)
```

## Harness Construction

The harness is code in the repo, versioned with the evals it runs:

- **Isolated, reproducible runs**: each task runs in a fresh sandbox with the
  same spec, pinned dependencies, and recorded config hash; results persist to
  a run ledger (one JSON line per task run) rather than chat scrollback.
- **Task files as data**: eval definitions live as files (`evals/<feature>.md`
  or `.yaml`) with task, success criteria, and grader declared — not embedded
  in scripts. Run history (`<feature>.log`) and `baselines.json` sit beside
  them; a release snapshot lands in release docs.
- **Grader selection — cheapest sufficient**: code grader (deterministic
  assertions: exit codes, schemas, content checks) is the default and the only
  kind allowed in gates where flakiness is unacceptable; rule grader
  (regex/schema constraints) next; model grader (LLM-as-judge, fixed rubric +
  score scale) for open-ended quality; human grader for ambiguous or
  security-relevant adjudication — never fully automate security sign-off.
- **Keep evals fast** — slow evals don't get run; parallelize task execution
  and keep the per-task harness overhead tiny relative to the workload.
- **Same input shape for all arms**: identical prompts/contexts modulo the one
  variable under test, so deltas are attributable.

## Regression-Detection Loop

1. Before the change: capture baseline on the fixed task set; register it in
   `baselines.json`.
2. After the change: re-run the same set under the same conditions; diff
   against the baseline.
3. Gate: the change ships only when the gate suite passes —

```python
def gate(results: dict[str, float]) -> bool:
    return (
        results["regression_pass3"] == 1.0
        and results["capability_pass3"] >= 0.90
        and results["token_cost_ratio"] <= 1.10  # <=10% cost regression tolerated
    )
```

   Cost and latency regressions must be explicit, bounded, and accepted — never
   discovered later (anti-pattern: chasing pass-rate gains while token cost
   silently doubles).
4. When a regression slips through: the failing case becomes a new permanent
   regression eval — every escaped defect hardens the suite.
5. Re-verify important gains periodically (interaction effects).

## Eval Storage & Reporting

Every report leads with the decision, not the drama: what changed, which
metric moved, did the gate pass, what is the next experiment. Example shape:
`Retry limit 2→3: pass@3 0.82→0.94, tokens/task +18%. Gate: PASS. Next: test
retry limit 2 with prompt fix instead.` Keep evals fast — slow evals don't run.

## Anti-Patterns (NEVER)

- Tuning without a recorded baseline; stacked multi-variable changes presented
  as one improvement; small-N wins celebrated as real; ignoring p95 tails.
- Single blended score hiding a cost/reliability trade-off; happy-path-only
  measurement; overfitting prompts to the eval set.
- Flaky or self-graded checks used as release gates.
- Security-relevant gates delegated to a model grader.
- Letting an escaped regression pass without adding it to the suite.
