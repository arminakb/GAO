---
description: Safe, zero-downtime database migrations — tooling, expand/contract, batching, and rollback rules
tags: migrations,database,postgres,alembic,zero-downtime
origin: ECC (enriched)
---

# Database Migrations

Safe, reversible schema changes for production systems. Every change is a
migration file reviewed against lock and rollback risk before it runs. Query
and indexing patterns live in database-patterns.md.

## When to Reference

- Writing or reviewing a schema/data migration (add/drop column, index, backfill)
- Planning a zero-downtime schema change or rollback
- Setting up migration tooling for a new project
- Diagnosing a migration that locks, times out, or drifts between environments

## Core Principles

1. Every schema change is a migration file — never alter production manually.
2. Forward-only in production; a "rollback" is a new forward migration.
3. Schema (DDL) and data (DML) migrations are separate files.
4. Deployed migrations are immutable — fix forward, never edit history.
5. Test against production-sized data; fine on 100 rows may lock on 10M.

## Pre-Apply Safety Checklist

- [ ] Documented rollback (or explicitly marked irreversible)
- [ ] No full table locks on large tables (concurrent operations)
- [ ] New columns nullable or with a default — never bare `NOT NULL`
- [ ] Indexes on existing tables created `CONCURRENTLY` (runs outside a
      transaction: Alembic `op.execute` with autocommit block / Django `atomic = False`)
- [ ] Backfill in its own migration, batched
- [ ] Tested on a production-sized copy; rollback plan written down

## Adding Columns & Indexes Safely

```sql
-- GOOD: nullable, instant
ALTER TABLE users ADD COLUMN avatar_url text;

-- GOOD: default with NOT NULL — Postgres 11+ is metadata-only
ALTER TABLE users ADD COLUMN is_active boolean NOT NULL DEFAULT true;

-- BAD: NOT NULL without default on a populated table (full rewrite + lock)
ALTER TABLE users ADD COLUMN role text NOT NULL;

-- GOOD: non-blocking index; BAD: plain CREATE INDEX blocks writes
CREATE INDEX CONCURRENTLY idx_users_email ON users (email);
```

## Expand–Contract Pattern

Never rename or restructure in one deploy. Three phases:

| Phase | Migration | Code deploy |
|---|---|---|
| 1 EXPAND | Add new column/table (nullable/default) | Write to BOTH old and new; backfill in batched migration |
| 2 MIGRATE | (none) | Read from NEW, write to BOTH; verify consistency |
| 3 CONTRACT | Drop old column/table | Use only NEW |

Example — rename `username` → `display_name`: add `display_name` nullable →
batched backfill `UPDATE users SET display_name = username WHERE display_name
IS NULL` → deploy dual-write, read-new → drop `username` a week later.

Removing a column: remove all code references first, deploy, then drop in the
next migration. Django: `SeparateDatabaseAndState` with `state_operations`
detaches the model field without dropping the DB column yet.

## Batched Data Migrations

Single-statement updates over millions of rows lock the table and hold one
long transaction. Batch, commit per batch, resume on restart:

```sql
-- Postgres shape; commit each pass until zero rows update
UPDATE users SET normalized_email = lower(email)
WHERE id IN (
  SELECT id FROM users WHERE normalized_email IS NULL
  LIMIT 10000 FOR UPDATE SKIP LOCKED
);
```

Alembic data migration (SQLAlchemy):

```python
def upgrade() -> None:
    conn = op.get_bind()
    while True:
        res = conn.execute(sa.text("""
            UPDATE users SET display_name = username
            WHERE id IN (SELECT id FROM users
                         WHERE display_name IS NULL LIMIT 10000)
        """))
        if res.rowcount == 0:
            break
        conn.commit()  # requires a non-transactional/autocommit migration
```

Django: use `apps.get_model("app", "Model")` (historical model) — never import
current models; pair `RunPython(forward, noop_or_reverse)`.

## Tooling Quick Reference

| Tool | Create | Apply | Notes |
|---|---|---|---|
| Alembic (Python) | `alembic revision -m "..."` | `alembic upgrade head` | autogenerate diffs; offline SQL mode |
| Django | `makemigrations` | `migrate` | `showmigrations` for state |
| Prisma | `migrate dev --name X` | `migrate deploy` | custom SQL via `--create-only` (CONCURRENTLY) |
| Drizzle | `drizzle-kit generate` | `drizzle-kit migrate` | `push` is dev-only |
| Kysely | `kysely migrate make X` | `kysely migrate latest` | migrations use `Kysely<any>` — never the typed interface |
| golang-migrate | `create -ext sql -seq` | `migrate up` / `down 1` | `force VERSION` to clear dirty state |

- Alembic is GOA's default for Python services; run `alembic upgrade head` in
  the deploy pipeline before the new app version starts serving traffic.
- Version-controlled, code-reviewed, applied by CI — never by hand on prod.
- Downgrade support: generate `downgrade()` for reversible DDL; data
  destruction is often irreversible — mark and document instead of faking it.

## Zero-Downtime Rules

- Deploy order: migrate schema first (additive), then code; old code must run
  against the new schema for at least one rollout cycle.
- One deploy per phase of expand–contract — bundling phases defeats the point.
- Concurrent index creation cannot run inside a transaction; configure the
  tool accordingly or it fails/wraps in a giant lock.
- Watch lock timeouts during deploy; if a migration blocks, investigate before
  retrying blind.
- Rollback safety: additive migrations roll back trivially (drop what was
  added); contract-phase drops only happen after the previous phase is
  verified in production — never roll back past a drop without a restore plan.

## Anti-Patterns (NEVER)

| Anti-pattern | Better approach |
|---|---|
| Manual SQL applied to production | Migration files with audit trail |
| Editing a deployed migration | New forward migration |
| `NOT NULL` without default on populated table | Nullable → backfill → constraint |
| Inline index on large existing table | `CREATE INDEX CONCURRENTLY` |
| Schema + data changes in one migration | Separate migrations |
| Dropping a column still referenced by deployed code | Remove code first, drop next deploy |
| Importing current models in a data migration (Django) | `apps.get_model` historical model |
| Renaming a column in place | Expand–contract over two deploys |
| Unbatched multi-million-row backfill | Batched `SKIP LOCKED` loop, commit per batch |
