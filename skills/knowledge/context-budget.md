---
description: Audits context/token consumption across agents, skills, and MCP tools; identifies bloat and produces prioritized savings recommendations.
tags: tokens, context, efficiency, audit
origin: ECC
---

# Context Budget Audit

Analyze token overhead across every loaded component of an agent system and
surface prioritized optimizations. Adapted for GAO: the budget unit is the
per-agent token limit in `harness/policies.json`, and the 40%-of-budget
system-prompt ceiling.

## When to Reference

- Output quality degrades or context fills faster than expected
- After adding agents, knowledge files, or MCP tools (audit immediately — creep is incremental)
- Before adding components, to confirm headroom
- When `harness-optimizer` proposes policy changes and the baseline matters

## Phase 1: Inventory

Estimate tokens per component class:

**Agent prompts** (`agents/*.md`) — `chars / 3.5` (GAO drift-test convention):
- Flag: prompt >40% of the role's per-agent limit (CI enforces this)
- Flag: `## Output Format` examples longer than the rules they illustrate

**Knowledge files** (`skills/knowledge/**/*.md`):
- Flag: files whose content no agent prompt references (dead weight — still
  served by the Knowledge MCP listing)
- Flag: overlapping files covering the same domain (merge candidates)

**MCP tools** (active servers):
- ~500 tokens per tool schema. **MCP is the biggest lever** — a 30-tool
  server costs more than the entire knowledge surface. Flag servers >20
  tools and servers that wrap CLIs an agent could call directly.

## Phase 2: Classify

| Bucket | Criteria | Action |
|--------|----------|--------|
| Always needed | Referenced by an agent prompt's Available Tools / knowledge citations; matches current task domain | Keep |
| Sometimes needed | Domain-specific (e.g. `frontend-patterns`), loaded only for relevant tasks | Keep — MCP loads on `read_knowledge`, not upfront |
| Rarely needed | No prompt references it, overlapping content, no plausible task | Remove or merge |

GAO note: knowledge files are lazy-loaded via `read_knowledge`/`search_knowledge`,
so a large knowledge surface costs only listing overhead — the audit's focus
is agent prompts (always loaded) and tool schemas (always loaded), not
knowledge volume.

## Phase 3: Detect Issues

- **Heavy agent prompts** — over the 40% ceiling, or rules sections that
  restate what the knowledge base already covers (distill, don't duplicate)
- **Duplicated guidance** — the same checklist in an agent prompt AND a
  knowledge file; the prompt should carry only the role-critical subset
- **Bloated examples** — worked examples that illustrate an anti-pattern
  already covered by one-line rules
- **MCP over-subscription** — tool schemas for capabilities agents never use

## Phase 4: Report Format

```
Context Budget Report
═══════════════════════════════════════
Per-agent prompt usage: role: est-tokens / 40%-cap (worst first)
Knowledge surface: N files, ~M tokens (lazy-loaded — listing cost only)
MCP tools: N tools, ~X,XXX tokens (always loaded)

WARNING: Issues Found (N) — ranked by token savings
Top 3 Optimizations:
1. [action] → save ~X,XXX tokens
2. [action] → save ~X,XXX tokens
3. [action] → save ~X,XXX tokens
```

## Estimation Rules

- Prose: `chars / 3.5` (GAO drift-test convention) or `words × 1.3`
- Code-heavy files: `chars / 4`
- Always re-run the audit after adding any agent, prompt section, or MCP tool
