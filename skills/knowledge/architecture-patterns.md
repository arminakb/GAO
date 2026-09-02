---
description: ADR capture and hexagonal (ports & adapters) architecture for GAO — decision records, domain boundaries, dependency rule
tags: architecture, adr, hexagonal, ports, adapters, design
origin: ECC (enriched)
---

# Architecture Decision Records & Hexagonal Design

Two complementary disciplines: recording *why* the architecture is shaped as it is (ADRs), and structuring code so business logic is independent of frameworks, transport, and persistence (ports & adapters). Use this reference when making or recording significant design choices, or when domain logic has become entangled with I/O.

## When to Reference

- Choosing between significant alternatives (framework, library, pattern, database, API design)
- Capturing a decision made during planning or implementation as an ADR
- Answering "why did we choose X?" from the decision log
- Designing or refactoring toward ports & adapters / clean boundaries
- Supporting multiple interfaces for one use case (HTTP, CLI, queue workers, cron)
- Reviewing code for dependency-direction violations or leaky boundaries

## ADR Format

Lightweight Nygard-style ADRs live in `docs/adr/` as `NNNN-decision-title.md`, plus a `README.md` index table (`| ADR | Title | Status | Date |`) and a `template.md`.

```markdown
# ADR-0004: Use LangGraph for agent orchestration

**Date**: 2026-08-31
**Status**: accepted
**Deciders**: core team

## Context
[2-5 sentences: the problem, constraints, forces]

## Decision
[1-3 sentences stating the decision clearly]

## Alternatives Considered
### Alternative 1: [Name]
- **Pros / Cons / Why not**: [benefits / drawbacks / specific rejection reason]

## Consequences
### Positive / Negative / Risks
- [benefits / trade-offs / risk and mitigation]
```

Lifecycle: `proposed → accepted → deprecated | superseded by ADR-NNNN`; superseded ADRs link their replacement. First-time setup requires explicit user confirmation before creating `docs/adr/`; draft ADRs are presented for review, never auto-written.

## When to Write an ADR

| Category | Examples |
|----------|---------|
| Technology choices | Framework, language, database, cloud provider |
| Architecture patterns | Monolith vs services, event-driven, CQRS |
| API design | REST vs GraphQL, versioning, auth mechanism |
| Data modeling | Schema design, caching strategy |
| Infrastructure | Deployment model, CI/CD, monitoring stack |
| Security | Auth strategy, encryption, secret management |
| Testing | Framework, coverage targets, E2E vs integration |
| Process | Branching, review, release cadence |

Not worth recording: naming, formatting, trivial implementation choices.

**Good ADR hygiene**: be specific ("Use Pydantic v2" not "use a validation lib"); record the *why* — rationale matters more than the what; always include rejected alternatives ("we just picked it" is not rationale); state trade-offs honestly; keep it readable in 2 minutes; write in present tense ("We use X"); note the original date when backfilling; never let stale ADRs linger.

Detect decision moments from signals like "let's go with X", "we should use X instead of Y", or comparing two frameworks and reaching a conclusion — but draft for review rather than auto-writing files.

## Hexagonal Architecture (Ports & Adapters)

The core application depends on abstract ports; adapters implement those ports at the edges. Layers:

- **Domain model** — business rules, entities, value objects. No framework imports.
- **Use cases (application layer)** — orchestrate domain behavior; own inbound/outbound port definitions.
- **Inbound ports** — what the application can do (use-case interfaces, command/query contracts).
- **Outbound ports** — what the application needs (repositories, gateways, event publishers, clock).
- **Adapters** — infrastructure implementing ports: HTTP handlers, DB repos, queue consumers, SDK wrappers.
- **Composition root** — the single place where concrete adapters are injected into use cases.

### Dependency Direction

Always inward; violations are the primary defect to check in review:

- Adapters → application/domain
- Application → port interfaces only
- Domain → domain-only abstractions; **domain → nothing external**

### Port Rules

- Model capabilities, not technologies: `OrderRepositoryPort`, `PaymentGatewayPort` — not `PostgresPort`.
- Every external side effect (persistence, external API, time, randomness, logging) is behind an outbound port.
- Ports usually live in the application layer; only truly domain-level abstractions live in the domain.
- Mapping between protocol/DB shapes and domain objects happens in adapters, never in use cases.

### Python Example

```python
from typing import Protocol

class OrderRepositoryPort(Protocol):
    def save(self, order: Order) -> None: ...
    def find_by_id(self, order_id: str) -> Order | None: ...

class CreateOrderUseCase:
    def __init__(self, orders: OrderRepositoryPort) -> None:
        self._orders = orders

    def execute(self, order_id: str, amount_cents: int) -> str:
        order = Order.create(order_id, amount_cents)
        self._orders.save(order)
        return order.id
```

Adapters implement the protocols (e.g. a `PostgresOrderRepository` mapping rows to `Order`); the composition root wires concrete implementations into use cases — keep wiring centralized, no hidden service-locator globals.

### Boundaries & Layout

Organize feature-first with explicit boundaries:

```text
src/
  features/orders/
    domain/            # entities, business rules — zero framework imports
    application/
      ports/           # inbound + outbound protocols
      use_cases/       # orchestration
    adapters/
      inbound/http/    # FastAPI routes → use-case calls
      outbound/postgres/
    composition/       # container/wiring module
```

Validation happens at boundaries (inbound adapter + use-case invariants). Errors are translated across boundaries: infrastructure errors become application/domain errors (see `error-handling.md`). Use cases return plain data structures, never DB rows or framework objects; inbound adapters are the natural place to map onto shared API contracts (see `api-design.md`).

### Testing per Boundary

- **Domain tests**: pure business rules, no mocks or framework setup.
- **Use-case unit tests**: in-memory fakes for outbound ports; assert outcomes and interactions.
- **Adapter contract tests**: shared suites run against each port implementation.
- **Adapter integration / E2E tests**: real infra for serialization, retries, timeouts; journeys through inbound → use case → outbound.

### Refactoring Playbook

1. Pick one vertical slice (a single endpoint/job) with high churn and low blast radius.
2. Extract a use-case boundary with explicit input/output types.
3. Introduce outbound ports around existing infrastructure calls; wrap legacy services as adapters first (facade-first / strangler approach).
4. Move orchestration out of controllers into the use case; keep old endpoints delegating to it.
5. Add characterization tests before extraction; keep them until the new boundary is verified.
6. Repeat slice by slice — no big-bang rewrites. Keep a reversible toggle per migrated slice.

## Best-Practices Checklist

- Domain and use-case layers import only internal types and ports.
- Every external dependency is represented by an outbound port.
- Use immutable transformations (return new values instead of mutating shared state).
- Composition root is explicit and easy to audit.
- Language/framework specifics stay in adapters, never in domain rules.

## Anti-Patterns (NEVER)

- Domain entities importing ORM models, web framework types, or SDK clients.
- Use cases reading directly from `request`/response objects or queue metadata.
- Returning DB rows from use cases without mapping.
- Adapters calling each other instead of flowing through ports.
- Dependency wiring scattered across files; PRs with architectural changes but no ADR.
