---
description: Measure-first performance method — baseline loops, profiling, common hotspots, caching discipline, and latency budgets for tail latency
tags: performance, profiling, benchmarking, latency, optimization
origin: ECC (enriched)
---

# Performance Optimization Method

Performance work is an empirical discipline: measure first, optimize the verified
bottleneck, then re-measure to confirm the win. Most "optimizations" applied without
profiling add complexity and change nothing, so this method enforces evidence at
every step. Optimize for the tail (p95/p99), not the average.

## When to Reference

- Users report slowness or metrics regress after a change
- Asked to "make X faster" or to try multiple optimization variants
- Reviewing loops, queries, or network calls for efficiency
- Setting or enforcing performance budgets (bundle size, latency, memory)
- Diagnosing memory growth or suspected leaks in latency-sensitive paths

## Measure First — Always

Never optimize without a profile. Before touching code, a bounded optimization
loop requires: the operation, the correctness gate that must stay green, the
metric (wall time, p95, rows/sec, cost/run, memory), the current baseline, and
a search budget (max variants, max time, max spend).

```bash
# Python: cProfile a slow function
python -m cProfile -s cumulative -m mymodule
python -m py_spy record --pid <PID> -o profile.svg  # sampling for live services

# Node/JS equivalents
node --prof app.js && node --prof-process isolate-*.log
npx lighthouse https://app.example.com --view
```

```python
# Targeted in-process timing
import time
from contextlib import contextmanager

@contextmanager
def timed(label: str, results: dict) -> None:
    start: float = time.perf_counter()
    try:
        yield
    finally:
        results[label] = time.perf_counter() - start
```

If the profile doesn't show the hotspot you expected, your hypothesis is wrong —
do not proceed with the "fix".

## Profiling Workflow

1. Reproduce with a realistic workload (synthetic micro-benchmarks mislead).
2. Capture a baseline profile and record the key metric(s).
3. Identify the top hotspots by cumulative time/allocation — usually 1–2 dominate.
4. Fix one hotspot; keep the change minimal and testable.
5. Re-profile. Keep the win only if the metric improved measurably; revert otherwise.
6. Add a regression guard (perf budget in CI, or a timing assertion).

### Variant Table (multi-variant work)

Track every attempt so wins are attributable and losers stay rejected:

```text
Variant    | Hypothesis         | Command              | Time | Correct? | Notes
baseline   | current path       | npm run job          | 120s | yes      | stable
batch-500  | fewer round trips  | npm run job -- -b500 |  42s | yes      | winner
parallel-8 | more workers       | npm run job -- -w8   |  31s | no       | rate limited
```

Rules: one hypothesis per variant, same input shape for all runs, reject
variants failing correctness/safety/reproducibility, compare against the prior
accepted winner (not just the previous run), and stop when improvement is within
noise or the budget is spent. Say "best measured safe variant", never "optimal".
A variant is promoted only when: tests pass, the delta repeats, rollback is
obvious, and the winning config is encoded in source control or a runbook.

## Common Hotspots

**N+1 queries** — one query per item in a loop. Batch or join instead.

```python
# BAD: 1 query for users, then N queries for orders
for user in session.query(User).all():
    orders: list[Order] = user.orders  # lazy-loads per user

# GOOD: single eager-loaded query
users: list[User] = session.query(User).options(
    joinedload(User.orders)
).all()
```

**Chatty I/O** — sequential network calls that could be parallel or batched.

```python
import asyncio

# BAD: three sequential round trips
user: User = await fetch_user(uid)
posts: list[Post] = await fetch_posts(uid)

# GOOD: parallel when independent
user, posts = await asyncio.gather(fetch_user(uid), fetch_posts(uid))
```

**Unnecessary copies / repeated work in loops** — sort once, look up with a map,
avoid deep clones and string concatenation in loops.

```python
from collections import defaultdict

# BAD: O(n^2) — filtering the whole list per user
matched: list[Order] = [o for o in all_orders if o.user_id == user.id]

# GOOD: O(n) — group once, O(1) lookups
orders_by_user: dict[int, list[Order]] = defaultdict(list)
for order in all_orders:
    orders_by_user[order.user_id].append(order)
```

Also: `SELECT *` in production, missing indexes on filtered/joined columns,
missing pagination on large result sets, no connection pooling, and recursion
without memoization.

## Latency-Critical Paths

When freshness and p95 matter (streaming, queues, dashboards), split the
metrics — never collapse everything into "fast": p50/p95/p99 latency,
throughput, freshness age, queue depth, cache hit rate, and failure/retry
behavior each get their own number.

Map the hot path end to end and measure each segment separately:

```text
source event -> provider API -> ingest worker -> queue -> cache -> edge route
-> client stream -> render -> user-visible state
```

Optimization order: remove unnecessary round trips → cache stable reads (with
freshness metadata) → batch small calls → move compute closer to data → split
hot/cold paths → apply backpressure before queues grow unbounded → stream only
when it improves freshness → add canaries for stale data and degraded
providers. Guardrails: never drop required validation for speed, never hide
stale data behind fast cache hits, and verify with live readbacks (HTTP timing,
provider freshness, queue state) before calling the path ready.

## Caching Discipline

Cache only what profiling shows is hot, and always with a defined invalidation
policy. An unbounded or never-invalidated cache is a correctness bug and a
memory leak waiting to happen.

```python
from functools import lru_cache

@lru_cache(maxsize=256)
def get_user_profile(user_id: int) -> dict[str, str]:
    return fetch_profile_from_db(user_id)  # TTL handled by cache-size + explicit clears
```

Rules: give every cache a TTL or explicit invalidation; include all identity
factors in the key; measure hit rate; don't cache rarely-hit or easily-computed
values; never cache user-specific data under a shared key.

## Memory Leaks

Classic patterns: listeners/timers/callbacks registered without cleanup, closures
holding large objects, unbounded caches and logs. Detect by taking heap snapshots
under load and diffing: growth that never plateaus indicates a leak.

## Avoid Premature Optimization

- Do not optimize code without a profile showing it is hot.
- Prefer clarity first; most code is never a bottleneck.
- Fix algorithms (complexity) before micro-tuning implementations — a Map/Set
  beats a nested loop by orders of magnitude, whereas micro-optimizations rarely do.
- Keep a performance budget in CI so wins are protected from regressions.
- Every optimization must be justified by a before/after measurement.

## Report Format

For each finding: location (file:line), the measured impact (ms, MB, %), the
evidence (profile excerpt), the fix, and the re-measured result. Unverified
improvements are claims, not results. For eval-style verification of behavior
changes, pair this with `eval-benchmark-methodology.md`; for CI regression
gates see `verification-loop.md`.

## Anti-Patterns (NEVER)

- Optimizing without a recorded baseline or profile.
- Claiming millisecond behavior from client labels without measurement.
- An unbounded cache "just for now" — no TTL, no invalidation, no hit-rate metric.
- Stacked multi-variable tuning presented as one improvement.
- Dropping validation or hiding staleness to buy latency.
- Chasing averages while p99 tail failures silently worsen.
