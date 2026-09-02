---
description: Rust review guide — panic prevention, thiserror/anyhow error design, unsafe discipline, async correctness, and testing standards
tags: rust,code-review,error-handling,unsafe,async
origin: ECC (enriched)
---

# Rust Code Review Guide

Distilled review guide for idiomatic Rust: ownership/borrowing, `Result`/`?` error handling, safe concurrency, and testing standards. Use it when reviewing Rust changes for memory safety, error-propagation conventions, and common high-severity defects like `unwrap()` panics and `unsafe` misuse.

## When to Reference

- Reviewing any Rust source change (`*.rs`, Cargo.toml)
- Questions about ownership, borrowing, lifetimes, or cloning
- Library vs application error-handling design (`thiserror` vs `anyhow`)
- Concurrency: threads, `Arc<Mutex<T>>`, channels, async/Tokio
- Reviewing `unsafe` blocks or Rust tests

## CRITICAL — Panics and Error Handling

- Never `unwrap()`/`expect()` in production paths; propagate with `?` and context.
- Libraries define typed errors (`thiserror`); apps use `anyhow::Result`.
- Do not silently discard `Result` values (`let _ = validate(...)`).
- `Option` handling via combinators, not nested matches.

```rust
// Library code: structured, typed errors (thiserror)
#[derive(Debug, Error)]
pub enum StorageError {
    #[error("record not found: {id}")]
    NotFound { id: String },
    #[error("connection failed")]
    Connection(#[from] std::io::Error),   // `#[from]` enables `?` auto-conversion
    #[error("invalid data: {0}")]
    InvalidData(String),
}
```

- Mutex poisoning: `lock().expect("mutex poisoned")` is acceptable — a poisoned
  mutex means another thread panicked while holding it, which is itself a bug.

```rust
// BAD: panics on missing file or bad TOML
let cfg: Config = toml::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();

// GOOD: propagate with context
let content = std::fs::read_to_string(path)
    .with_context(|| format!("failed to read config from {path}"))?;
let cfg: Config = toml::from_str(&content)
    .with_context(|| format!("failed to parse config from {path}"))?;
```

## CRITICAL — Unsafe Code

- `unsafe` must have a `// SAFETY:` comment justifying every invariant.
- Never use `unsafe` to bypass the borrow checker or for convenience.
- Never `mem::transmute` between unrelated types.
- FFI boundaries and proven performance paths are the only acceptable uses.

```rust
// BAD: no justification, unsound for arbitrary input
let n = unsafe { *slice.get_unchecked(user_index) };

// GOOD: documented invariant (index validated < len earlier)
// SAFETY: caller guarantees ptr is valid, aligned, and initialized.
let w = unsafe { &*ptr };
```

## HIGH — Ownership, Borrowing, Cloning

- Pass `&T`/`&[u8]`/`&str` unless ownership is required; take `Vec<u8>`/`String` only when storing/consuming.
- Flag `&Vec<T>`/`&String` parameters — use `&[T]`/`&str`.
- Flag `.clone()` used purely to satisfy the borrow checker without explanation.
- Prefer `Cow<'_, str>` when mutation is conditional.
- Newtypes (`UserId(u64)`) to prevent swapping primitive arguments.

```rust
// BAD: clones, and takes over-specific parameter types
fn process(data: &Vec<u8>) -> usize { let c = data.clone(); c.len() }

// GOOD: borrow, minimal type
fn process(data: &[u8]) -> usize { data.len() }
```

## HIGH — Concurrency and Async

- Shared mutable state via `Arc<Mutex<T>>`; lock guard scope kept minimal.
- Bounded channels for backpressure; drop senders so receivers terminate.
- Never block in async contexts: `std::thread::sleep` in async fn is a defect — use `tokio::time::sleep().await`.
- Wrap network calls in `tokio::time::timeout`; handle `JoinError` from spawned tasks.

```rust
// BAD: blocks the executor thread
async fn work() { std::thread::sleep(Duration::from_secs(1)); }

// GOOD: async-aware sleep
async fn work() { tokio::time::sleep(Duration::from_secs(1)).await; }
```

## HIGH — Modeling and Exhaustiveness

- Model states as enums; illegal states must be unrepresentable.
- `match` on business-critical enums must be exhaustive — no wildcard `_ => {}` that silently ignores future variants.
- Prefer iterator chains over manual loops; annotate `collect()` target types.
- Builders for structs with many fields.

```rust
// BAD: wildcard hides new variants
match cmd { Command::Start => start(), _ => {} }

// GOOD: exhaustive; compiler forces handling of new variants
match cmd {
    Command::Start => start(),
    Command::Stop => stop(),
    Command::Restart => restart(),
}
```

## MEDIUM — API Surface and Structure

- Minimal `pub`: internal helpers should be `pub(crate)` or private.
- Organize modules by domain, not by type; re-export the public API from `lib.rs`.
- Accept generics (`impl Read`), return concrete types; `Box<dyn Error>` is wrong in libraries.
- `#[must_use]` on fallible return types; respect the linter's warnings.

## Testing Standards

- Unit tests in `#[cfg(test)] mod tests` next to the code; integration tests in `tests/`.
- TDD expected: failing test first (`todo!()` placeholder), then minimal implementation.
- Assert error variants explicitly: `assert!(matches!(err, ConfigError::ParseError(_)))`.
- Test panics with `#[should_panic]` (and expected message); return `Result<(), Box<dyn Error>>` from tests that use `?`.
- Parameterized tests with `rstest`; property tests with `proptest`; mocks with `mockall`.
- Coverage via `cargo-llvm-cov`, target 80%+; float comparisons use epsilon, not `==`.

```rust
// GOOD: explicit error-path coverage
#[test]
fn rejects_invalid_email() {
    let err = User::new("Bob", "not-an-email").unwrap_err();
    assert!(err.contains("invalid email"));
}
```

## Tooling Gates

```bash
cargo clippy -- -D warnings   # must pass
cargo fmt --check             # must pass
cargo audit                   # no known CVEs in deps
cargo test --all-features
```
