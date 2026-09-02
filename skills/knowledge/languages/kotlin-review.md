---
description: Kotlin review guide — coroutine/Flow safety, sealed hierarchies, scope-function discipline, Compose recomposition, and Android security
tags: kotlin,code-review,coroutines,compose,android
origin: ECC (enriched)
---

# Kotlin Code Review Guide

Distilled review guide for Kotlin and Android/KMP: coroutine/Flow safety, clean-architecture boundaries, Compose recomposition traps, and idiomatic Kotlin. Use it when reviewing Kotlin changes for cancellation bugs, framework leakage into the domain layer, exported-component security, and `!!`/mutable-state defects.

## When to Reference

- Reviewing any Kotlin change (`.kt`, `.kts`), Android or KMP
- Coroutine scope/structured-concurrency and Flow collection issues
- Clean architecture: domain/data/presentation module boundaries
- Jetpack Compose performance and recomposition
- Android security: exported components, crypto/storage, WebView, logging

## CRITICAL — Security

- Exported Activities/Services/Receivers without guards; unvetted deep links and intent filters.
- Homegrown crypto, plaintext secrets, or weak keystore usage.
- Unsafe WebView: JavaScript bridges, cleartext traffic, permissive trust settings.
- Sensitive logging: tokens, credentials, or PII emitted to logs.
- Stop and escalate any CRITICAL security finding before further review.

## CRITICAL — Architecture Boundaries

- Domain module must not import Android, Ktor, Room, or any framework.
- Data-layer entities/DTOs must not leak to UI — map to domain models.
- Business logic belongs in UseCases, not ViewModels.
- No circular module dependencies.

```kotlin
// BAD: framework import in domain
// domain/src/.../UserUseCase.kt
import android.content.Context

// GOOD: domain is pure Kotlin; dependencies via interfaces
class UserUseCase(private val repo: UserRepository)
```

## HIGH — Coroutines and Flows

- No `GlobalScope` — use `viewModelScope` / `coroutineScope` (structured concurrency).
- Never swallow `CancellationException` — rethrow it before catching `Exception`.
- IO/network calls must run in `withContext(Dispatchers.IO)`, never on `Dispatchers.Main`.
- Parallel calls via `coroutineScope { async { ... } }`; use `supervisorScope`
  when children fail independently (catch per-child, rethrowing
  `CancellationException` each time):

```kotlin
suspend fun fetchDashboard(userId: String): Dashboard = supervisorScope {
    val user = async { userService.getUser(userId) }
    val notifications = async { notificationService.getRecent(userId) }
    Dashboard(
        user = user.await(),
        notifications = try { notifications.await() }
        catch (e: CancellationException) { throw e }
        catch (e: Exception) { emptyList() },
    )
}
```

- StateFlow must not hold mutable collections — emit immutable copies (`copy` / `update {}`).
- No Flow collection in `init {}` — use `stateIn()` with `SharingStarted.WhileSubscribed`.

```kotlin
// BAD: swallows cancellation, breaks structured concurrency
try { fetchData() } catch (e: Exception) { log(e) }

// GOOD: rethrow cancellation
try { fetchData() }
catch (e: CancellationException) { throw e }
catch (e: Exception) { log(e) }
```

```kotlin
// BAD: mutation inside StateFlow is undetected by Compose
_state.value.items.add(newItem)

// GOOD: immutable update
_state.update { it.copy(items = it.items + newItem) }
```

## HIGH — Compose

- Unstable/mutable parameters cause unnecessary recomposition — prefer immutable data classes.
- Side effects (network/DB) only in `LaunchedEffect` or ViewModel — never in composition.
- Pass lambdas, not `NavController`, deep into the tree.
- `LazyColumn` items need stable `key(...)`; `remember` must depend on its keys.
- Avoid allocating objects/lambdas inline in composable parameters.

```kotlin
// BAD: new lambda every recomposition
Button(onClick = { viewModel.doThing(item.id) })

// GOOD: stable reference keyed on inputs
val onClick = remember(item.id) { { viewModel.doThing(item.id) } }
Button(onClick = onClick)
```

## MEDIUM — Kotlin Idioms

- No `!!` — use `?.`, `?:`, `requireNotNull`, or `checkNotNull`.
- Prefer `val` over `var`; expose `List`, not `MutableList`, from public APIs.
- No Java-style getters/setters — use properties; no static utility classes — use top-level functions.
- String templates `"Hello $name"` over concatenation.
- `when` over sealed classes must be exhaustive (no `else` fallback for business logic):

```kotlin
// GOOD: sealed hierarchy + exhaustive when
sealed class Result<out T> {
    data class Success<T>(val data: T) : Result<T>()
    data class Failure(val error: AppError) : Result<Nothing>()
    data object Loading : Result<Nothing>()
}

fun <T> Result<T>.getOrNull(): T? = when (this) {
    is Result.Success -> data
    is Result.Failure -> null
    is Result.Loading -> null       // compiler enforces: add a case → compile error here
}
```

- Scope functions by intent: `let` transform nullable, `apply` configure
  object (returns receiver), `also` side effects, `run`/`with` compute a
  result — don't nest them; a chain of mixed scope functions is unreadable.

## MEDIUM — Android Specific

- No `Activity`/`Fragment` references in singletons/ViewModels (context leaks).
- Serialized classes need `@Keep` or ProGuard rules.
- User-facing strings in `strings.xml`/Compose resources, not hardcoded.
- Collect Flows in lifecycle owners with `repeatOnLifecycle`, never bare `collect` in `onCreate`.

## Testing Standards

- Unit-test UseCases/ViewModels with plain JUnit + coroutine test dispatchers (`runTest`).
- Flow tests use `Turbid/turbine`-style collectors or `first()`/`toList()` assertions.
- ViewModel tests assert state transitions (`StateFlow` values), not internal calls only.
- Android UI tests: Compose testing APIs or Espresso; keep them out of unit suites.

## Approval Criteria

- **Approve**: no CRITICAL or HIGH issues.
- **Block**: any CRITICAL or HIGH issue — must fix before merge.
- Report findings with file:line, issue, and suggested fix; only report issues with >80% confidence.
