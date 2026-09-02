---
description: C++ review guide — memory safety (RAII, smart pointers), Core Guidelines rules, exception discipline, sanitizers, and testing gates
tags: cpp,code-review,memory-safety,raii,concurrency
origin: ECC (enriched)
---

# C++ Code Review Guide

Distilled review guide for C++ memory safety, modern C++ idioms, concurrency, and performance. Use it when reviewing C++ changes for raw `new`/`delete`, buffer overflows, use-after-free, data races, and Rule of Five violations.

## When to Reference

- Reviewing any C++ change (`.cpp`, `.cc`, `.cxx`, `.hpp`, `.hh`, `.h`)
- Memory safety: ownership, RAII, smart pointers, dangling pointers
- Security: injection, format strings, integer overflow, unsafe casts
- Concurrency: threads, mutexes, lock ordering
- Modernization: replacing C-style constructs with C++17 idioms

## CRITICAL — Memory Safety

- Raw `new`/`delete` are defects — use `std::unique_ptr`/`std::shared_ptr` (and `make_unique`/`make_shared`).
- Buffer overflows: C-style arrays, `strcpy`, `sprintf`, `gets` without bounds — use `std::string`, `std::array`, `std::copy`.
- Use-after-free: dangling pointers, invalidated iterators (e.g. `erase` during iteration).
- Uninitialized variables read before assignment; null dereference without a check.
- Resources must be tied to object lifetime (RAII) — no manual acquisition/release pairs.

```cpp
// BAD: leak-prone manual ownership
Widget* w = new Widget();
process(w);
delete w;

// GOOD: RAII ownership
auto w = std::make_unique<Widget>();
process(*w); // freed automatically, even on exception
```

## CRITICAL — Security

- Command injection: unvalidated input to `system()`/`popen()` — avoid or strictly allowlist.
- Format string attacks: user input must never be the `printf` format string.
- Integer overflow on untrusted input: check ranges or use checked arithmetic before sizing allocations/loops.
- Hardcoded secrets in source.
- `reinterpret_cast` without written justification.

```cpp
// BAD: user input as format string
printf(userInput);

// GOOD: input as data
printf("%s", userInput.c_str());
```

## HIGH — Exception Discipline (Core Guidelines E.*)

- Throw by value, catch by reference (`catch (const NetworkError&)`); use
  purpose-designed user-defined exception types, not bare `throw std::string`.
- Exception hierarchy per domain: a small base (`AppError : std::runtime_error`)
  with typed subclasses carrying context (status codes, offending values).
- `noexcept` when throwing is impossible or unacceptable (E.12); destructors,
  deallocation, and `swap` must never fail (E.16) — no throwing work inside.
- Don't catch-and-swallow at every level (E.17): handle where meaningful,
  otherwise let it propagate. Translate at layer boundaries, preserving cause.

```cpp
// E.14 + E.15: typed exceptions, catch by const reference
class NetworkError : public AppError {
public:
    NetworkError(std::string msg, int code) : AppError(std::move(msg)), status_code(code) {}
    int status_code;
};
```

## HIGH — Concurrency

- Data races: shared mutable state without synchronization — mutex, atomic, or confinement.
- Deadlocks: consistent global lock ordering; prefer `std::scoped_lock` for multiple mutexes.
- Manual `lock()`/`unlock()` — use `std::lock_guard`/`std::unique_lock` (exception-safe).
- Every `std::thread` must be `join()`ed or `detach()`ed before destruction; detached threads are usually a defect.

```cpp
// BAD: manual unlock missed on exception, unordered multi-lock
m.lock(); counter++; m.unlock();

// GOOD: RAII guards
{ std::scoped_lock lk(m1, m2); ++counter; }
```

## HIGH — Code Quality

- No RAII = defect: file handles, sockets, locks must be wrapped in owning objects.
- Rule of Five: if a class declares a destructor, copy/move constructor, or assignment, review all five (or delete them). Rule of Zero preferred where possible.
- Functions > 50 lines or nesting > 4 levels — flag for decomposition.
- C-style leftovers: `malloc`, C arrays, `#define` constants, `typedef` — use `new`-free C++, `std::array`/`std::vector`, `constexpr`, `using`.

```cpp
// BAD: incomplete special members → double free on copy
struct Buf { char* data; ~Buf() { delete[] data; } };

// GOOD: Rule of Zero with owned container
struct Buf { std::vector<char> data; };
```

## MEDIUM — Performance

- Pass large objects by `const&`, not by value; use `std::move` for sink parameters and expired locals.
- String building in loops: use `std::ostringstream` or `reserve()`; pre-size vectors with `reserve()` when the size is known.

```cpp
// BAD: copies + repeated reallocation
std::string s;
for (auto& p : parts) s = s + p;

// GOOD
std::string s; s.reserve(total);
for (auto& p : parts) s += p;
```

## MEDIUM — Best Practices

- `const` correctness: const methods, const reference parameters, `constexpr` where possible.
- `auto` balanced: use for obvious iterators/long types, not where type obscures intent.
- Include hygiene: guards/`#pragma once`, no unnecessary includes; forward-declare in headers where viable.
- Never `using namespace std;` in headers.

## Testing and Tooling Gates

```bash
clang-tidy --checks='*,-llvmlibc-*' src/*.cpp -- -std=c++17  # must be clean
cppcheck --enable=all --suppress=missingIncludeSystem src/
cmake --build build                                          # builds warning-free
```

- Tests: unit tests alongside code (gtest/catch2), cover error paths and ownership transfer (moves, copies, destruction order).
- Concurrency code requires stress tests (thread sanitizer: `-fsanitize=thread`); memory bugs need `-fsanitize=address` runs in CI.
- Sanitizer matrix as CMake options, enabled per CI job: `-fsanitize=address`
  (heap/stack/use-after-free), `-fsanitize=undefined` (UB), `-fsanitize=thread`
  (data races) — each with `-fno-omit-frame-pointer` for readable traces.
- Flaky-test guardrail: a test that fails intermittently under TSan or with
  `-DENABLE_TSAN=ON` indicates a real race — fix the code, never delete the test.

## Approval Criteria

- **Approve**: no CRITICAL or HIGH issues. **Warning**: MEDIUM only. **Block**: any CRITICAL or HIGH issue.
