---
description: Typed error hierarchies, retries with backoff, circuit breakers, and user-facing error messages for production services.
tags: errors, retry, resilience, api
origin: ECC
---

# Error Handling Patterns

Robust error handling for production applications: typed errors as API
surface, deliberate retry policy, and a hard separation between user-facing
messages and server-side diagnostics. Pairs with `fastapi-patterns` for the
framework-side handler wiring.

## When to Reference

- Designing error types or exception hierarchies for a new module or service
- Adding retry logic or circuit breakers for unreliable external dependencies
- Reviewing API endpoints for missing error handling
- Debugging cascading failures or silent error swallowing

## Core Principles

1. **Fail fast and loudly** — surface errors at the boundary where they occur; do not bury them.
2. **Typed errors over string messages** — errors are first-class values with structure (`code`, `status_code`, optional `details`).
3. **User messages ≠ developer messages** — friendly text to users, full context logged server-side.
4. **Never swallow errors silently** — every `except` block must handle, re-raise, or log (see `silent-failures`).
5. **Errors are part of the API contract** — document every error code a client may receive, and emit them in the project's stable envelope `{ "error": { "code", "message" } }`.

## Typed Exception Hierarchy (Python)

```python
class AppError(Exception):
    """Base application error carrying a stable machine-readable code."""
    def __init__(self, message: str, code: str, status_code: int = 500):
        super().__init__(message)
        self.code = code
        self.status_code = status_code

class NotFoundError(AppError):
    def __init__(self, resource: str, id: str):
        super().__init__(f"{resource} not found: {id}", "NOT_FOUND", 404)

class ValidationError(AppError):
    def __init__(self, message: str, details: list[dict] | None = None):
        super().__init__(message, "VALIDATION_ERROR", 422)
        self.details = details or []
```

## Retry with Exponential Backoff

Retry transient failures only — never 4xx client errors:

```python
import random, time
from collections.abc import Callable

def with_retry(fn: Callable[[], object], max_attempts: int = 3,
               base_delay: float = 0.5, max_delay: float = 10.0) -> object:
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as error:  # narrow `retry_if` in real code
            last_error = error
            if attempt == max_attempts or _is_client_error(error):
                raise
            delay = min(base_delay * 2 ** (attempt - 1) + random.random() * base_delay, max_delay)
            time.sleep(delay)
    raise last_error  # pragma: no cover
```

Properties that matter:
- **Jitter** prevents synchronized retry storms across callers.
- **Cap the delay** — exponential growth without a cap stalls callers.
- **Retry predicate** distinguishes transient (5xx, timeouts, connection resets) from permanent (4xx, domain validation) failures. Retrying a `409 CONFLICT` converts a clean failure into a latency bug.

## User-Facing Error Messages

Map stable codes to human-readable text; keep technical detail server-side:

```python
USER_ERROR_MESSAGES: dict[str, str] = {
    "NOT_FOUND": "The requested item could not be found.",
    "VALIDATION_ERROR": "Please check your input and try again.",
    "RATE_LIMITED": "Too many requests. Please wait a moment and try again.",
    "INTERNAL_ERROR": "Something went wrong on our end. Please try again later.",
}

def get_user_message(code: str) -> str:
    return USER_ERROR_MESSAGES.get(code, USER_ERROR_MESSAGES["INTERNAL_ERROR"])
```

## Checklist

- [ ] Every `except` block handles, re-raises, or logs — no silent swallowing
- [ ] API errors follow the stable envelope `{ error: { code, message } }`
- [ ] User-facing messages contain no stack traces or internal details
- [ ] Full error context is logged server-side
- [ ] Custom errors extend a base `AppError` with a `code` field
- [ ] Async tasks surface errors to callers — no fire-and-forget without fallback
- [ ] Retry logic retries only retriable errors (never 4xx)
