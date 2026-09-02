---
description: React/Next.js frontend patterns — composition, hooks, state, RSC data fetching, testing with RTL/MSW/axe, and performance gotchas
tags: react, frontend, typescript, testing, performance, accessibility
origin: ECC (enriched)
---

# Frontend Development Patterns

Modern React/Next.js patterns: composition, reusable hooks, predictable
state, RSC data fetching, measurable performance work, and behavior-focused
testing. Pairs with `e2e-testing.md` for browser flows.

## When to Reference

- Building React components (composition, props, rendering)
- Managing state (useState, useReducer, Zustand, Context)
- Deciding data fetching and server/client boundaries (RSC)
- Writing component tests (React Testing Library, MSW, axe)
- Optimizing performance (memoization, virtualization, code splitting)
- Building accessible, keyboard-navigable UI patterns

## Component Patterns: Composition Over Inheritance

Compose small pieces instead of configuring a monolith: `<Card>` accepts
`children` (plus a `variant` prop) and renders `<CardHeader>`/`<CardBody>`
inside — cards, layouts, and slots are built from `children` and component
props, never inheritance. **Compound components** share hidden context
(Context provider wraps children; each child `useContext`es it and throws if
missing) so children cooperate without prop drilling — e.g.
`<Tabs><Tab id="a"/></Tabs>`.

## Custom Hooks

Extract stateful logic into hooks with stable contracts. The pitfall: inline
fetchers/options change identity every render, retriggering effects in an
infinite loop — keep latest values in refs so `refetch` stays stable:

```typescript
export function useQuery<T>(key: string, fetcher: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [loading, setLoading] = useState(false)
  const fetcherRef = useRef(fetcher)   // latest fetcher, stable identity
  useEffect(() => { fetcherRef.current = fetcher })
  const refetch = useCallback(async () => {
    setLoading(true); setError(null)
    try { setData(await fetcherRef.current()) }
    catch (err) { setError(err as Error) }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { refetch() }, [key, refetch])
  return { data, error, loading, refetch }
}
```

Also in the kit: `useDebounce` — debounce the value, not the fetch call.
**Hook discipline**: top-level only; clean up every subscription/listener;
functional updaters when new state depends on old; extract a hook only when
the same sequence appears in 2+ components; don't memoize until a profiler
proves it.

## State Management: Context + Reducer

For domain state that crosses many components, a typed reducer inside
Context beats prop drilling and ad-hoc `useState` nests:

```typescript
type Action =
  | { type: 'SET_ITEMS'; payload: Item[] }
  | { type: 'SELECT_ITEM'; payload: Item }
  | { type: 'SET_LOADING'; payload: boolean }
// reducer: switch on action.type, return { ...state, ... } — never mutate
```

Rules: exhaustive `Action` unions; a custom hook (`useItems`) wraps
`useContext` and throws outside the Provider; updates produce new objects.
| State location | When |
|---|---|
| `useState` in the component | Used by one component |
| Lift to nearest common ancestor | Parent + a few descendants |
| React Context | Distant branches, low-frequency reads (theme, auth) |
| External store (Zustand) | High-frequency updates shared across the tree |
| TanStack Query / SWR | Server-derived data |

Most pages need none of the global options — resist abstraction until
duplicated lifting hurts. Split context per concern so a theme change doesn't
re-render auth consumers.

## Server Components & Data Fetching (RSC)

| Need | Tool |
|---|---|
| Per-request data in Next.js App Router | RSC `await fetch()` |
| Client-side cache + mutations + invalidation | TanStack Query |
| Lightweight client cache + revalidation | SWR |
| Real-time subscriptions | SSE / WebSockets / lib subscription API |
| One-off fire-and-forget | `fetch()` in an event handler |

```typescript
// Server Component — default, async, ships no JS for itself
export default async function ProductPage({ params }: { params: { id: string } }) {
  const product = await db.product.findUnique({ where: { id: params.id } });
  if (!product) notFound();
  return <ProductView product={product} />;  // client islands via "use client"
}
```
Server → Client passes serializable props or `children`; never `import` a
Server Component from a Client Component file — compose via `children`. Avoid
`useEffect` + `fetch` for application data (races, no cache, no retry). Place
`<Suspense>` close to the data, not at the route root, and reserve space
(skeleton/`min-height`) to avoid layout shift. Wrap each async tree in an
error boundary (below). Authenticate and authorize inside Server Actions —
every `"use server"` function is a public endpoint; the calling component's
gating is not security.

## Performance Optimization

Measure before optimizing. The standard moves, in order of impact:

```typescript
const sorted = useMemo(() => [...items].sort((a, b) => b.volume - a.volume), [items]) // 1. memoize expensive computes
const handleSearch = useCallback((q: string) => setSearchQuery(q), [])               // 2. stabilize callbacks
const HeavyChart = lazy(() => import('./HeavyChart'))                                // 3. lazy-load heavy subtrees
const virtualizer = useVirtualizer({ count: items.length,                            // 4. virtualize long lists
  getScrollElement: () => parentRef.current, estimateSize: () => 100, overscan: 5 })
```

Do not memoize blindly: `useMemo`/`useCallback` have their own cost. Profile
first; memoize what the profiler flags. Further gotchas, by priority:

