---
description: PostgreSQL-first query, indexing, transaction, pooling, and JSONB patterns for GOA data access
tags: database,postgres,mysql,indexing,transactions,performance
origin: ECC (enriched)
---

# Database Query & Schema Patterns

Efficient, safe data access for production Python systems: indexing strategy,
N+1 prevention, transaction boundaries, connection pooling, and JSONB usage.
PostgreSQL is GOA's primary engine — MySQL notes are marked where they apply.
Schema-change/migration procedure lives in database-migrations.md.

## When to Reference

- Writing or reviewing queries, joins, or ORM access patterns
- Diagnosing slow queries (EXPLAIN, missing indexes, N+1)
- Choosing indexes or data types for a new table
- Deciding transaction boundaries for multi-statement operations
- Configuring connection pools or server timeouts
- Modeling flexible/extension data with JSONB

## Indexing Strategy

| Query pattern | Index type | Example |
|---|---|---|
| `WHERE col = x` / `col > y` | B-tree (default) | `CREATE INDEX ON t (col)` |
| `WHERE a = x AND b > y` | Composite (a, b) | equality cols first, then range |
| JSONB containment `@>` / full-text `@@` | GIN | `USING gin (col)` |
| Time-series ranges on append-only tables | BRIN | `USING brin (created_at)` |
| Covering reads | INCLUDE | `ON t (email) INCLUDE (name)` |
| Soft-delete filtering | Partial | `ON t (email) WHERE deleted_at IS NULL` |

```sql
-- Composite order: equality columns first, then range/sort column
CREATE INDEX idx_orders ON orders (status, created_at);
-- Serves: WHERE status = 'pending' AND created_at > '2024-01-01' ORDER BY created_at
```

- Index foreign keys — Postgres does NOT auto-index them; unindexed FKs cause
  slow joins and lock-heavy cascade deletes. MySQL's InnoDB auto-indexes FKs.
- Every index costs write throughput, migration time, and buffer space — add
  with `EXPLAIN` evidence, not preemptively for every column.
- MySQL/MariaDB: use `utf8mb4` for user-facing text; prefer `BINARY(16)` over
  `VARCHAR(36)` for UUID lookup keys on hot tables (Postgres: native `uuid`).

## Data Types

| Use case | Use | Avoid |
|---|---|---|
| IDs | `bigint` / `uuid` | `int` on growable tables |
| Strings | `text` | `varchar(255)` |
| Timestamps | `timestamptz` (MySQL: `DATETIME` + app-managed UTC) | `timestamp` w/o tz assumptions |
| Money | `numeric(12,2)` / `DECIMAL(p,s)` | `float`, `double` |
| Flags | `boolean` | `varchar`, `int` |
| Status values | lookup table / constrained varchar | `ENUM` when values change often |

## N+1 Prevention

```python
# BAD: one query per item
for order in orders:
    order.customer = await customers.get(order.customer_id)   # N queries

# GOOD: batch fetch + map
custs = await customers.get_many({o.customer_id for o in orders})
by_id = {c.id: c for c in custs}
for order in orders:
    order.customer = by_id[order.customer_id]
```

- ORM forms: SQLAlchemy `selectinload()` (separate query, M2M) /
  `joinedload()` (JOIN, FK); Django `select_related` / `prefetch_related`.
- Detect in tests: `assertNumQueries` (Django), or SQLAlchemy `echo=True` /
  query-count assertions on listing endpoints.
- Upserts beat read-then-write:
  `INSERT ... ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v`
  (MySQL: `ON DUPLICATE KEY UPDATE` — use `VALUES(col)` for MariaDB compat).

## Queue / Claim Pattern

```sql
UPDATE jobs SET status = 'processing', started_at = now()
WHERE id = (
  SELECT id FROM jobs WHERE status = 'pending'
  ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED
) RETURNING *;
```

- `SKIP LOCKED` is for queue-style workloads only — it silently skips locked
  rows and is wrong for accounting/integrity-sensitive reads (both engines).
- MySQL workers: same shape with `START TRANSACTION ... COMMIT`; on deadlock
  roll back and retry the whole transaction with a bounded retry budget, and
  lock rows in a deterministic order (e.g. `ORDER BY id`) across code paths.

## Transactions & Isolation

- One use case's writes = one transaction; keep reads outside it when possible.
- Short transactions only: no external API calls, no long computation inside
  one. Deadlock checklist: consistent lock order, indexed predicates on
  UPDATE/DELETE, bounded retry on deadlock (1213 in MySQL).
