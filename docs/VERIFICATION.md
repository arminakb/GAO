# Verification & Evidence

GOA never accepts "tests pass" from agent prose. Verification is a
deterministic engine (`harness/verification.py`):

## 1. Toolchain discovery

Manifest probes (no assumptions):

| Manifest | Toolchain | Commands |
|---|---|---|
| `uv.lock` | python_uv | `uv run pytest -x -q`, `uv run mypy`, `uv run ruff check` |
| `pyproject.toml` | python_pip | `<sys.executable> -m pytest -x -q`, `-m mypy`, `-m ruff` |
| `package.json` | node | `npm test`, `npm run build --if-present`, `npm run lint --if-present` |
| `go.mod` | go | `go test ./...`, `go build ./...` |
| `Cargo.toml` | rust | `cargo test --quiet`, `cargo build --quiet` |
| none | unknown | no commands (reported, not fabricated) |

## 2. Execution

Each command runs through the sandboxed `ExecutionToolkit` (cwd confined to
the workspace, banned-pattern filter, timeout, stdout/stderr captured,
exit code recorded).

First failure stops the gate (fail-fast) and drives recovery routing.

## 3. Failure classification (deterministic)

Output markers win over command category:

- `SyntaxError` / `IndentationError` → `syntax_error`
- `ModuleNotFoundError` / `No module named` → `dependency_error`
- `FAILED` / `AssertionError` → `test_failure`
- otherwise the command's category: `type_error`, `lint_error`, `build_error`
- no exit code → `timeout`

## 4. Recovery routing

| Category | Resolver |
|---|---|
| test_failure | `test_failure_resolver` |
| type_error / lint_error / syntax_error | `coder` |
| build_error / dependency_error / timeout | `build_error_resolver` |
| unknown | `coder` |

Bounded: after 3 failed verify cycles in a session, `goa_repair` returns
`escalate` instead of looping.

## 5. Evidence model (machine-readable)

```json
{
  "verdict": "RED",
  "failure_category": "test_failure",
  "commands": [
    {
      "check": "tests",
      "command": "uv run pytest -x -q",
      "ok": false,
      "exit_code": 1,
      "duration_ms": 812.4,
      "output_excerpt": "FAILED tests/test_x.py::test_a - assert 1 == 2",
      "executed_at": "2026-09-01T09:00:00Z"
    }
  ],
  "summary": "test_failure in 'uv run pytest -x -q'"
}
```

Every verify run is appended to the session's `VerificationRecord`, giving a
complete RED→GREEN trail per task (TASK.md §13).
