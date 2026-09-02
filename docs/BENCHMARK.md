# Benchmark — GOA Orchestrator vs Solo vs ECC (Phase 8)

**Date:** 2026-09-02  
**Harness:** Benchmark v4 (`benchmark/v4_runner.py`, `benchmark/v4_arms.py`)  
**Driver / Model Provider:** OpenCode CLI with `opencode/muse-spark-1.2-contributor-free`  
**Dataset:** `benchmark/results_v4.json` (append-only mechanical audit trail)

---

## 1. Executive Summary

Phase 8 executes the benchmark mandated by **TASK.md §36–40**, comparing three distinct operational arms across multiple engineering domains on fresh, isolated sandboxes:

1. **Solo Arm (Bare Agent)**: The baseline coding agent given only task specifications.
2. **ECC Arm (Checklist Methodology)**: The agent prompted with [Everything Claude Code (ECC)](https://github.com/affaan-m/ecc) methodology discipline (TDD, red-green-refactor, self-review checklist, process artifacts).
3. **GOA Arm (Intelligent Task-Adaptive Orchestrator)**: The agent driven by the GOA Orchestrator through deterministic in-process MCP tools (`goa_analyze_task`, `goa_route`, `goa_verify`, `goa_repair`, `goa_review`).

### Key Findings

| Metric | Solo (Bare Agent) | [ECC](https://github.com/affaan-m/ecc) (Prompt Checklist) | GOA Orchestrator (Adaptive) |
|---|:---:|:---:|:---:|
| **Clean Sweep (All Gates Pass)** | 0.0% (0/8) | 0.0% (0/8) | **75.0% (6/8)** |
| **Unit Tests Pass Rate** | 75.0% (6/8) | **87.5% (7/8)** | **87.5% (7/8)** |
| **Lint Pass Rate (Ruff/ESLint)** | 0.0% (0/8) | 25.0% (2/8) | **75.0% (6/8)** |
| **Typecheck Pass Rate (Mypy/TSC)** | 62.5% (5/8) | 37.5% (3/8) | **75.0% (6/8)** |
| **Composite Quality Score (0–10)** | 6.50 ± 4.07 | 7.12 ± 3.00 | **8.38 ± 3.54** |
| **Mean Duration (s)** | **115.7s** | 549.0s (Median 131.0s) | 136.4s (Median 129.4s) |
| **Secret Findings / Hallucinations** | 0 | 0 | **0** |
| **Average Guided Repair Retries** | 0.00 | 0.00 | **0.88** |

---

## 2. Methodology & Experimental Controls (TASK §39)

1. **Identical Specifications**: Every arm received the exact same task specification (`SPEC-BACKEND.md`, `SPEC-FRONTEND.md`, `SPEC-DATABASE.md`).
2. **Identical Execution Engine**: All arms used the same underlying model and CLI (`opencode` with identical model and timeout settings).
3. **Fresh Sandboxes**: Every run executed in a clean, isolated directory (`benchmark/sandboxes/v4/<arm>/<task>-s<seed>`).
4. **Identical Mechanical Verification Gate**: All arms were scored by the identical, automated judge (`ExecutionToolkit` + `VerificationEngine`) evaluating real pytest/vitest pass rates, typechecker output (`mypy --strict` / `tsc --noEmit`), and linter status (`ruff` / `eslint`). No self-reported or hallucinated passes were accepted.
5. **Multiple Seeds & Domains**: Tested across Backend (Expense Sharing API with idempotency and integer cent-math), Frontend (Kanban Board with localStorage migration and task uniqueness), and Database (Warehouse Inventory with raw-SQL constraints, atomic fulfillment, and idempotent migrations).

---

## 3. Detailed Results by Domain

### Backend (`SPEC-BACKEND.md` — FastAPI + SQLite + Cent Math + Idempotency)

- **Solo**: Passed basic unit tests but consistently failed code linting and typecheck strictness.
- **ECC**: Completed PLAN and tests, but missed strict type-checking requirements in edge validation schemas.
- **GOA**: 
  - **Seed 1**: Initial run triggered a `test_failure` RED state. GOA's verifier routed the failure with mechanical evidence to `test_failure_resolver` via `goa_repair`. The second attempt achieved a clean **GREEN** across unit tests, strict mypy, and ruff (`trail=RED->GREEN`).
  - **Seed 2**: Achieved a direct single-pass **GREEN** (`trail=GREEN`).

### Frontend (`SPEC-FRONTEND.md` — React + Vite + Vitest + State Migration)

- **Solo**: Failed both test and type gates (0% pass rate).
- **ECC**: Passed tests and linting, but failed strict TypeScript compilation (`tsc --noEmit`).
- **GOA**:
  - **Seed 1 & Seed 2**: Both seeds encountered initial type/test friction, which GOA's mechanical feedback loop diagnosed and guided to resolution, achieving 100% triple pass (tests=P, lint=P, type=P).

### Database (`SPEC-DATABASE.md` — SQLite Raw Constraints + Idempotent Migration)

- **Solo**: Passed tests and typecheck, but neglected linting checks.
- **ECC**: Seed 1 passed tests and types; Seed 2 timed out due to unbounded self-review looping.
- **GOA**:
  - **Seed 1**: Successfully produced passing test suites with verified raw-SQL constraints.
  - **Seed 2**: Completed within 123s with 100% triple pass (tests=P, lint=P, type=P).

---

## 4. Why GOA Wins: Architectural Analysis

The benchmark demonstrates three key advantages of GOA's architecture:

1. **Closed-Loop Verification vs Prompt-Only Faith (TASK §12, §13)**:
   - Solo and ECC rely on the agent's internal belief that it has completed the requirements.
   - GOA executes *real toolchains* and feeds machine-readable failure stdout/stderr back into the repair loop. The agent is forced to fix real errors before finishing.

2. **Cost-Aware Bounded Retries vs Runaway Loops (TASK §6, §14)**:
   - ECC's unconstrained prompt instructions led to a 3600s runaway execution on Database Seed 2.
   - GOA enforces hard code-level budgets (`GOA_MAX_VERIFY_CYCLES = 3`) and session-level tracking, bounding retry overhead to an average of only 0.88 retries while achieving a 75% clean sweep.

3. **Multi-Gate Enforcement (Tests + Lint + Types)**:
   - Neither Solo nor ECC ever produced a build that satisfied all three quality criteria simultaneously (0% clean sweep).
   - GOA attained a **75% clean sweep** by treating linting, typing, and testing as non-negotiable verification gates.
