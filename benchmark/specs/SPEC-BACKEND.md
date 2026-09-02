# Benchmark Project: Expense Sharing API (Backend)

A small but complete backend service that the contestant must build from
scratch. Chosen to exercise backend skills: typed API design, money math,
idempotency, validation, error contracts, persistence, and testing.

## Functional Requirements

1. **Domain**: Groups with members and shared expenses. Members are free
   strings (no auth). An expense records: payer, amount (integer cents),
   participants (non-empty list of member names), optional description.
2. **API** (HTTP JSON):
   - `POST /groups` — create a group (name) → 201
   - `GET /groups/{id}` — group with members and totals
   - `POST /groups/{id}/expenses` — add an expense. Supports an optional
     `Idempotency-Key` header: retrying the same key returns the original
     response and must NOT create a duplicate expense.
   - `GET /groups/{id}/balances` — net balance per member (what they are
     owed / owe), such that all balances sum to exactly 0
   - `GET /groups/{id}/expenses` — list expenses
   - `DELETE /expenses/{id}` — remove an expense (balances recompute)
3. **Money rule**: an expense is split evenly across participants **in
   integer cents**; the remainder (amount − k·floor) is distributed to the
   first participants in list order (1 cent each). Splitting must never
   lose or invent a cent: sum of shares == amount for every expense.
4. **Errors**: machine-readable JSON errors with stable codes
   (`GROUP_NOT_FOUND`, `INVALID_AMOUNT`, `DUPLICATE_MEMBER`, …) in one
   consistent envelope on every error path — including request-validation
   failures. No raw tracebacks.
5. **Persistence**: SQLite (file-backed); schema created idempotently at
   startup; deleting an expense must cascade/remove its shares.

## Non-Functional Requirements

- Python 3.12, full type hints, `mypy --strict` clean on `src`
- pytest suite: unit + tests for idempotency, exact cent-splitting, and
  balance-sum-zero invariant
- No secrets in code; README with run instructions
- Standard library + FastAPI/Starlette permitted; heavy frameworks beyond
  that are not

## Deliverables

- Source under `src/`, tests under `tests/`, `pyproject.toml`, `README.md`
- `uv run pytest` and `uv run mypy --strict src` must pass from a clean
  checkout

## Deliberate Traps

- The idempotency rule (Req 2) silently fails in naive implementations:
  a retry either duplicates the expense or (worse) replays as a 500. Only
  a test posting the same key twice and asserting identical response +
  single expense catches it.
- Cent-splitting (Req 3) punishes float math and naive `amount // k`:
  100 cents over 3 people must be 34/33/33 in list order, and shares must
  sum to exactly 100.
- Balance-sum-zero (Req 3/4) fails if shares are dropped or double-counted
  when participants include the payer or after a delete.
- Validation failures (e.g. amount ≤ 0, empty participants) must return
  the stable envelope — the framework-default 422 shape is a spec
  violation.
