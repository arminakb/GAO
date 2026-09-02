# Benchmark Project: Warehouse Inventory Schema & Data Layer (Database Design)

A small but complete database-design project that the contestant must build
from scratch. Chosen to exercise schema design skills: constraint design,
transactional invariants enforced at the database level, migration
safety, query correctness, and documentation of schema decisions.

## Functional Requirements

1. **Domain**: Warehouses, products, stock levels, orders.
   - `warehouses(id, name)`; `products(id, sku UNIQUE, name, unit_price_cents)`
   - Stock: a product's quantity **per warehouse** (composite unique key)
   - `orders(id, warehouse_id, created_at)` and order lines
     (product, quantity > 0)
2. **Schema deliverable** (SQL DDL files, executed by the data layer):
   - FKs with explicit referential actions; CHECK constraints for
     non-negative stock, positive quantities, non-negative prices
   - Indexes for the three hot queries below; each index justified in
     `SCHEMA.md`
3. **Data layer** (Python package, typed):
   - `receive_stock(warehouse, product, qty)` and
     `fulfill_order(order_id)` — fulfillment must decrement stock for all
     order lines **atomically**: if any line lacks stock, NOTHING is
     decremented and a typed error is raised. The invariant
     `stock >= 0` must be enforced by the DATABASE (constraint/trigger),
     not only application code.
   - Hot queries: (a) low-stock report (products with stock < threshold
     per warehouse), (b) order history for a product across warehouses,
     (c) revenue per warehouse per calendar month
4. **Migration deliverable**: `migrate.py` upgrading a v1 database (no
   categories) to v2 (adds a `categories` table and a nullable
   `category_id` on products, backfills category from product name
   prefix "cat-" if present). The migration MUST be idempotent (safe to
   run twice) and must not duplicate backfilled rows.
5. **Tests** (pytest): constraint violations attempted via **raw SQL**
   (negative stock, duplicate SKU, FK violation) must fail at the
   database level; fulfillment atomicity (insufficient-stock order leaves
   stock untouched); migration run twice → same row counts.

## Non-Functional Requirements

- Python 3.12, SQLite via stdlib `sqlite3`, full type hints,
  `mypy --strict` clean on `src`
- No secrets; `SCHEMA.md` documents entities, constraints, and each
  index with a one-line justification; README with run instructions

## Deliverables

- `sql/` DDL + migration files (or equivalent in-code DDL), source under
  `src/`, tests under `tests/`, `SCHEMA.md`, `pyproject.toml`, `README.md`
- `uv run pytest` and `uv run mypy --strict src` must pass from a clean
  checkout

## Deliberate Traps

- App-level-only stock checks pass naive tests but fail the raw-SQL
  constraint test (Req 3/5): the database itself must reject negative
  stock even when the data layer is bypassed.
- Non-atomic fulfillment (multi-line decrement without a transaction)
  fails the atomicity test: partial decrement must be impossible.
- Idempotent migration (Req 4) fails if `ALTER TABLE`/backfill runs
  unconditionally: second run must detect applied state and no-op
  without erroring or duplicating.
- Revenue-by-month (Req 3c) punishes string-month math: month boundaries
  and grouping must be correct via date functions, and revenue must be
  computed in integer cents.
