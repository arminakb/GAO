---
description: Security review checklist — OWASP injection classes, XSS/CSP, CSRF, rate limiting, secrets, and severity-based approval gates
tags: security,owasp,review,injection,secrets
origin: ECC (enriched)
---

# Security Review Checklist (OWASP)

Security audit method for code changes: hunt injection, broken auth,
sensitive-data exposure, and misconfiguration across the diff and its blast
radius. Zero Critical/High findings is the bar for approval; a CRITICAL
finding stops the workflow until fixed.

## When to Reference

- Reviewing code that handles authn/authz, user input, secrets, or PII
- Auditing dependency or configuration changes
- Assessing whether a change is safe to merge/ship
- Designing mitigation for a reported vulnerability

## Severity Taxonomy

- **Critical** — remotely exploitable without auth: SQLi, RCE, exposed admin,
  leaked production credentials. Fix immediately; halt shipping.
- **High** — broken access control, sensitive data exposure, weak crypto on
  secrets. Fix before merge.
- **Medium** — defense-in-depth gaps: verbose errors, missing rate limits,
  permissive CORS. Fix or file a tracked issue.
- **Low** — hardening: security headers, cookie flags, log hygiene.

## Injection Prevention

- SQL: parameterized queries or bound parameters only — never f-strings or
  `+` concatenation of user input into SQL.

  ```python
  # VULNERABLE
  cur.execute(f"SELECT * FROM users WHERE name = '{name}'")
  # SAFE
  cur.execute("SELECT * FROM users WHERE name = %s", (name,))
  ```

- Shell: `subprocess.run([...], shell=False)` with list args; reject any
  `shell=True` with user-controlled data. Validate against allowlists.
- Path traversal: resolve and verify user-supplied paths stay inside the
  intended base directory (`Path(base, name).resolve().is_relative_to(base)`).
- Deserialization: never `pickle`/`yaml.load` untrusted data; use
  `yaml.safe_load` and JSON.

## Secrets Management

- No hardcoded API keys, tokens, passwords, or connection strings — in code,
  tests, fixtures, comments, or logs. Use environment variables or a secret
  manager, validated at startup.
- Rotate any secret that ever entered the codebase, logs, or git history.
- Never log secrets or full tokens; scrub/redact in telemetry.

## Authentication & Access Control

- Deny by default: every endpoint/service checks identity **and** permission;
  authorization derives from the authenticated principal, never from
  client-supplied IDs.
- Failure messages don't reveal whether the account/resource exists.
- Session/token validation on every request; short-lived tokens, secure
  cookies (`HttpOnly`, `Secure`, `SameSite`).
- Server-side rate limiting on authentication and expensive endpoints.

## Data Exposure & Output Handling

- Sanitize/encode output per sink (HTML escaping, safe templates) to prevent
  XSS; use ORM/parameterization for SQL sinks.
- Return the minimum data necessary; mask PII in responses and logs.
- Enforce TLS for external calls; never disable certificate verification.

## XSS, CSP & CSRF

- Sanitize user-provided HTML with an allowlist sanitizer (e.g. DOMPurify:
  `ALLOWED_TAGS: ['b','i','em','strong','p']`, `ALLOWED_ATTR: []`) before any
  `dangerouslySetInnerHTML`/`innerHTML` sink.
- Content Security Policy: start strict (`default-src 'self'`,
  `object-src 'none'`, `frame-ancestors 'none'`, `script-src 'self'`) and
  loosen only with a documented removal plan. `'unsafe-inline'` /
  `'unsafe-eval'` neutralize CSP — treat them as temporary compatibility debt.
- CSRF: state-changing requests require a CSRF token (synchronizer token or
  `SameSite=Strict/Lax` cookies + `Origin` verification); pure
  bearer-token APIs without cookie auth are exempt.

## Rate Limiting

Server-side limits on authentication and expensive endpoints (search,
exports, LLM calls) — see api-design.md for header conventions and
redis-patterns.md for fixed/sliding-window implementations. Per-IP for
anonymous, per-user for authenticated; return `429` with `Retry-After`.

## Dependency & Config Risks

- Pin dependencies; check new dependencies against known advisories and
  scope (does this really need to be a dependency?).
- Debug mode off, CORS restricted to known origins, error responses free of
  stack traces and internals.

## Stop-and-Escalate Policy

On discovering a Critical finding: stop reviewing outward, report it with an
exact file/line, attack vector, and remediation, and block approval until it
is fixed. Check sibling call paths for the same flaw class before concluding.

## Review Checklist

- [ ] All user input validated/parameterized at every sink
- [ ] No secrets in code, tests, or logs; startup validation present
- [ ] Authorization on every protected path, derived from authn principal
- [ ] Error responses leak no internals; logging scrubs sensitive values
- [ ] Dependencies pinned and free of known advisories
