---
description: Java review guide — Spring/Quarkus security, Optional discipline, centralized exception handling, JPA pitfalls, and test slicing
tags: java,code-review,spring,quarkus,jpa
origin: ECC (enriched)
---

# Java Code Review Guide

Distilled review guide for Java with Spring Boot and Quarkus rules: security (SQL injection, secrets, validation), layered architecture, JPA/Panache pitfalls, concurrency, and testing standards. Use it when reviewing Java changes for injection risks, swallowed exceptions, `@Transactional` misuse, N+1 queries, and unbounded lists.

## When to Reference

- Reviewing any Java change (`*.java`); detect framework first via `pom.xml`/`build.gradle` (`spring-boot` → SPRING rules, `quarkus` → QUARKUS rules)
- Security: SQL/command injection, secrets, PII logging, input validation, CSRF
- Layered architecture, DI style, `@Transactional` placement
- JPA/Panache persistence, MongoDB, reactive pipelines
- Concurrency, async execution, and test scoping

## CRITICAL — Security

- SQL injection: no string concatenation in queries — bind parameters (`:param`/`?`). Watch `@Query`, `JdbcTemplate`, native queries, Panache customs.
- Command injection: user input into `ProcessBuilder`/`Runtime.exec()`; code injection via `ScriptEngine.eval` — both must be blocked or allowlisted.
- Path traversal: `new File(userInput)`/`Paths.get(userInput)` without `getCanonicalPath()` validation.
- Hardcoded secrets: use env vars, `application.yml`/`application.properties`, or a secrets manager (Vault).
- PII/token logging near auth code.
- Missing validation: raw `@RequestBody`/`@RestForm` without `@Valid`.
- CSRF: disabling needs documented justification; QUARKUS form endpoints need `quarkus-csrf-reactive`.
- Escalate any CRITICAL security finding immediately.

```java
// BAD: SQL injection
String q = "SELECT * FROM users WHERE id = " + userId;

// GOOD: bind parameter
@Query("SELECT u FROM User u WHERE u.id = :id")
Optional<User> findById(@Param("id") Long id);
```

## CRITICAL — Error Handling

- Empty catch blocks or `catch (Exception e) {}` with no action.
- `.get()` on `Optional` without `isPresent()` — use `.orElseThrow()`.
- Centralized handling: SPRING `@RestControllerAdvice`; QUARKUS `@ServerExceptionMapper`/`ExceptionMapper<T>`.
- Correct HTTP statuses: 404 not 200-with-null; 201 on creation.

```java
// BAD: NoSuchElementException on missing row
User u = repository.findById(id).get();

// GOOD
User u = repository.findById(id).orElseThrow(() -> new NotFoundException(id));
```

## HIGH — Architecture

- Field `@Autowired` is a code smell — constructor injection (SPRING); `@Inject`/constructor injection (QUARKUS).
- QUARKUS: prefer `@ApplicationScoped` over `@Singleton` (no proxying/interception with `@Singleton`).
- Business logic must delegate to the service layer, not controllers/resources.
- `@Transactional` on the service layer only; add `readOnly = true` for reads (SPRING); Panache mutating calls require a transaction (QUARKUS).
- Never return JPA/Panache entities from controllers — use DTOs/records.
- QUARKUS reactive: no blocking I/O on reactive threads — use `@Blocking` or reactive clients.

## HIGH — Persistence

- N+1 queries: `FetchType.EAGER` on collections — use `JOIN FETCH` or `@EntityGraph`.
- Unbounded list endpoints: paginate (`Pageable`/`Page<T>`, `PanacheQuery.page(...)`; MongoDB `.page(Page.of(...))`).
- Mutating `@Query` needs `@Modifying` + `@Transactional`.
- `CascadeType.ALL` with `orphanRemoval = true` must be deliberate.
- MongoDB: codecs for custom types, indexes on queried fields, no large blobs in documents (16 MB limit), TTL for time-sensitive data, explicit `ClientSession` for multi-document transactions.

## MEDIUM — Concurrency and State

- Mutable non-final fields in singleton beans (`@Service`/`@ApplicationScoped`) are race conditions.
- Unbounded async: `CompletableFuture`/`@Async` without a custom Executor; use managed `ManagedExecutor` (QUARKUS).
- `@Scheduled` methods must not block long; QUARKUS: `concurrentExecution = SKIP`.
- Idempotency keys checked before state mutation; guard illegal state transitions (e.g. `CANCELLED → PROCESSING`); non-atomic compensation is a defect.
- Retries need exponential backoff + jitter; async events need dead-letter handling.

## MEDIUM — Idioms and Performance

- No string concatenation in loops — `StringBuilder`/`String.join`.
- No raw types (`List` → `List<T>`); use pattern matching for `instanceof`+cast (Java 16+).
- Return `Optional<T>`, never null, from the service layer; callers chain
  `map`/`flatMap`/`orElseThrow` — never call `.get()`:

```java
// GOOD: Optional as a return contract, transformed functionally
return marketRepository.findBySlug(slug)
    .map(MarketResponse::from)
    .orElseThrow(() -> new MarketNotFoundException(slug));
```

- Domain-specific unchecked exceptions (`MarketNotFoundException`) with
  centralized translation to HTTP responses — SPRING `@RestControllerAdvice`
  with `@ExceptionHandler`, QUARKUS `ExceptionMapper<T>`; broad
  `catch (Exception ex)` only in that central layer.

## Testing Standards

- Right-size test annotations: SPRING — `@WebMvcTest`/`@DataJpaTest` for slices, not blanket `@SpringBootTest`; QUARKUS — plain JUnit 5 + Mockito for units, `@QuarkusTest` for integration, Testcontainers/`@QuarkusTestResource` for external services.
- Async assertions via Awaitility — no `Thread.sleep()` in tests.
- Descriptive names: `should_return_404_when_user_not_found`, not `testFindUser`.

## Tooling Gates

```bash
./mvnw verify -q || ./gradlew check    # build + tests must pass
./mvnw spotbugs:check checkstyle:check # static analysis
./mvnw dependency-check:check          # CVE scan
```

## Approval Criteria

- **Approve**: no CRITICAL or HIGH issues. **Warning**: MEDIUM only. **Block**: any CRITICAL or HIGH issue.
