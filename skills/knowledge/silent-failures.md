---
description: Detection guide for silent failures — swallowed exceptions, masked defaults, ignored errors, and log-and-continue antipatterns
tags: errors,review,reliability,verification
---

# Silent Failure Detection Guide

Silent failures are errors that occur but never surface: they are swallowed,
masked with defaults, or logged and ignored. They are among the highest-severity
defects because the system appears healthy while producing wrong results, losing
data, or corrupting state — and the failure surfaces far from its cause. This
guide gives the taxonomy, detection patterns, and severity rationale.

## When to Reference

- Reviewing error handling in new or modified code
- Investigating a bug that "shouldn't be possible" or manifests downstream
- Auditing async, network, file, or DB call sites for missing failure paths
- Designing retries, fallbacks, or degradation behavior

## Taxonomy of Silent Failures

### 1. Swallowed Exceptions

Empty or over-broad catch blocks that discard the error and continue.

```python
# BAD: failure vanishes; caller believes save succeeded
try:
    save_report(report)
except Exception:
    pass

# GOOD: handle specifically, with context; or let it propagate
try:
    save_report(report)
except SaveError as exc:
    logger.exception("report save failed for job %s", job_id)
    raise ReportSaveError(job_id) from exc  # preserve stack trace
```

Bare `except Exception` (worse: bare `except:`) around real logic is a red flag
by default. Catch the narrowest type that can actually be handled.

### 2. Default-Value Masking

Substituting a plausible default when real data retrieval fails, so downstream
code computes confidently wrong answers.

```python
# BAD: retry failure is indistinguishable from "no settings"
settings: dict = fetch_settings() or DEFAULTS

# GOOD: fail loudly; apply defaults only where absence is a valid state
settings: dict = fetch_settings()  # raises on failure
```

Ask of every fallback: "is this default distinguishable from real data, and is
that ambiguity intentional?" If not, the default is masking.

### 3. Empty-Result Laundering

Turning an error into an empty list/dict/None so the caller can't tell
"nothing found" from "the lookup blew up".

```python
def load_users(user_ids: list[int]) -> list[User]:
    # BAD: DB outage looks identical to an empty org
    try:
        return db.fetch_users(user_ids)
    except DBError:
        return []

    # GOOD: propagate, or return an explicit result type
    # -> raises DBError, or returns Result[list[User], DBError]
```

Empty results from a lookup are only acceptable when empty is a legitimate
domain answer — not when they hide a transport, permission, or query failure.

### 4. Logging Without Acting (Log-and-Forget)

Logging the error and continuing as if success — the log is not a failure path.

```python
# BAD: job reports success despite partial failure
for task in tasks:
    try:
        run(task)
    except TaskError as exc:
        logger.error("task %s failed: %s", task.id, exc)
return JobResult(status="success")

# GOOD: aggregate and reflect the real outcome
failures: list[TaskError] = []
for task in tasks:
    try:
        run(task)
    except TaskError as exc:
        logger.exception("task %s failed", task.id)
        failures.append(exc)
if failures:
    return JobResult(status="partial_failure", errors=failures)
```

### 5. Error Propagation Breakage

- Lost stack traces: `raise NewError(...)` without `from exc`
- Generic rethrows that erase type information
- Missing `await` / unawaited coroutines — errors vanish into abandoned tasks
- Fire-and-forget calls with no error callback

```python
# BAD: original context discarded
except TimeoutError:
    raise JobFailed("job timed out")

# GOOD: chain the cause
except TimeoutError as exc:
    raise JobFailed(f"job {job_id} timed out") from exc
```

### 6. Missing Error Handling Entirely

No timeout on network/file/DB calls, no rollback around transactional work,
no handling around partial batch failures. Absence of a handler is not absence
of failure — it's absence of control over the failure.

## Detection Patterns

- Grep for `except: pass`, `except Exception: pass`, `pass` inside except blocks
- Grep for `or []`, `or {}`, `or ""`, `or None` around fallible calls
- Review every `try` block: is each caught exception handled, re-raised, or
  reported — and can the caller distinguish failure from success?
- Check async call sites for unawaited coroutines and unobserved task results
- Check every external call (HTTP, DB, file, subprocess) for a timeout
- For every `logger.error/warning`, verify something acts on it (return, raise,
  retry, alert) — logging alone is not handling
- Verify transactional code paths have rollback/compensation on failure

## Why High Severity

- Failures surface far from the cause, making diagnosis expensive
- They corrupt data and results while dashboards stay green
- They erode trust: any output could be built on masked failures
- They compound — one masked failure poisons every downstream consumer
- They defeat monitoring, because nothing is measuring what never surfaces

The rule of thumb: **an error must either be handled (with the outcome visible
in the result) or propagated (with context). There is no third option.**
