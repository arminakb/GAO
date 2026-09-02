---
description: Idiomatic Python standards — naming, typing, structure, error handling, and patterns for production code
tags: python,standards,typing,error-handling
origin: ECC (enriched)
---

# Python Coding Standards & Patterns

Idiomatic Python standards for writing and reviewing production code. Code
should be obvious, explicitly typed, and safe by default: readability outranks
cleverness, and every error path is handled deliberately rather than silently.

## When to Reference

- Writing new Python modules, functions, or classes
- Reviewing Python code for quality, typing, or structure
- Refactoring existing Python code or packages
- Deciding how to name, document, or type a public interface

## Naming Conventions

- `snake_case` for functions, variables, and modules; `PascalCase` for classes;
  `SCREAMING_SNAKE_CASE` for module-level constants.
- Names describe intent, not type: `active_users`, not `user_list_2`.
- Booleans read as predicates: `is_valid`, `has_permission`, `retry_enabled`.
- Private/internal members use a single leading underscore; never rely on
  name mangling (`__attr`) outside of classes designed for inheritance.

## Typing

- Type-hint every public function signature and return type. Use modern
  built-in generics: `list[str]`, `dict[str, int]`, `X | None`.
- Prefer `from __future__ import annotations` at module top for forward refs.
- Use `Literal`, `StrEnum`, or a small `Enum` instead of free-form strings.
- Use `TypeVar`/`TypeAlias` for generic contracts; `Protocol` for structural
  duck typing (accept anything with the methods you need, no inheritance).
- Use `pydantic.BaseModel` for data crossing system boundaries (API payloads,
  structured LLM output, config); plain dataclasses for internal value objects
  (`@dataclass(frozen=True)` for immutable value objects).
- `Any` is a smell: if you cannot name the type, narrow the contract.

## Functions & Structure

- Functions do one thing; keep them under ~50 lines and files under ~800 lines.
- Maximum 4 levels of nesting — use early returns and guard clauses.
- Many small focused modules over few large ones; organize by domain/feature,
  not by technical layer.
- High cohesion, low coupling: a module should be importable without dragging
  in the world.
- Avoid mutating shared state. Build and return new values; pass data in,
  return data out.

## Docstrings & Comments

- Every public module, class, and function carries a docstring: what it does,
  args, returns, raises (Google or NumPy style, consistently).
- Docstrings state the contract; comments explain *why*, never restate *what*.
- Keep docstrings synchronized with the signature — a stale docstring is worse
  than none.

## Error Handling

- Handle errors at the appropriate level; never swallow exceptions silently.
  A bare `except: pass` is always a bug.
- Catch the narrowest exception type that is meaningful (`except ValueError`,
  not `except Exception`).
- Chain exceptions to preserve the traceback when re-raising:

```python
try:
    parsed = json.loads(data)
except json.JSONDecodeError as e:
    raise ValueError(f"Failed to parse data: {data}") from e  # `from e` keeps cause
```

- Define a small custom exception hierarchy per domain so callers can catch
  precisely; see error-handling.md for the full pattern.
- Validate external input at system boundaries and fail fast with clear
  messages; internal functions may then trust their inputs (EAFP with
  explicit failure handling at the edge).
- Log with context (`logger.exception(...)` inside handlers) rather than
  printing.

## Resource & Memory Patterns

- Use `with` / `contextlib` for every resource (files, locks, sessions);
  write custom context managers with `@contextmanager` for paired setup/
  teardown logic.
- Stream large data with generators instead of building full lists in memory:

```python
def read_large_file(path: str) -> Iterator[str]:
    with open(path) as f:
        for line in f:
            yield line.strip()
```

- Prefer comprehensions over imperative accumulation loops when they stay
  readable; switch to a loop when the comprehension needs an `if`/`else`
  expression nested more than once.
- Dataclasses (with validation via pydantic at boundaries) or NamedTuples for
  structured records instead of raw dicts.

## Idiom Checklist

- Use `pathlib.Path`, f-strings, and `contextlib`/`with` for resource safety.
- Never compare to `True`/`False` with `==`; use truthiness or `is None`.
- No magic numbers or strings: lift them to named constants.
- Immutable defaults: never use a mutable value (`[]`, `{}`) as a default
  argument.
- Decorators for cross-cutting concerns (timing, retries, caching) — but
  parameterized decorators need `@functools.wraps` to preserve metadata.

## Review Checklist

- [ ] All public functions typed and documented
- [ ] No bare/broad exception swallowing; `from e` chaining when re-raising
- [ ] Functions and files within size limits; nesting under 4 levels
- [ ] No mutable default arguments or shared mutable state
- [ ] Resources managed with context managers; large data streamed
- [ ] External inputs validated at the boundary with clear failure messages
