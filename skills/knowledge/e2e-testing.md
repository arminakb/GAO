---
description: E2E testing with Playwright — flow selection, page objects, selectors, wait discipline, flake prevention, and CI configuration
tags: e2e,playwright,testing,flaky,ci
origin: ECC (enriched)
---

# End-to-End Testing with Playwright

E2E tests verify complete user journeys through the real running application
— UI, API, and persistence together. They are expensive and flake-prone, so
they are reserved for the critical flows that must never break, written with
resilient selectors and strict wait discipline.

## When to Reference

- Writing or reviewing Playwright test suites
- Deciding which flows deserve E2E coverage vs unit/integration tests
- Debugging flaky, timing-sensitive browser tests
- Setting selector and wait strategies for UI automation

## What Deserves E2E (and What Doesn't)

Cover the flows whose failure would be a production incident:
- Authentication (login, logout, session persistence)
- Core value path (the one thing the product exists to do, end to end)
- Data submission with persistence (create → reload → verify)
- Payment/permission boundaries

Do NOT use E2E for: input validation edge cases, business-rule matrices,
component styling, error-message text — these are faster and more reliable as
unit/integration tests. E2E suites grow expensive; a thin layer over a deep
unit suite wins.

## Page Object Pattern

Encapsulate each page/screen in a class exposing actions and assertions;
tests never touch selectors directly.

```python
class LoginPage:
    def __init__(self, page: Page) -> None:
        self.page = page
        self.email = page.get_by_label("Email")
        self.submit = page.get_by_role("button", name="Sign in")

    def login(self, user: str, password: str) -> None:
        self.email.fill(user)
        self.password = self.page.get_by_label("Password")
        self.password.fill(password)
        self.submit.click()
```

Fixtures construct page objects; a test reads as a user journey, not DOM
manipulation.

## Selector Strategy (resilience order)

1. `get_by_role(...)` — accessibility roles (best; ties tests to a11y)
2. `get_by_label` / `get_by_text` — user-visible semantics
3. `data-testid` attributes — stable, explicit test hooks
4. NEVER: generated CSS classes, positional `nth()`, brittle XPath tied to
   layout structure.

## Wait & Flake Discipline

- Rely on auto-waiting assertions (`expect(locator).to_be_visible()`,
  `to_have_text`) — Playwright polls until timeout.
- NEVER `time.sleep()` or fixed delays; they are both slow and flaky.
- Wait for network idleness or specific responses when a request must finish
  (`page.wait_for_url`, `expect_response`).
- One logical journey per test; each starts from a known state (seeded DB or
  API-created fixtures — not other tests' leftovers).
- Capture screenshots, console logs, and network traces on failure for
  diagnosis.

## Configuration & CI

```typescript
export default defineConfig({
  fullyParallel: true,
  retries: process.env.CI ? 2 : 0,       // retry only in CI, never locally
  workers: process.env.CI ? 1 : undefined, // serialize in CI to kill order flakes
  use: {
    baseURL: process.env.BASE_URL || 'http://localhost:3000',
    trace: 'on-first-retry',             // trace on retry = free diagnosis
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    actionTimeout: 10000,
    navigationTimeout: 30000,
  },
  webServer: { command: 'npm run dev', url: 'http://localhost:3000',
               reuseExistingServer: !process.env.CI },
})
```

- `retries: 2` masks genuine flakes — treat any test that passes only on
  retry as failing and file it for fix (retries buy CI stability, not truth).
- Artifacts (screenshot, trace, video) are failure-only; `npx playwright
  show-trace <file>` replays them.

## Common Causes & Fixes

| Cause | BAD | GOOD |
|---|---|---|
| Race on element | `page.click(sel)` then hope | locator auto-wait: `page.locator(sel).click()` |
| Network timing | `waitForTimeout(5000)` | `waitForResponse(r => r.url().includes('/api/data'))` |
| Animation | click mid-animation | `waitFor({ state: 'visible' })` + `waitForLoadState('networkidle')` |
| Shared state | tests reuse DB rows | API-created fixtures per test, unique identifiers |
| Order dependence | test B needs test A's rows | each test seeds its own state |

Diagnosis drill: run the failing test alone → with workers=1 → with retries=0.
If it passes alone but fails in parallel, it's shared state; if it fails
alone, it's a wait/selector bug or a real product race.

## Review Checklist

- [ ] Only critical flows covered; the rest pushed down to unit/integration
- [ ] Page objects used; no raw selectors in test bodies
- [ ] Role/label/testid selectors only; no brittle XPath or classes
- [ ] Zero fixed sleeps; assertions auto-wait
- [ ] Tests independent, starting from deterministic seeded state
