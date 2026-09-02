---
description: Service-layer, repository, DTO, middleware, caching, and background-job patterns for GOA backend services
tags: backend,architecture,repository,dto,caching,queues
origin: ECC (enriched)
---

# Backend Architecture Patterns

Structural patterns for maintainable backend services: keep business logic
independent of storage and transport via repository and hexagonal boundaries,
prefer immutability, and treat errors as a designed part of the system.
API contracts live in api-design.md; error taxonomy in error-handling.md.

## When to Reference

- Designing or reviewing service-layer and data-access structure
- Deciding where validation, error translation, and retries belong
- Shaping DTOs at a transport or storage boundary
- Adding caching (Redis), background jobs, or a new external dependency
- Reviewing error handling for silent failures or lost context

## Layering & Dependency Rule

```
handler (transport DTOs)  →  service (domain)  →  repository (port)  →  DB adapter
```

- Dependencies point **inward only**: the core defines protocols; HTTP
  handlers, DB clients, and queues implement them.
- A new integration touches an adapter, not the core — that is the test of a
  healthy boundary. Core modules must have no infra imports.

## Service Layer

Business logic lives in services, not handlers and not repositories:

```python
class OrderService:
    def __init__(self, orders: OrderRepository, payments: PaymentGateway) -> None: ...

    async def place_order(self, cmd: PlaceOrder) -> Order:
        order = Order.create(cmd)              # domain rules here
        await self.payments.authorize(order)   # orchestration here
        await self.orders.save(order)          # persistence via port
        return order
```

- Services own orchestration, invariants, and transactions; handlers own
  parsing/serialization; repositories own queries only.
- One service method = one use case = one transaction boundary.

## Repository Pattern

Encapsulate all data access behind a protocol; business logic never imports
an ORM or DB driver:

```python
class UserRepository(Protocol):
    def find_by_id(self, user_id: str) -> User | None: ...
    def save(self, user: User) -> None: ...

class PostgresUserRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None: ...

# Service depends on the protocol → in-memory fakes in tests.
class UserService:
    def __init__(self, repo: UserRepository) -> None: ...
```

- Standard operations: `find_all`, `find_by_id`, `create`, `update`, `delete`;
  plus intent-named queries (`find_active_by_tenant`) over generic flag params.
- Repositories return domain objects, never ORM rows or raw dicts.
- BAD: `find_all(filters: dict)` pass-through query builders — filters belong
  in the service; the repository exposes named, intention-revealing methods.

## DTOs (Data Transfer Objects)

Explicit boundary types; never leak domain or ORM objects across the wire:

```python
# BAD: returning the ORM model / domain object directly
@app.get("/orders/{id}")
async def get_order(id: str) -> Order: ...        # leaks internals, couples API to schema

# GOOD: handler converts domain object to a response DTO
class OrderOut(BaseModel):
    id: str
    total: Decimal
    status: OrderStatus

@app.get("/orders/{id}")
async def get_order(id: str) -> OrderOut:
    order = await orders.get(id)
    return OrderOut.model_validate(order)
```

| Boundary | DTO | Direction |
|---|---|---|
| HTTP request | `OrderCreate`, `OrderUpdate` (Pydantic) | in |
| HTTP response | `OrderOut` | out |
| Repository result | domain object (not a DTO) | inward |

- Input DTOs validate and coerce at the edge; output DTOs pin exactly which
  fields are public (prevents accidental PII/internals leaks).
- Separate create vs update vs read shapes — one model for all three couples
  unrelated requirements.

## Middleware & Cross-Cutting Concerns

Pipeline order matters: auth → tenant/context → rate limit → logging → handler.

```python
def require_auth(handler: Handler) -> Handler:
    async def wrapped(req: Request) -> Response:
        user = await verify_token(req.authorization)   # raises ApiError(401)
        req.state.user = user                          # handlers read identity here
        return await handler(req)
    return wrapped
```

- Auth/permission checks fail closed: missing or invalid → 401/403, never pass.
- Per-request context (request id, user id) is set in middleware and consumed
  by logging — see error-handling.md for structured log fields.

## Caching (Cache-Aside)

Decorate a repository rather than scattering cache calls through services:

```python
class CachedUserRepository(UserRepository):
    async def find_by_id(self, user_id: str) -> User | None:
        if (cached := await redis.get(f"user:{user_id}")):
            return User.model_validate_json(cached)
        user = await self.base.find_by_id(user_id)
        if user:
            await redis.setex(f"user:{user_id}", 300, user.model_dump_json())
        return user
```

- Cache-aside on read; invalidate (delete key) on write — never write-through
  blind updates for relational data.
- TTL defaults: reference data minutes–hours, hot entities ~300s; anything
  cross-instance goes through Redis (see redis-patterns.md).
- Redis is the shared store for rate limits and locks too.

## Background Jobs & Queues

- Never do slow work inside a request handler — enqueue and return 202/201.
- Job handlers reuse the same service layer; they are just another entry point.
- Jobs must be idempotent or carry an idempotency key: at-least-once delivery
  is the norm, duplicates happen.
- Persist queue state in Redis or the DB, never an in-process list — in-process
  queues lose everything on deploy and don't scale across replicas.
- Classify failures per error-handling.md: transient → retry with backoff;
  permanent → dead-letter with full context, never silently dropped.

## Query Discipline (applies at the adapter)

- Select explicit columns, never `SELECT *` in hot paths.
- Batch related lookups (one `WHERE id = ANY(:ids)`) instead of per-item
  queries — see database-patterns.md for N+1 fixes and index requirements.

## Immutability

- Construct new values instead of mutating: return updated copies; use
  `frozen=True` dataclasses / Pydantic models for value objects.
- Shared mutable state across agents/tasks/requests is the primary source of
  race conditions; make ownership explicit.
- Event/state records are append-only: never rewrite history, append the next.

## Error Taxonomy

1. **Retryable & transient** — network blips, rate limits, timeouts: retry
   with exponential backoff + jitter, bounded by max attempts.
2. **Deterministic / caller error** — validation, missing resource, denial:
   never retry; return a precise error.
3. **Fatal / programmer error** — invariant violations: fail loudly, log with
   full context, stop rather than continue corrupt.

```python
async def with_retry(op: Callable[[], Awaitable[T]], attempts: int = 3) -> T:
    for i in range(attempts):
        try:
            return await op()
        except TransientError:
            if i == attempts - 1:
                raise
            await asyncio.sleep(2**i + random.random())
    raise AssertionError("unreachable")
```

## No Silent Failures

- Every `except` either recovers meaningfully, re-raises with context, or logs
  full exception detail. No `except: pass`.
- Errors are translated, not erased: DB/HTTP errors become domain errors at
  adapter boundaries, preserving cause (`raise DomainError(...) from exc`).
- Partial failures in multi-step operations are compensated or clearly
  surfaced — never dropped.

## Anti-Patterns (NEVER)

- Business logic in handlers or SQL in services — each layer has one job.
- Returning ORM rows or domain entities as HTTP responses (use DTOs).
- Generic `dict`-based repository methods instead of named queries.
- In-process in-memory queues/rate-limiter state in multi-replica services.
- Write-through cache updates on relational data without invalidation review.
- Unbounded retries, or retrying deterministic failures.
- Swallowed exceptions or lost `__cause__` during error translation.
