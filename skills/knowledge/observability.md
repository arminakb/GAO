---
description: Observability and production readiness — structured logging, metrics, tracing, health checks, alerting, SLOs, and ship-blocker triage
tags: observability, logging, metrics, health-checks, sre, production
origin: ECC (enriched)
---

# Observability & Production Readiness

Production behavior is judged by evidence, not green CI: structured logs you
can query, metrics that expose the Golden Signals, traces that follow a request
across agent nodes, health checks that distinguish "alive" from "ready", and
alerts that page on symptoms rather than causes. Use this when wiring
observability into a service or auditing whether a release is shippable.

## When to Reference

- Adding logging, metrics, tracing, or health endpoints to a service
- Configuring Kubernetes probes or Docker HEALTHCHECK (see `kubernetes-patterns.md`, `container-ops.md`)
- Deciding what to alert on and how to avoid alert fatigue
- Setting SLOs, error budgets, and dashboard panels
- Auditing a repo for production readiness before launch or after a merge
- Answering "what would break in prod?"

## Structured Logging

- **Logs are JSON lines** with timestamp, level, message, and correlation id —
  never free-text prose the next consumer must regex.
- **Correlation ids** propagate end to end: inbound request id → every agent
  node, tool call, and downstream HTTP call. Without one, distributed failures
  are unexplainable.
- **No secrets or PII in logs** — tokens, keys, user content, and personal
  data stay out; log identifiers and shapes, not payloads.
- Log at the boundaries (request in/response out, tool call/result, retry,
  degraded mode); leave debug detail to debug level, off by default.
- Aggregate logs centrally and make them searchable by correlation id and
  level.

```json
{"ts": "2026-09-01T10:15:30Z", "level": "error", "msg": "tool call failed",
 "request_id": "req_8f2a", "agent_node": "planner", "tool": "read_knowledge",
 "attempt": 2, "error": "timeout after 5s"}
```

## Metrics — the Golden Signals

Export, at minimum: **latency** (p50/p95/p99, per endpoint), **traffic**
(request rate), **errors** (rate and ratio, by type), **saturation** (queue
depth, memory, connection-pool use). Track these per agent node for GAO runs —
token cost and task latency are orchestrator golden signals, not nice-to-haves.
Prefer counters/histograms the backend can aggregate over pre-averaged gauges;
a mean alone hides the tail that users actually experience.

## Tracing

- One span per hop: inbound request → each orchestrator node → each tool/LLM
  call → outbound request. Propagate the trace context across HTTP, queues,
  and async boundaries.
- Span attributes carry the *what* (node name, tool name, model, retry count),
  not payloads — same privacy rule as logs.
- Sample intelligently: keep 100% of error traces, sample successes; tail
  latency diagnosis needs the slow traces, not the median ones.
- Traces join logs via the shared correlation/trace id.

## Health Checks: Liveness vs Readiness

They answer different questions and must be separate endpoints:

| Probe | Question | Fail action |
|---|---|---|
| **Liveness** (`/health/live`) | Is the process running (not deadlocked)? | Restart the container |
| **Readiness** (`/health/ready`) | Can it serve traffic right now (deps reachable, warm)? | Remove from load balancer, no restart |
| **Startup** (`/health/startup`) | Has initialization finished? | Defer liveness/readiness checks |

```python
@app.get("/health/ready")
async def readiness():
    checks = {
        "database": await check_db(),      # SELECT 1
        "redis": await check_redis(),
    }
    ok = all(c["status"] == "ok" for c in checks.values())
    return JSONResponse(status_code=200 if ok else 503,
                        content={"status": "ok" if ok else "degraded",
                                 "checks": checks, "version": APP_VERSION})
```

Liveness must NOT check dependencies — a database blip would restart every
instance simultaneously and turn a brownout into an outage. Readiness is the
one that proves dependencies are reachable. Add a startup probe for slow
initialization so liveness doesn't kill the process mid-boot.

## Alerting Principles

- **Page on symptoms, not causes**: alert on user-visible signals (error rate,
  p95 latency, queue age) that an SLO consumes — not on CPU% or a single pod
  restart.
- **Every page must be actionable**: if the responder can't do anything but
  watch, it's a dashboard panel or a ticket, not a page.
- **Multi-window, multi-burn-rate**: a spike for one minute is noise; burn
  rate sustained across a short and a long window is a page.
- **Route by ownership**: each alert names the owning runbook and on-call
  target; unowned alerts get deleted, not muted.
- Silences are time-boxed and annotated; "muted forever" alerts are rot.

## SLO Basics

- Define per critical user journey: "99% of orchestrator runs complete within
  60s over 28 days" — measured at the consumer edge, not inside the cluster.
- **Error budget** = 1 − SLO. Budget remaining → ship features and take risks;
  budget exhausted → freeze risky changes and fix reliability first.
- Start modest (99.5% is 3.6h/month of budget; 99.9% is 43min — don't promise
  99.9% on day one).
- Track SLO burn on a dashboard next to deployment markers so regressions
  correlate with releases.

## Production Readiness Audit

Evidence over assertion — check, don't assume (green CI is not readiness):

| Lens | Checks |
|---|---|
| Config | Env vars documented, validated at startup, fail-fast; no hardcoded secrets |
| Data | Migrations forward-safe with rollback plan; destructive ops staged; writes idempotent |
| Auth | Server-side enforcement on sensitive routes; secrets out of bundles/logs |
| Ops | Clean-checkout start via documented commands; deploy + rollback paths tested; health endpoint proves dependency reachability |
| Monitoring | Metrics exported; alerts on error/latency thresholds; log aggregation live; uptime monitor on health endpoint |
| UX | Loading/empty/error states present; launch-critical path covered E2E |

Score findings as blockers (auth missing on sensitive data, non-idempotent
webhooks, unsafe migrations, exposed secrets, no rollback for a high-impact
release — any one blocks launch) vs high-value fixes, and lead the report with
a one-line ship/hold verdict plus the evidence checked and missing. Local
evidence only: never upload repo contents to external scanners without explicit
user approval, and never treat an unpinned remote audit tool as the default path.

## Checklist

- [ ] Logs are structured JSON with request-id correlation; no secrets/PII
- [ ] Golden Signals exported per endpoint and per agent node (incl. token cost)
- [ ] Trace context propagates across every hop, including async/queues
- [ ] Separate liveness/readiness endpoints; liveness ignores dependencies
- [ ] Alerts page on SLO burn (multi-window), each with an owned runbook
- [ ] SLO + error budget defined for critical journeys
- [ ] Rollback path documented and tested before the release ships

## Anti-Patterns (NEVER)

- Liveness probes that check downstream dependencies (restart storms on blips).
- Logging request payloads, tokens, or user PII.
- Alerting on causes (CPU, single restarts) instead of user-visible symptoms.
- Treating green CI as production readiness — CI answers a different question.
- A health endpoint that returns 200 without proving dependencies are reachable.
- Uploading source or secrets to an external audit service without explicit approval.
