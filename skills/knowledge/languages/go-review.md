---
description: Go review guide — error wrapping, goroutine-leak prevention, errgroup coordination, interface design, and table-driven testing
tags: go,code-review,concurrency,error-handling,testing
origin: ECC (enriched)
---

# Go Code Review Guide

Distilled review guide for idiomatic Go: simplicity, error values with wrapping, goroutine/channel safety, and table-driven testing. Use it when reviewing Go changes for concurrency leaks, ignored errors, and violations of Go's error-handling and interface conventions.

## When to Reference

- Reviewing any Go change (`*.go`, go.mod)
- Error wrapping, sentinel errors, `errors.Is`/`errors.As`
- Goroutines, channels, `context`, `sync` primitives, graceful shutdown
- Interface design and package layout
- Reviewing Go tests, benchmarks, or fuzz targets

## CRITICAL — Error Handling

- Every `err != nil` must be handled: return wrapped, log, or document why it is safe to ignore. `result, _ := ...` is a defect.
- Wrap errors with context using `%w` so callers can `errors.Is`/`errors.As`.
- Sentinel errors (`ErrNotFound`) and typed errors (`*ValidationError`) for common cases; inspect with `errors.Is` / `errors.As`.
- Never use `panic` for control flow in library code.

```go
// BAD: swallowed and context-free
data, _ := os.ReadFile(path)
return nil, err

// GOOD: wrapped with context, propagates
data, err := os.ReadFile(path)
if err != nil {
    return nil, fmt.Errorf("load config %s: %w", path, err)
}
```

## CRITICAL — Goroutine Leaks and Cancellation

- Every goroutine must have a guaranteed exit: `context` cancellation, closed channel, or `WaitGroup`.
- Sends on unbuffered channels must be `select`-able against `ctx.Done()`; use buffered channels for fire-and-forget results.
- `context.Context` is the first parameter, never stored in structs.
- Always `defer cancel()` for `WithTimeout`/`WithCancel`.
- Prefer `errgroup` for coordinated concurrent work with error propagation:

```go
g, ctx := errgroup.WithContext(ctx)   // first error cancels the rest
results := make([][]byte, len(urls))
for i, url := range urls {
    i, url := i, url                  // capture loop vars (pre-1.22)
    g.Go(func() error {
        data, err := FetchWithTimeout(ctx, url)
        if err != nil { return err }
        results[i] = data
        return nil
    })
}
if err := g.Wait(); err != nil { return nil, err }
```

```go
// BAD: blocks forever if no receiver / on cancellation
ch := make(chan []byte)
go func() { ch <- fetch(url) }()

// GOOD: respects cancellation
ch := make(chan []byte, 1)
go func() {
    select {
    case ch <- data:
    case <-ctx.Done():
    }
}()
```

## HIGH — Concurrency and Shared State

- "Share memory by communicating": use channels for coordination; protect shared state with `sync.Mutex`.
- `sync.WaitGroup`: `wg.Add(1)` before `go`, `defer wg.Done()` inside.
- Server lifecycle: signal.Notify + `server.Shutdown(ctx)` with timeout.
- Avoid package-level mutable globals; inject dependencies via constructors.

```go
// BAD: nil map panics, global mutable state
var db *sql.DB // set in init(), errors ignored

// GOOD: zero-value-useful types, DI
type Counter struct{ mu sync.Mutex; n int }
func NewServer(db *sql.DB) *Server { return &Server{db: db} }
```

## HIGH — Interfaces and API Design

- Accept interfaces, return structs — returning interfaces hides behavior.
- Small, single-method interfaces; compose (`io.ReadWriteCloser`) as needed.
- Define interfaces where they are consumed, not where implemented.
- Optional behavior via type assertion `if f, ok := w.(Flusher); ok`.

```go
// BAD: returns interface, over-broad interface
func Process(r io.Reader) (io.Reader, error)

// GOOD: concrete return
func Process(r io.Reader) (*Result, error)
```

## HIGH — Performance

- Preallocate slices/maps when size is known: `make([]Result, 0, len(items))`.
- Never concatenate strings in loops — `strings.Builder` or `strings.Join`.
- `sync.Pool` for hot-path allocations (reset before `Put`).

```go
// BAD: repeated reallocation
var s string
for _, p := range parts { s += p }

// GOOD: single buffer
var sb strings.Builder
for _, p := range parts { sb.WriteString(p) }
```

## MEDIUM — Idiom and Layout

- Clear over clever: no IIFE-wrapped trivial logic, no naked returns in long functions.
- `const` by default, `let`-equivalent (`var`/`:=`) only when reassigning; early returns keep the happy path unindented.
- Package names: short, lowercase, no underscores, no redundant `Service` suffix.
- Standard layout: `cmd/`, `internal/`, `pkg/`, `testdata/`.
- Functional options pattern for optional config; embedding for composition.

## Testing Standards

- Table-driven tests with `t.Run` subtests; include error cases (`wantErr bool`).
- Use `t.Parallel()` for independent subtests (capture loop var: `tt := tt` pre-1.22 style).
- Helpers call `t.Helper()`; cleanup via `t.Cleanup` / `t.TempDir()`.
- Mock via small interfaces with function fields (`GetUserFunc`), not heavy frameworks.
- Golden files in `testdata/` with an `-update` flag; coverage targets:
  critical logic 100%, public APIs 90%+, general 80%+.
- HTTP handler testing via `httptest` — exercise the full handler, not the
  business function:

```go
func TestGetUser(t *testing.T) {
    req := httptest.NewRequest("GET", "/users/42", nil)
    rec := httptest.NewRecorder()
    handler(rec, req)
    if rec.Code != http.StatusOK {
        t.Errorf("got %d; want 200", rec.Code)
    }
}
```

- Benchmarks with `b.ResetTimer()` and `-benchmem`; fuzz targets with seed
  corpus via `f.Add` (`go test -fuzz=FuzzParse -fuzztime=30s`).

```go
// GOOD: table-driven with error cases
for _, tt := range tests {
    t.Run(tt.name, func(t *testing.T) {
        got, err := ParseConfig(tt.input)
        if tt.wantErr { if err == nil { t.Error("expected error") }; return }
        if err != nil { t.Fatalf("unexpected error: %v", err) }
    })
}
```

## Tooling Gates

```bash
go vet ./... && go test -race ./...   # race detector must pass
golangci-lint run                     # errcheck, staticcheck, govet(shadow)...
gofmt -l .                            # must be empty
```
