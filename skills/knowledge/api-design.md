---
description: REST API design — contract-first workflow, envelopes, status codes, pagination, filtering, rate limiting, auth, and versioning
tags: api,rest,http,pagination,versioning
origin: ECC (enriched)
---

# API Design Principles

Contract-first API design: define the interface (schemas, error semantics,
versioning) before implementation, validate every input at the boundary, and
return consistent, predictable responses so clients can handle success and
failure uniformly.

## When to Reference

- Designing a new HTTP endpoint, RPC method, or service interface
- Reviewing API contracts for consistency and error semantics
- Deciding versioning, pagination, filtering, or rate-limiting structure
- Specifying validation rules for inbound payloads

## Contract-First Workflow

1. Specify the resource model and operations before writing handlers.
2. Define request/response schemas explicitly (Pydantic models, OpenAPI).
3. Define the full error surface: every failure mode and its status code.
4. Only then implement; validate that the implementation matches the contract
   with schema-level tests.

## Response Envelope

Use one consistent shape for every response so clients need a single parser:

```python
class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    code: str  # stable machine-readable code, e.g. "VALIDATION_ERROR"
    details: list[str] = []

class ListResponse(BaseModel, Generic[T]):
    success: bool = True
    data: list[T]
    pagination: Pagination | None = None
```

- `success` flag, typed `data`, and a structured error object — never raw
  exception strings or stack traces in responses.
- Error messages are user-actionable; detailed context goes to server logs.
- If your project uses a `meta`/`links` pagination style instead, keep the
  same principle: one shape for all responses, one parser.

## Status Codes & Error Semantics

| Code | Meaning | Use for |
|------|---------|---------|
| 200 | OK | GET, PUT, PATCH with body |
| 201 | Created | POST — include `Location` header |
| 204 | No Content | DELETE, PUT without body |
| 400 | Bad Request | Malformed input, validation failure |
| 401 | Unauthorized | Missing/invalid authentication |
| 403 | Forbidden | Authenticated but not authorized |
| 404 | Not Found | Unknown resource |
| 409 | Conflict | Duplicate entry, state conflict |
| 422 | Unprocessable | Semantically invalid (valid JSON, bad data) |
| 429 | Too Many Requests | Rate limited — include `Retry-After` |
| 500 | Internal Error | Server fault — never expose details |
| 503 | Unavailable | Overload/maintenance — include `Retry-After` |

- Idempotency: `GET`/`PUT`/`DELETE` safely retryable; `POST` for non-idempotent
  creation; `PATCH` made idempotent by applying full field semantics.
- Common mistakes: 200 for everything (`{"status": 200, "success": false}`),
  500 for validation errors, 200 for created resources (use 201 + `Location`).
- Never leak internal details (SQL, file paths, stack traces) in error bodies.

## Resource Naming

```
GET    /api/v1/users            # plural nouns, kebab-case, no verbs
GET    /api/v1/users/:id/orders # sub-resources for relationships
POST   /api/v1/orders/:id/cancel  # verbs only for true non-CRUD actions

# BAD: /getUsers, /user (singular), /team_members (snake_case in URL)
```

## Pagination

Offset vs cursor — pick by data shape, not habit:

| Use case | Type |
|----------|------|
| Admin dashboards, small datasets (<10K) | Offset (`?page=2&per_page=20`) |
| Infinite scroll, feeds, large datasets | Cursor (`?cursor=...&limit=20`) |
| Public APIs | Cursor default, offset optional |
| Search results | Offset (users expect page numbers) |

```sql
-- Offset: easy, but slow on large offsets and unstable under concurrent inserts
SELECT * FROM users ORDER BY created_at DESC LIMIT 20 OFFSET 20;
-- Cursor: stable performance and consistent under inserts; no random page jump
SELECT * FROM users WHERE id > :cursor ORDER BY id ASC LIMIT 21; -- +1 for has_next
```

Always bound `per_page`/`limit` server-side; return `total`/`has_next` and
`next_cursor` in the pagination object.

## Filtering, Sorting, Search

```
GET /api/v1/orders?status=active&customer_id=abc       # equality filters
GET /api/v1/products?price[gte]=10&price[lte]=100      # comparison operators
GET /api/v1/products?category=electronics,clothing     # multi-value (comma)
GET /api/v1/products?sort=-created_at,price            # "-" prefix = desc
GET /api/v1/products?q=wireless+headphones             # full-text search
GET /api/v1/users?fields=id,name,email                 # sparse fieldsets
```

Whitelist filterable/sortable fields server-side — never map query params
directly to SQL columns.

## Authentication & Authorization

- Bearer tokens (`Authorization: Bearer ...`) for user clients; API keys
  (`X-API-Key`) for server-to-server.
- Resource-level authz: fetch the resource, then compare ownership —
  `403` when it belongs to someone else, never just `404`-hide everything.
- Role-based authz via middleware/dependencies (`requireRole("admin")`);
  derive permissions from the authenticated identity, never client input.

## Rate Limiting

```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1640000000

# When exceeded:
HTTP/1.1 429 Too Many Requests
Retry-After: 60
{"success": false, "error": "Rate limit exceeded. Try again in 60 seconds.",
 "code": "RATE_LIMIT_EXCEEDED"}
```

Typical tiers: anonymous 30/min per IP, authenticated 100/min per user,
premium 1000/min per key, internal 10000/min per service. See
redis-patterns.md for the fixed-window and sliding-window implementations.

## Versioning & Evolution

- Version in the URL (`/api/v1/...`) — explicit, cacheable, easy to route.
- Don't version until you must; keep at most 2 active versions.
- Non-breaking (no new version): adding response fields, optional query
  params, new endpoints.
- Breaking (new version): removing/renaming fields, changing types, URL
  structure, or auth method. Never repurpose an existing field's meaning.
- Deprecation: announce (6 months for public APIs), add
  `Sunset: Sat, 01 Jan 2026 00:00:00 GMT` header, return `410 Gone` after.
- Document every breaking change in a changelog with a migration path.

## Validation at Boundaries

- Validate every inbound payload with a schema at the edge (FastAPI does this
  via Pydantic request models); reject unknown/extra fields deliberately.
- Normalize and sanitize before persistence; never trust client-supplied IDs
  for authorization decisions — derive them from the authenticated context.
- Fail fast: a request missing required context is rejected before any
  side effects occur.

## Review Checklist

- [ ] Request/response schemas defined and enforced before handlers run
- [ ] Single consistent envelope; no internal details in error bodies
- [ ] Status codes match semantics (not 200-for-everything); idempotency honored
- [ ] Pagination on all list endpoints (cursor or offset, bounded limits)
- [ ] Filter/sort fields whitelisted; authorization derived from identity
- [ ] Rate limiting configured with standard headers
- [ ] Breaking changes versioned, documented, with Sunset/410 policy
- [ ] OpenAPI spec updated; naming consistent with existing endpoints
