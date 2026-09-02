---
description: TypeScript review guide — type safety, async correctness, immutability, fetch error contracts, and Node.js security
tags: typescript,code-review,async,security,immutability
origin: ECC (enriched)
---

# TypeScript Code Review Guide

Distilled review guide for TypeScript/JavaScript: type safety, async correctness, Node/web security, and idiomatic patterns. Use it when reviewing TS/JS changes for injection and XSS risks, `any`/unsafe-cast abuse, floating promises, swallowed errors, and event-loop-blocking I/O.

## When to Reference

- Reviewing any TypeScript/JavaScript change (`.ts`, `.tsx`, `.js`, `.jsx`)
- Type-safety questions: `any`, `unknown`, non-null assertions, `as` casts, tsconfig strictness
- Async correctness: promises, `await` in loops, `forEach(async ...)`, error handling
- Node.js security: injection, path traversal, secrets, prototype pollution
- Performance and bundle-size concerns

## CRITICAL — Security

- Never execute untrusted strings: `eval`, `new Function` — reject outright.
- XSS: unsanitized input into `innerHTML`, `dangerouslySetInnerHTML`, `document.write`.
- SQL/NoSQL injection: string concatenation in queries — parameterize or use an ORM.
- Path traversal: user input into `fs.readFile`/`path.join` without `path.resolve` + prefix validation.
- Hardcoded secrets: must come from environment variables.
- Prototype pollution: merging untrusted objects without schema validation or `Object.create(null)`.
- `child_process` with user input: validate and allowlist before `exec`/`spawn`.

```ts
// BAD: injection + hardcoded secret
db.query(`SELECT * FROM users WHERE id = ${req.params.id}`);
const key = "sk_live_abc123";

// GOOD: parameterized, secret from env
db.query("SELECT * FROM users WHERE id = $1", [req.params.id]);
const key = process.env.STRIPE_KEY;
```

## HIGH — Type Safety

- `any` without justification is a defect — use `unknown` and narrow, or a precise type.
- Non-null assertion `value!` without a preceding runtime guard.
- `as` casts that bypass checks (casting to unrelated types to silence errors) — fix the type instead.
- Weakening tsconfig (disabling `strict`, widening `any` defaults) must be flagged explicitly.
- Public functions should have explicit return types; avoid implicit `any`.

```ts
// BAD: lies to the compiler
const user = await res.json() as User;
const name = (data as any).name!;

// GOOD: validate at boundary
const user = UserSchema.parse(await res.json()); // zod
```

## HIGH — Async Correctness

- Unhandled rejections: async calls without `await` or `.catch()` (floating promises).
- `forEach(async fn)` does not await — use `for...of` or `Promise.all`.
- Sequential `await` in loops for independent work — use `Promise.all`.
- Fire-and-forget calls in event handlers/constructors need explicit error handling.

```ts
// BAD: not awaited; serial for independent calls
items.forEach(async i => save(i));
const a = await fetchA(); const b = await fetchB();

// GOOD: parallel + awaited
await Promise.all(items.map(i => save(i)));
const [a, b] = await Promise.all([fetchA(), fetchB()]);
```

## HIGH — Error Handling

- Empty `catch` blocks or `catch (e) {}` with no action.
- `JSON.parse` without try/catch — always wrap or validate with a schema.
- Throw `new Error("message")`, never bare strings or objects.
- React trees without `<ErrorBoundary>` around async/data-fetching subtrees.

```ts
// BAD: silent swallow, throws string
try { cfg = JSON.parse(raw); } catch {}
throw "invalid config";

// GOOD: wrap with context, throw Error
try { cfg = JSON.parse(raw); } catch (e) {
  throw new Error(`invalid config: ${String(e)}`);
}
```

## HIGH — Node.js Specifics

- `fs.readFileSync` (and other sync calls) in request handlers blocks the event loop — use async variants.
- External input without schema validation at boundaries (zod/joi/yup).
- `process.env.X` accessed without fallback or startup validation.
- Mixing `require()` into ESM without clear intent.

## MEDIUM — Idiomatic Patterns

- `const` by default, `let` only on reassignment, never `var`; `===` throughout.
- Immutability: spread to update, never mutate in place —

```ts
// BAD: direct mutation (breaks React state, shared refs)
user.name = 'New Name';
items.push(newItem);

// GOOD: new objects/arrays
const updatedUser = { ...user, name: 'New Name' };
const updatedArray = [...items, newItem];
```

- Naming: camelCase variables/functions, PascalCase types/classes; functions
  read as verb-noun (`fetchMarketData`, `isValidEmail`) — not bare nouns.
- Check HTTP status before parsing: `if (!response.ok) throw new Error(\`HTTP ${response.status}\`)` —
  `fetch` does not reject on 4xx/5xx, so unhandled non-OK responses flow into
  `.json()` and fail with a confusing parse error.
- No module-level mutable shared state — prefer immutable data and pure functions.
- Standardize on `async/await`; don't mix callback style in new code.
- No `console.log` in production — structured logger; no magic numbers — named constants.
- Deep optional chaining `a?.b?.c?.d` without `?? fallback` hides failures.

## MEDIUM — React/Next.js (when applicable)

- Incomplete dependency arrays on `useEffect`/`useCallback`/`useMemo` (exhaustive-deps).
- Direct state mutation instead of new objects; `key={index}` on dynamic lists.
- Derived state computed in effects instead of during render.
- Importing server-only modules into client components (RSC boundary leak).
- Inline objects/arrays as props causing re-renders — hoist or memoize.

## Testing Standards

- Vitest (or Jest `--ci`) must pass before approval; no skipped tests in a PR.
- Validate external data in tests the same way production does (schema-first).
- Prefer testing behavior through the public API; mock I/O boundaries only.
- Coverage of error paths (rejected promises, invalid input) is expected, not just the happy path.

## Tooling Gates

```bash
npm run typecheck --if-present   # or: tsc --noEmit -p <relevant tsconfig>
eslint . --ext .ts,.tsx,.js,.jsx # lint must pass
npm audit                        # dependency CVE scan
vitest run                       # tests must pass
```

## Approval Criteria

- **Approve**: no CRITICAL or HIGH issues.
- **Warning**: MEDIUM only (merge with caution).
- **Block**: any CRITICAL or HIGH issue.
