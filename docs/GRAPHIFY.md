# Graphify Integration

Graphify is GOA's architectural knowledge graph (`graphify-out/graph.json`):
nodes = symbols/modules, edges = dependencies. It is a competitive advantage
because it turns "what might this change break?" from a guess into a graph
query.

## Current integration

| Surface | Mechanism |
|---|---|
| Queries (existing) | `graph_memory_mcp.py`: query_architecture, explain_symbol, find_impact_path, get_community_members, list_god_nodes |
| Refresh (existing) | `refresh_graph` → `graphify update .` → NetworkX reload |
| Impact for external agents (new) | `goa_get_impact(symbol)` on the GOA MCP server |
| Fresh-graph review (new) | `goa_refresh_graph()` between implementation and review (TASK.md §17) |

## Impact analysis (`goa_get_impact`)

```
changed symbol
   ↓ in/out degree, descendants (capped)
affected modules
   ↓
god-node check  (degree ≥ max(10, edges/nodes×5))
   ↓
review depth: normal | increased
blast radius: local | medium | high
```

- God nodes (highly connected components) automatically justify deeper
  verification/review — the reviewer gets a `review_depth: increased` signal.
- `affected_downstream` lists what the change can propagate to (regression
  surface candidates for extra tests).

## Refresh discipline

`goa_refresh_graph()` runs `graphify update .` and reports success/failure
cleanly (missing CLI, timeout). Review stages must operate on fresh state —
stale AST snapshots are rejected by workflow ordering (refresh after
implementation, before review), not by trust.

## Honest limits

The current graph is import-level, not type-level; dynamic dispatch and
cross-language edges are invisible. Impact results are advisory inputs to
routing/review depth — GOA does not claim soundness, only cheap, useful
approximation. When `graph.json` is absent, `goa_get_impact` returns
`available: false` and orchestration proceeds without it (graceful
degradation, never fabrication).
