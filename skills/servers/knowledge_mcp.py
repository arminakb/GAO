"""Knowledge MCP Server.

Exposes user-provisioned Markdown files in ``skills/knowledge/`` as read-only
MCP Resources and Tools.  These files are **NEVER** generated or modified by
the system — they are manually provisioned by the user.

Features:
- Recursive discovery: knowledge files may live in subdirectories
  (e.g. ``languages/rust-review.md``); their name is the slash-joined
  relative path without the ``.md`` suffix (``languages/rust-review``).
- Optional YAML frontmatter: a leading ``---`` block may define
  ``description`` and ``tags``; anything else in the block is ignored.
  Without frontmatter the description falls back to the first heading line.
- Ranked search: matches against a file's name/description/tags rank above
  plain line matches.

Transport: stdio (default)

Start:
    python skills/servers/knowledge_mcp.py
"""

from __future__ import annotations

import re
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

server = FastMCP("knowledge-server")

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"

_MAX_SEARCH_RESULTS = 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _name_for(path: Path) -> str:
    """Return the resource name (slash-joined relative path, no suffix)."""
    return path.relative_to(KNOWLEDGE_DIR).with_suffix("").as_posix()


def _path_for(name: str) -> Path:
    """Resolve a resource name to a path, rejecting traversal outside the dir."""
    candidate = (KNOWLEDGE_DIR / name).with_suffix(".md").resolve()
    if not candidate.is_relative_to(KNOWLEDGE_DIR.resolve()):
        raise ValueError(f"Invalid knowledge resource name: {name!r}")
    return candidate


def _iter_md_files() -> list[Path]:
    """Return all .md files in the knowledge directory, recursively."""
    if not KNOWLEDGE_DIR.is_dir():
        return []
    return sorted(KNOWLEDGE_DIR.rglob("*.md"))


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split an optional leading ``---`` frontmatter block from the body.

    Returns ``(metadata, body)``. Only scalar ``key: value`` pairs are
    parsed (``description``, ``tags`` expected); lists and nested mappings
    are ignored.
    """
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip().strip("'\"")
        if key and value:
            meta[key] = value
    return meta, parts[2].lstrip("\n")


def _read_file(path: Path) -> tuple[str, list[str], str]:
    """Return ``(description, tags, full_text)`` for a knowledge file."""
    text = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(text)
    description = meta.get("description", "")
    if not description:
        for line in body.splitlines():
            stripped = line.strip().lstrip("#").strip()
            if stripped:
                description = stripped[:160]
                break
    tags = [t.strip() for t in re.split(r"[,\s]+", meta.get("tags", "")) if t.strip()]
    return description, tags, text


def _fallback_description(path: Path) -> str:
    """Best-effort description without reading the full file."""
    try:
        description, _, _ = _read_file(path)
        return description or path.stem
    except OSError:
        return path.stem


# ---------------------------------------------------------------------------
# MCP Resources — each .md file is a resource
# ---------------------------------------------------------------------------


@server.resource("knowledge://{name}")
def knowledge_resource(name: str) -> str:
    """Read a knowledge file by name (relative path, without .md extension)."""
    try:
        path = _path_for(name)
    except ValueError:
        return f"Knowledge resource '{name}' not found."
    if not path.is_file():
        return f"Knowledge resource '{name}' not found."
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@server.tool()
def list_knowledge() -> list[dict[str, str]]:
    """List all available knowledge resources.

    Returns a list of dicts with ``name``, ``description``, and ``tags``
    keys. New files added to ``skills/knowledge/`` (including
    subdirectories) are discovered automatically.
    """
    results: list[dict[str, str]] = []
    for path in _iter_md_files():
        try:
            description, tags, _ = _read_file(path)
        except OSError:
            description, tags = path.stem, []
        results.append(
            {
                "name": _name_for(path),
                "description": description[:160],
                "tags": ", ".join(tags),
            }
        )
    return results


@server.tool()
def read_knowledge(name: str) -> str:
    """Read the full content of a specific knowledge file.

    Args:
        name: The knowledge file name (relative path, without .md
              extension). e.g. ``"python-standards"`` or
              ``"languages/rust-review"``

    Returns:
        The full Markdown content of the file, or an error message naming
        valid alternatives if the file does not exist.
    """
    try:
        path = _path_for(name)
    except ValueError:
        return f"Knowledge resource '{name}' not found."
    if not path.is_file():
        available = [_name_for(p) for p in _iter_md_files()]
        return (
            f"Knowledge resource '{name}' not found. Available: {', '.join(available) or '(none)'}"
        )
    return path.read_text(encoding="utf-8")


@server.tool()
def search_knowledge(query: str) -> list[dict[str, str]]:
    """Search across all knowledge files for a keyword or phrase.

    Ranking: matches in a file's name, description, or tags score highest
    (files are listed with a ``line_number`` of ``"0"`` and a ``match``
    type); body line matches follow. Case-insensitive.

    Args:
        query: The search term.

    Returns:
        A list of dicts with ``name``, ``line_number``, ``excerpt``, and
        ``match`` keys.
    """
    query_lower = query.lower()
    scored: list[tuple[int, dict[str, str]]] = []

    for path in _iter_md_files():
        try:
            description, tags, text = _read_file(path)
        except OSError:
            continue
        name = _name_for(path)

        # Metadata-level match (name / description / tags) ranks first.
        metadata_blob = f"{name} {description} {', '.join(tags)}".lower()
        if query_lower in metadata_blob:
            scored.append(
                (
                    0,
                    {
                        "name": name,
                        "line_number": "0",
                        "excerpt": (description or name)[:200],
                        "match": "metadata",
                    },
                )
            )

        for i, line in enumerate(text.splitlines(), start=1):
            if query_lower in line.lower():
                scored.append(
                    (
                        1,
                        {
                            "name": name,
                            "line_number": str(i),
                            "excerpt": line.strip()[:200],
                            "match": "body",
                        },
                    )
                )

    scored.sort(key=lambda pair: pair[0])
    return [result for _, result in scored[:_MAX_SEARCH_RESULTS]]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    server.run()
