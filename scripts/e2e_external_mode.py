"""E2E validation of the External Agent Mode loop (run manually or via pytest -m e2e).

Demonstrates: analyze → route → verify(RED) → repair → fix → verify(GREEN) →
memory → status, all through the GOA MCP tool functions on a real sandbox repo.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import skills.servers.goa_mcp as goa


def run_e2e() -> None:
    root = Path(tempfile.mkdtemp()) / "shop"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='shop'\n")
    (root / "cart.py").write_text(
        "def apply_discount(price: int, pct: int) -> int:\n    return price - pct\n"
    )
    test_src = (
        "from cart import apply_discount\n\n\n"
        "def test_discount():\n"
        "    assert apply_discount(200, 10) == 180\n"
    )
    (root / "test_cart.py").write_text(test_src)

    goa._WORKSPACE = root
    goa._TOOLKIT = goa.ExecutionToolkit(root)
    goa._VERIFIER = goa.VerificationEngine(goa._TOOLKIT)
    goa._SESSIONS.clear()

    a = goa.goa_analyze_task("Fix the discount calculation bug in cart.apply_discount", "e2e")
    plan = goa.goa_route("Fix the discount calculation bug in cart.apply_discount", "e2e")
    print("1. analyze:", a["complexity"], "| 2. route:", plan["track"], plan["steps"])

    v1 = goa.goa_verify("e2e")
    print("3. verify:", v1["verdict"], "|", v1["summary"], "| cat:", v1["failure_category"])
    r1 = goa.goa_repair("e2e")
    print("4. repair:", r1["resolver"], "cycles:", r1["cycles_failed"])

    # The external coding agent applies this fix:
    (root / "cart.py").write_text(
        "def apply_discount(price: int, pct: int) -> int:\n    return price - price * pct // 100\n"
    )
    v2 = goa.goa_verify("e2e")
    print("6. verify:", v2["verdict"], "|", v2["summary"])

    goa.goa_record_memory(
        "successful_fix", "discount bug was subtraction; fixed to percentage math", ["cart"], "e2e"
    )
    print(
        "7. memory hits:",
        len(goa.goa_get_memory("discount bug")),
        "| status verdict:",
        goa.goa_get_status("e2e")["last_verdict"],
    )

    rec = goa._evidence_record()
    trail = [
        (e.red_green, e.failure_category.value if e.failure_category else None) for e in rec.entries
    ]
    print(
        "8. RED->GREEN trail:",
        trail,
    )


if __name__ == "__main__":
    run_e2e()
