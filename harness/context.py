"""Context construction helpers (TASK.md §6, §19).

Deterministic token estimation and relevance-preserving excerpting.

TASK §19 forbids crude truncation that destroys important evidence: for
command output, the failure summary (FAILED lines, tracebacks) lives at the
*tail*, so naive head-clipping removes exactly the evidence recovery needs.
:func:`smart_excerpt` keeps both the head (setup/context) and the tail
(evidence) with an explicit clipped marker in between.

Nothing here trusts an LLM — every function is deterministic.
"""

from __future__ import annotations

#: Average characters per token used for deterministic estimation. Good
#: enough for budget accounting; not a tokenizer (TASK §6 wants cost *tracked*,
#: not perfectly predicted).
_CHARS_PER_TOKEN = 4

#: Lines that mark evidence worth keeping even when the middle is clipped.
_EVIDENCE_MARKERS = ("FAILED", "ERROR", "Error", "assert", "Traceback", "warning")


def estimate_tokens(text: str) -> int:
    """Deterministic token estimate for *text* (chars / 4, min 1)."""
    return max(1, (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN)


def smart_excerpt(text: str, limit: int) -> str:
    """Relevance-preserving excerpt of *text* to at most *limit* chars.

    Keeps the head and the tail (where failure evidence lives). If the
    dropped middle contains evidence markers, they are echoed in the
    clipping marker so recovery never loses the failure kind.
    """
    if len(text) <= limit:
        return text
    half = max(limit // 2, 1)
    middle = text[half:-half]
    markers = sorted({m for m in _EVIDENCE_MARKERS if m in middle})
    note = f"; evidence markers present: {', '.join(markers)}" if markers else ""
    return text[:half] + f"\n...[{len(middle)} chars clipped{note}]...\n" + text[-half:]
