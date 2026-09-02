---
description: MCP server design for GAO — tool granularity, LLM-readable errors, schema design, stdio vs HTTP transport, and security posture
tags: mcp, tools, protocol, api, llm, security
origin: ECC (enriched)
---

# MCP Server Design Patterns

The Model Context Protocol (MCP) exposes tools, resources, and prompts to AI
agents over a standardized wire protocol. GAO runs MCP servers so agent nodes
can call project capabilities (e.g. `read_knowledge`) through a uniform,
schema-validated interface. These patterns keep tools easy for an LLM to call
correctly and safe to retry.

## When to Reference

- Adding a new tool, resource, or prompt to a GAO MCP server
- Deciding tool granularity — one coarse tool vs many fine-grained ones
- Designing input schemas and error messages for LLM callers
- Choosing between stdio and HTTP transport, or wiring a server into a client config
- Hardening tools against injection, misuse, and unsafe defaults
- Making mutating tools safe against retries from agent loops

## Core Surface

- **Tools**: actions the model invokes (search, run a check). Register with the
  API your SDK version provides (`tool()` vs `registerTool()` — verify against
  current official MCP docs before upgrading; don't copy-paste old signatures).
- **Resources**: read-only data the model fetches; handlers typically receive a `uri`.
- **Prompts**: reusable parameterized templates the client can surface.

## Tool Granularity

- **One tool = one clear intent.** Split kitchen-sink tools into focused ones
  (`search_notes` vs `read_note`) rather than a single `notes(action=...)` tool.
- But don't fragment so far that the model must chain three calls to do one
  logical thing — if two operations are always used together, merge them.
- Parameterize by data, not by mode flags: prefer `read_note(path)` over
  `read_note(mode="by_id"|"by_path")`. Multiple modes means multiple tools.
- Fewer, well-named tools beat many similar ones — similar names cause
  mis-selection by the model.
- Keep the tool surface small and orthogonal: every tool adds context cost and
  another selection decision for the model.
- Document cost/side effects in the tool description (e.g. "calls external API,
  rate-limited") so the agent can budget calls.

## Read-Only vs Mutating Tools

Separate them explicitly and make the distinction visible to the caller:

- **Read-only** (`read_knowledge`, search, list): safe to call speculatively
  and to retry. Never mutate state as a side effect.
- **Mutating** (write, update, delete, send): must be idempotent when possible
  so an agent-loop retry doesn't double-apply. Accept a client-supplied idempotency
  key or use deterministic keys from inputs.
- Mutating tools that cannot be idempotent (irreversible actions) should
  require explicit confirmation and be documented as such in their description.
- Destructive defaults are forbidden: prefer dry-run/preview modes; default to
  read-only until the caller passes an explicit `commit=True` style flag.

```python
async def update_note(path: str, content: str,
                      expected_hash: str | None = None) -> UpdateResult:
    """Idempotent write: no-op when content already matches."""
    current = await store.read(path)
    if current and current.content == content:
        return UpdateResult(changed=False)
    if expected_hash and current and current.hash != expected_hash:
        raise ConflictError(f"note changed since read: {path}")
    await store.write(path, content)
    return UpdateResult(changed=True)
```

## Schema Design

- Define an input schema for **every** tool — typed parameters, documented
  descriptions, and a documented return shape. Validate before the handler
  runs (Pydantic v2 in Python, Zod in TypeScript).
- Keep schemas flat and simple; deep nesting hurts model accuracy.
- Use enums for closed sets of values instead of free strings.
- Make optional parameters truly optional with sensible defaults; don't force
  the model to pass empty values.
- Return structured results (a typed payload), not free-form prose the caller
  must parse.

```python
class ReadKnowledgeInput(BaseModel):
    topic: str = Field(description="Knowledge file name without .md extension")
    fmt: KnowledgeFormat = KnowledgeFormat.markdown

class ReadKnowledgeOutput(BaseModel):
    content: str
    truncated: bool = False
```

## Error Messages That Name Valid Alternatives

Error text is read by the model, not a human debugger — it is the tool's
recovery API. A good error tells the caller what to do next:

- Never return raw stack traces; return a short, structured message.
- When a lookup fails, list valid options (bounded — truncate long lists).
- When validation fails, state the expected format with an example.
- Distinguish "not found" from "invalid input" from "permission denied" —
  each implies a different next action for the agent (see `error-handling.md`).

```python
async def read_knowledge(topic: str) -> str:
    available = sorted(kb.list_topics())
    if topic not in available:
        preview = ", ".join(available[:10]) + (", ..." if len(available) > 10 else "")
        raise ToolError(
            f"Unknown topic '{topic}'. Valid topics: {preview}",
            valid_alternatives=available,
        )
    return kb.read(topic)
```

## Transport: stdio vs HTTP

- **stdio** is the transport for local clients (e.g. Claude Desktop, a local
  agent host): the client spawns the server as a subprocess and speaks over
  stdin/stdout. Wire protocol is JSON-RPC; log to stderr, never stdout —
  stdout is protocol.
- Keep server logic (tools + resources + prompts) independent of the transport
  so the same server object can be served over stdio or HTTP from a thin
  entrypoint (e.g. `mcp.run()` for stdio, `transport="streamable-http"` for
  remote).
- Use Streamable HTTP for remote clients (cloud, cross-host agents); legacy
  HTTP/SSE only for backward compatibility.
- Ensure stdio servers are long-lived, handle initialize / list-tools /
  call-tool round-trips, and shut down cleanly on stdin close.

## Security

- **Validate everything crossing the boundary**: schema-validate tool inputs;
  treat any string that will reach a shell, SQL engine, path join, or LLM
  prompt as untrusted. Reject before executing, with an error naming the
  expected format.
- **Scope tools to the caller**: read-only tools by default; per-caller auth on
  mutating tools; no tool should expose more of the filesystem or network than
  its stated capability (path traversal and `../` escapes are the classic leak).
- **Pin and audit dependencies**: pin the MCP SDK version, read release notes
  when bumping; never fetch or execute remote content inside a tool handler
  without explicit allowlisting.
- **Prompt-injection surface**: if a tool returns third-party/untrusted text,
  mark it as data in the result, never as instructions the agent must follow.

## Registration & Versioning Notes

- SDK registration APIs vary by version and language; verify against the
  current official MCP docs before upgrading.
- Prefer idempotent handlers and bounded responses (truncate large outputs,
  report `truncated: true`) — agent loops retry, and huge payloads waste
  context.

## Anti-Patterns (NEVER)

- One mega-tool with a mode/verb string parameter.
- Mutating tools without idempotency or confirmation for irreversible effects.
- Errors like `An error occurred` — the model can't recover from that.
- Untyped tool inputs; validation errors surface at call time inside the handler.
- Printing logs to stdout on a stdio server (corrupts the protocol stream).
- Passing untrusted tool arguments into shell commands, SQL, or file paths unvalidated.
