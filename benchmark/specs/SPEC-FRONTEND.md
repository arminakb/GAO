# Benchmark Project: Kanban Task Board (Frontend)

A small but complete single-page web app that the contestant must build from
scratch. Chosen to exercise frontend skills: component design, typed state
management, form validation, persistence, error states, accessibility, and
testing with Vitest + Testing Library.

## Functional Requirements

1. **Domain**: A task board with three columns: `todo`, `doing`, `done`.
   Tasks have a unique `id`, `title`, optional `description`, `createdAt`.
2. **Task lifecycle**: create a task (into `todo`), move a task between any
   two columns, delete a task. A task must always exist in exactly one
   column — moving must never duplicate or lose it.
3. **Validation**: title is required, 1–200 characters. Invalid input shows
   an inline error message and does not mutate state.
4. **Persistence**: all state persists to `localStorage` and is restored on
   reload. A previously stored board in an OLD schema shape (e.g. missing
   the `description` field, or tasks stored as a flat array with no column
   info) must be migrated or safely ignored — the app must not crash on
   legacy data.
5. **UI**: column headers show live counts. A text filter (case-insensitive
   substring on title) narrows all columns. Empty states show a message.

## Non-Functional Requirements

- TypeScript in strict mode (`tsc --noEmit` clean), React 18+, Vite
- Test suite: Vitest + React Testing Library; at minimum: creating, moving
  (uniqueness assertion), validation errors, and legacy-data restore
- No `any` in app code; no secrets; README with run instructions
- Standard React ecosystem only (React, Vite, Vitest, Testing Library);
  state libraries not required and not forbidden

## Deliverables

- Source under `src/`, tests under `src/` or `tests/`, `package.json`,
  `README.md`
- `npm install && npm test -- --run && npm run build` and
  `npx tsc --noEmit` must pass from a clean checkout

## Deliberate Traps

- Task-move uniqueness (Req 2) breaks under naive implementations that
  append to the target column without removing from the source, or that
  key lists by array index — only a test asserting global uniqueness
  across all columns after moves catches it.
- Legacy-data restore (Req 4) punishes `JSON.parse(localStorage)` without
  shape validation: old data crashes the app on first render.
- Filter + move interaction: moving a task while the filter is active must
  not corrupt column state (filtered view is derived, never stored).