- Defaults are fine for most work (READ COMMITTED / REPEATABLE READ in
  InnoDB); escalate only with a documented reason (e.g. SERIALIZABLE for
  check-then-write races you can't express as a constraint).
- Server timeouts are mandatory:
  `idle_in_transaction_session_timeout = '30s'` and `statement_timeout = '30s'`
  (Postgres); MySQL: `innodb_lock_wait_timeout = 10`.
- Batched backfills commit per batch so progress survives restarts.

## EXPLAIN Analysis

- Postgres: `EXPLAIN (ANALYZE, BUFFERS) SELECT ...`; MySQL: `EXPLAIN` (or
  `EXPLAIN ANALYZE` only when safe to execute — it runs the statement).
- Read: estimate vs actual rows gap → stale stats; `Seq Scan` on a large table
  with a selective predicate → missing index; `key=NULL` (MySQL) → index unused.

```sql
-- Slow-query sources (Postgres)
SELECT query, mean_exec_time, calls FROM pg_stat_statements
WHERE mean_exec_time > 100 ORDER BY mean_exec_time DESC;
SELECT relname, n_dead_tup FROM pg_stat_user_tables
WHERE n_dead_tup > 1000;                      -- bloat → consider VACUUM review
-- Unindexed FKs (Postgres): check pg_constraint 'f' rows vs pg_index (use the
-- catalog query in code review tooling, not by hand at 3am).
```

MySQL diagnostics: `SHOW FULL PROCESSLIST`, `SHOW ENGINE INNODB STATUS\G`
(capture soon after a deadlock — it's overwritten), slow query log with
`long_query_time = 1` and `log_queries_not_using_indexes = ON`.

## Connection Pooling

```python
engine = create_engine(
    "postgresql+asyncpg://app:***@db.internal/app",
    pool_size=10, max_overflow=5,
    pool_timeout=30, pool_recycle=240, pool_pre_ping=True,
    connect_args={"connect_timeout": 5},
)
```

- `pool_pre_ping=True` always; set `pool_recycle` below the server
  `wait_timeout` (e.g. recycle 240 vs wait_timeout 300 in MySQL).
- Sizing: pool total (size + overflow) per instance × instance count must fit
  under `max_connections` with headroom for admin/migration sessions.
- Separate pools (or users) for migration/admin vs runtime app traffic.

## JSONB Usage (Postgres)

```sql
CREATE TABLE events (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  payload jsonb NOT NULL,
  event_type text GENERATED ALWAYS AS (payload->>'type') STORED
);
CREATE INDEX idx_events_type ON events (event_type);          -- or GIN on payload
SELECT * FROM events WHERE payload @> '{"status": "failed"}'; -- GIN-indexed
```

- JSONB is for extension/variable data, NOT for fields needing relational
  integrity: foreign keys, ownership, tenancy, lifecycle stay as columns.
- Frequently filtered JSON paths → generated column + index (MySQL: `JSON_EXTRACT`
  generated column, same idea).
- Validate payloads with a schema (Pydantic) before writing — the DB won't.

## Server Defaults (Postgres starting point)

```sql
ALTER SYSTEM SET max_connections = 100;               -- size for RAM
ALTER SYSTEM SET work_mem = '8MB';
ALTER SYSTEM SET idle_in_transaction_session_timeout = '30s';
ALTER SYSTEM SET statement_timeout = '30s';
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
REVOKE ALL ON SCHEMA public FROM public;              -- security default
```

MySQL config is review-driven, not preset: key levers are
`innodb_buffer_pool_size`, `innodb_flush_log_at_trx_commit = 1`,
`sync_binlog = 1`, `wait_timeout = 300`, binlog `ROW` format.

## Read Replicas (MySQL-heavy setups)

- Replicas lag. Never route read-your-own-write paths (checkout flows,
  permission checks, post-create reads) to a replica immediately after a
  write — pin them to the primary. Monitor IO/SQL thread health and lag, not
  just TCP connectivity.

## Anti-Patterns (NEVER)

- `SELECT *` in hot paths; per-item queries inside loops (N+1).
- Unindexed foreign keys; indexes added without EXPLAIN evidence.
- `float`/`double` for money; `timestamp` without time zone.
- External API calls inside a transaction; unbounded transactions.
- `SKIP LOCKED` for integrity-sensitive reads (queue use only).
- Deep `OFFSET` pagination on large tables — use keyset/cursor (see api-design.md).
- JSONB for relational/foreign-key data; writing unvalidated JSON payloads.
- Pool recycle above server `wait_timeout`; pools sized without max_connections math.
- Application DB users with `ALL PRIVILEGES`/`*.*` — least privilege only.
