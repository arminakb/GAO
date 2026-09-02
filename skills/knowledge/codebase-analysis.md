---
description: Systematic unfamiliar-codebase analysis — survey signals before detail, map architecture, dependencies, and hotspots, then plan
tags: codebase, analysis, onboarding, planning, exploration
origin: ECC (enriched)
---

# Codebase Analysis & Onboarding Method

A systematic method for analyzing an unfamiliar codebase before acting on it: survey structure and signals first, read selectively second, and only then commit to a plan. This reference is framed for a planner agent decomposing a task in a repository it has never seen — the survey output (stack, entry points, data flow, conventions, hotspots) feeds directly into a decomposition plan.

## When to Reference

- First encounter with an unfamiliar repository before planning or implementing
- Taking over a large legacy codebase needing a structural overview
- Answering "how is X done in this codebase?" or "where does Y live?"
- Producing an onboarding guide or project-instructions file for a repo
- Deciding where new code should go so it matches existing conventions

## Survey Before Detail

Never read every file. Gather cheap, high-signal signals first (glob/grep, not file reads), and only read files where signals are ambiguous or where the task points.

Phase 1 reconnaissance — run these checks in parallel:

1. **Package manifest** — Python: `pyproject.toml`, `requirements*.txt`, `setup.py`/`setup.cfg`; Node: `package.json`; Go: `go.mod`; Rust: `Cargo.toml`.
2. **Framework fingerprinting** — Django settings, Flask/FastAPI app factory (`main.py`, `app.py`), uvicorn/gunicorn entrypoints, `next.config.*`/`vite.config.*`, plugin configs.
3. **Entry points** — `main.py`, `cli.py`, `manage.py`, `__main__.py`, `cmd/`, package `__init__.py` exports, `[project.scripts]` console entry points.
4. **Directory snapshot** — top 2 levels only, ignoring `.venv`, `node_modules`, `__pycache__`, `dist`, `build`, `.next`, `.git`.
5. **Config and tooling** — `ruff`/`mypy`/`pyright`/`tsconfig` config, `Makefile`, `Dockerfile`, `docker-compose*`, `.github/workflows/`, `.env.example`.
6. **Test structure** — `tests/`, `test_*.py` / `*_test.py` / `*.spec.ts`, `pytest.ini` / `pyproject [tool.pytest]` / `jest.config.*`, coverage config.

Rules: verify, don't guess — if config implies one framework but the code uses another, trust the code. Flag unknowns explicitly ("could not determine test runner") rather than guessing. Prefer glob/grep over file reads.

## Map the Architecture

From recon data, establish:

- **Tech stack**: language and version constraints, frameworks and major libraries, databases/ORMs, build tools, CI platform.
- **Architecture pattern**: monolith vs packages/services/monorepo/serverless, API style (REST/GraphQL/gRPC), background workers, plugin/MCP-server layout.
- **Key directories**: map each top-level directory to its purpose (e.g. `adapters/` → provider integrations, `graph/` → orchestration graph, `tests/` → suites).
- **Data flow**: trace one request from entry to response — where it enters (router/handler), how it's validated (Pydantic schemas, guards), where business logic lives (services, use cases), how it reaches persistence (ORM, repositories, raw queries).

## Entry Points & Dependency Mapping

- **Entry points**: CLI commands, HTTP route tables, task/queue consumers, and module-level side effects. These anchor every "where does execution start?" question.
- **Dependency mapping**: for the subsystems the task touches, list what they import and who imports them. High fan-in modules (shared utils, base classes, core models) are change multipliers — a change there ripples everywhere.
- Identify internal boundaries: which modules are importable from anywhere vs. private to a feature. New code should respect these boundaries.
- Note framework-specific wiring (dependency injection, plugin registries, MCP server registration) — extension points define where new capabilities belong.

## Hotspot Identification

Prioritize attention on:

- Files/directories with many recent commits (churn) — likely to change again and to conflict.
- High fan-in/fan-out modules — small edits have large blast radius.
- Large files (>~800 lines) and deep nesting — candidates for splitting when touched.
- Test hotspots: areas with thin or no coverage that the task will modify need new tests first.
- Generated or vendored code — classify and exclude from review and editing; locate its source instead. Distinguish project code from embedded third-party code and build artifacts before judging anything.

| Classification | Meaning | Action |
|---|---|---|
| Project code | Owned by this repo | Read, review, modify per conventions |
| Embedded third-party | Vendored/bundled library code | Don't edit; track upstream version and updates |
| Build artifact | Generated or committed output | Exclude from analysis; locate the generator |

Conventions to detect before writing any code: naming style (snake_case modules, PascalCase classes), error handling pattern (exceptions vs Result types), typing strictness (full Pydantic/`typing` coverage vs partial), async style (asyncio patterns, sync/async split), test naming and fixtures, and git conventions (commit style, branch/PR workflow — skip if history is shallow or absent and say so).

## Producing a Decomposition Plan

Convert the survey into a plan the implementing agent can execute:

1. **Restate the task** in terms of the discovered architecture (which layer, which modules, which entry point).
2. **Enumerate concrete edits**: file-by-file additions/changes, each scoped to one subsystem where possible.
3. **Respect conventions**: place new files where similar files already live; reuse existing patterns (base classes, registries, schemas) instead of inventing parallel ones.
4. **Sequence by dependency**: shared/core changes first, then dependents; each step leaves the codebase importable and tests green.
5. **Attach verification to each step**: the specific test command (e.g. `pytest tests/unit -x`) or type check (e.g. `mypy <paths>`) that proves the step.
6. **Mark hotspots and risks**: call out high fan-in files, missing test coverage, and unknown conventions discovered in the survey.
7. **Keep the plan artifact concise** — scannable in ~2 minutes; details belong in the code.

## Output Artifacts

When a full onboarding write-up is wanted, produce:

- **Onboarding guide**: overview (2-3 sentences), tech-stack table, architecture description, key entry points, directory map, request lifecycle trace, conventions, common commands (dev/test/lint), and a "want to X → look at Y" table.
- **Project instructions file** (e.g. CLAUDE.md / AGENTS.md): detected stack, code style, test commands and patterns, build/run/lint commands, project structure, conventions. If one already exists, read it first and enhance — preserve existing content and mark what was added.

## Anti-Patterns (NEVER)

- Instructions file longer than ~100 lines — keep it focused.
- Listing every dependency — only the ones that shape how you write code.
- Explaining obvious directory names; copying the README instead of adding structural insight.
- Reading every file before forming a view.
- Guessing conventions the repo contradicts; presenting guesses as facts instead of flagging unknowns.
- Treating green CI as proof of structural health — coverage and churn analysis answer different questions.