| Gotcha | Fix |
|---|---|
| Sequential awaits on independent data (waterfalls — #1 killer) | `Promise.all`, or split into sibling Server Components (parallel by composition) |
| Await before a cheap sync guard | `if (!id) return null` first; defer awaits into the using branch |
| Barrel-file imports bloat first-load JS | Direct imports: `@/components/Button`, not `@/components` |
| New object/array prop identity breaks `memo` | Hoist defaults: `const EMPTY: Item[] = []` outside the component |
| New object in effect deps re-fires every render | Depend on primitives `[id, name]`, not `[{id, name}]` |
| Derived state in `useEffect` (extra render, desync) | Derive during render, not in an effect |
| Component defined inside a component | New type every render — unmounts children; define at module level |
| Costly urgent updates (filters, search) | `startTransition` for non-urgent, `useDeferredValue` for expensive renders |
| `0 && <Badge/>` renders "0" | Ternary: `{count > 0 ? <Badge/> : null}` |
| Long offscreen lists render anyway | CSS `content-visibility: auto` + `contain-intrinsic-size` |
| Missing/index `key`s on lists | Stable db ids; virtualize past ~50 rows |

Web Vitals mapping: LCP ← waterfalls/bundle size; INP ← re-renders/JS work; CLS ← Suspense placement/images; TBT ← third-party deferral.

## Form Handling

Controlled inputs with a typed form model and **centralized, pure validation**
(a `validate()` returning an error map — none scattered in `onChange`; callers
own success/error UX). Multi-step forms, dynamic field arrays, or cross-field
validation → a library (React Hook Form, TanStack Form); React 19
`useActionState` + Server Actions for new server-mutating forms, with
`useOptimistic` for instant-feeling updates.

## Error Boundary

Every route/tree gets one — a rendering crash should never blank the app:

```typescript
class ErrorBoundary extends React.Component<{ children: ReactNode }, BoundaryState> {
  state: BoundaryState = { hasError: false, error: null }
  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error }   // render fallback UI
  }
  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('boundary caught:', error, info)   // report to monitoring
  }
  render() {
    return this.state.hasError ? <Fallback /> : this.props.children
  }
}
```
A boundary catches render/lifecycle/constructor errors of its children — not
event handlers or async code; handle those with try/catch in the handler.

## Testing (React Testing Library)

Test what the user sees and does, not implementation details. Render with
production-like providers, query by role/label, interact with `userEvent`,
assert visible output — never inspect state, props-to-children, render counts,
or mock React itself. Query priority: `getByRole`/`getByLabelText` first;
`getByTestId` last resort. Variants: `getBy*` throws, `queryBy*` asserts
absence, `findBy*` for async-appearing elements — never `setTimeout` +
assertion.

```typescript
test('submits the form', async () => {
  const user = userEvent.setup();
  renderWithProviders(<UserForm onSubmit={vi.fn()} />);
  await user.type(screen.getByLabelText('Email'), 'user@example.com');
  await user.click(screen.getByRole('button', { name: /save/i }));
  expect(await screen.findByText(/saved/i)).toBeInTheDocument();
});
```

**Harness**: wrap providers once in `test-utils.tsx` (`renderWithProviders`)
with a fresh QueryClient per test (`retry: false`) — instantiate it *outside*
the wrapper closure or cache resets become flakes. Mock HTTP with MSW
(`setupServer` + `onUnhandledRequest: 'error'`; `server.use()` per-test for
error paths). Custom hooks: `renderHook` + `act`, public API only. Run
`expect(await axe(container)).toHaveNoViolations()` on interactive components
(missing labels, bad ARIA, heading order; visual contrast needs a real
browser). Skip DOM snapshots — they break on styling and get rubber-stamped;
use Playwright screenshot diffs for visual regression. Escalate to Playwright
when JSDOM can't: layout/flexbox, scrolling, drag-and-drop, iframes,
clipboard, downloads.

Coverage targets: utilities ≥90%, hooks ≥85%, presentational components ≥80%
(behavior, not lines), containers ≥70% (golden paths + error states).

## Accessibility

Accessibility is a contract, not a polish step — **keyboard navigation**
(ArrowUp/Down cycle, Enter selects, Escape closes, `e.preventDefault()` on
handled keys; `role`/`aria-expanded`/`aria-haspopup` on composite widgets) and
**focus management** (on modal open save `document.activeElement`, focus the
dialog with `tabIndex={-1}` + `role="dialog"` + `aria-modal="true"`, restore
focus on close). Interactive elements are operable without a mouse; every
icon-only control has an accessible name; semantic HTML (`<button>`, `<nav>`,
`<main>`) before `role` attributes; inputs have `<label htmlFor>` or
`aria-label`.

## Checklist

- [ ] Components compose small pieces; hooks stable via refs
- [ ] Reducer actions a closed typed union; context split per concern
- [ ] Server default, client islands minimal; no `useEffect`+`fetch` for app data
- [ ] Awaits parallelized, not waterfalled; long lists virtualized; no barrels
- [ ] Tests query by role, MSW for network, axe for a11y
- [ ] Validation pure and centralized; error boundary per route
- [ ] Keyboard operability + focus restore verified
