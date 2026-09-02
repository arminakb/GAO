"""Phase 8 arm executors for the v4 benchmark runner (TASK.md §36–40).

Three arms, identical spec, identical model provider (OpenCode CLI with the
same model), fresh sandboxes:

* solo — spec only, bare agent
* ecc  — ECC checklist methodology prompt
* goa  — GOA Orchestrator via the goa MCP tools (the deterministic core:
  analyze → route → implement → verify → repair loop), executed by the same
  underlying OpenCode agent

The GOA arm drives the ACTUAL orchestrator end-to-end: every step calls
`skills/servers/goa_mcp.py` functions directly (in-process MCP equivalent,
same code path the MCP server exposes), so orchestration decisions, budget
enforcement, and verification evidence are real.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

OPENCODE_MODEL = "opencode/muse-spark-1.2-contributor-free"

ECC_PROMPT = (
    "You are a disciplined software engineering agent following the ECC "
    "checklist methodology. Work inside the current directory (your sandbox). "
    "First read SPEC.md fully. Then: write a short PLAN.md checklist (coding "
    "standards, TDD red-green-refactor, security review, silent-failure hunt, "
    "confidence-gated self-review); write acceptance tests BEFORE "
    "implementation; record red-phase evidence in PLAN.md; implement; run a "
    "confidence-gated (>80% confidence) self-review and record findings with "
    "file:line in REVIEW.md (zero findings is valid); write README.md with run "
    "instructions. Verify personally before claiming done: run the exact gate "
    "commands from SPEC.md deliverables and fix failures. Never fabricate "
    "verification results; a claim requires demonstrated evidence."
)

SOLO_PROMPT = (
    "Build the project described in SPEC.md (in the current directory) "
    "completely. All deliverables in the spec are required."
)


def _opencode_run(sandbox: Path, prompt: str, timeout_s: int = 3600) -> tuple[int, str]:
    """Run the OpenCode agent inside *sandbox*; return (exit_code, tail).

    Sandbox isolation (TASK §39 — fresh environments): `opencode run` resolves
    its project root from the PWD env var, which subprocess(cwd=...) does NOT
    update — the agent then silently works in the caller's repo instead of the
    sandbox. Fix: pass --dir explicitly (absolute — opencode rejects relative
    paths) AND override PWD to the sandbox.
    """
    sandbox = sandbox.resolve()
    env = os.environ.copy()
    env["PWD"] = str(sandbox)
    proc = subprocess.run(
        [
            "opencode",
            "run",
            "--dir",
            str(sandbox),
            prompt,
            "--model",
            OPENCODE_MODEL,
        ],
        cwd=str(sandbox),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    return proc.returncode, (proc.stdout + proc.stderr)[-4000:]


def _strip_fence(text: str) -> str:
    """Remove a markdown code fence wrapper if present."""
    m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    return m.group(1) if m else text


def _extract_json(text: str) -> dict[str, Any] | None:
    """Pull the last JSON object out of agent output (best-effort)."""
    for match in reversed(list(re.finditer(r"\{.*\}", text, re.DOTALL))):
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
    return None


# ---------------------------------------------------------------------------
# Shared agent-arm executor factory
# ---------------------------------------------------------------------------


def make_prompt_arm(condition: str, prompt: str) -> Callable[[Path, str, int], dict[str, Any]]:
    """Solo/ECC arms: methodology lives in the prompt, agent runs free."""

    def executor(sandbox: Path, task_text: str, seed: int) -> dict[str, Any]:
        (sandbox / "SPEC.md").write_text(task_text, encoding="utf-8")
        start = time.perf_counter()
        code, tail = _opencode_run(sandbox, prompt)
        return {
            "total_tokens": None,  # external CLI does not expose token counts
            "total_cost_usd": None,
            "llm_calls": None,
            "tool_calls": None,
            "retries": 0,
            "metrics_source": "agent-telemetry" if code == 0 else "fallback",
            "notes": "" if code == 0 else f"opencode exit {code}: {tail[-500:]}",
            "_duration_s": time.perf_counter() - start,
        }

    executor.__name__ = f"executor_{condition}"
    return executor


executor_solo = make_prompt_arm("solo", SOLO_PROMPT)
executor_ecc = make_prompt_arm("ecc", ECC_PROMPT)


# ---------------------------------------------------------------------------
# GOA arm — drives the actual orchestrator (MCP tools, in-process)
# ---------------------------------------------------------------------------


def goa_drive_agent(sandbox: Path, directive: str, timeout_s: int = 3600) -> int:
    """The external execution engine: OpenCode acting on a GOA directive."""
    code, _ = _opencode_run(sandbox, directive, timeout_s=timeout_s)
    return code


def executor_goa(sandbox: Path, task_text: str, seed: int) -> dict[str, Any]:
    """GOA Orchestrator arm: the deterministic core runs the loop.

    Uses the same functions the MCP server exposes (in-process import —
    identical code path). The orchestrator decides routing, enforces the
    verify budget, and produces the RED→GREEN evidence trail; the external
    agent (OpenCode) is the implementation engine.
    """
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    (sandbox / "SPEC.md").write_text(task_text, encoding="utf-8")

    import skills.servers.goa_mcp as goa

    goa._WORKSPACE = sandbox.resolve()
    goa._TOOLKIT = goa.ExecutionToolkit(sandbox.resolve())
    goa._VERIFIER = goa.VerificationEngine(goa._TOOLKIT)
    goa._SESSIONS.clear()
    sid = f"bench-{seed}"

    t0 = time.perf_counter()
    tool_calls = 0
    analysis = goa.goa_analyze_task("Build the project described in SPEC.md", sid)
    tool_calls += 1
    route = goa.goa_route("Build the project described in SPEC.md", sid)
    tool_calls += 1

    # Solo/complex routing only changes how many guided cycles we allow; the
    # plan is recorded as evidence either way.
    plan_steps = route.get("steps", [])
    retries = 0
    agent_runs = 0
    max_cycles = 2 if route.get("track") == "solo" else 3

    directive = (
        "Work inside the current directory. Read SPEC.md fully and build the "
        "complete project it describes. All deliverables in the spec are "
        "required. Set up the project's own environment: run `uv sync` (or "
        "`python3 -m venv .venv && .venv/bin/pip install <deps>`) so that "
        "`.venv` exists with all dependencies — the orchestrator verifies "
        "mechanically with `.venv/bin/python -m pytest -q` and "
        "`.venv/bin/python -m ruff check --isolated .` from a clean state. "
        "Orchestration plan: " + json.dumps(plan_steps) + "."
    )

    trail: list[str] = []
    for _cycle in range(max_cycles):
        goa_drive_agent(sandbox, directive)
        agent_runs += 1
        verify = goa.goa_verify(sid)
        tool_calls += 1
        trail.append(verify["verdict"])
        if verify["passed"]:
            break
        repair = goa.goa_repair(sid)
        tool_calls += 1
        retries += 1
        failure_cmd = repair.get("failure_command") or "the failing check"
        excerpt = (repair.get("evidence_excerpt") or "")[:2000]
        directive = (
            f"Work inside the current directory. The orchestrator's mechanical "
            f"verification is RED (category: {repair.get('failure_category')}). "
            f"Failing command: {failure_cmd}. Evidence excerpt:\n{excerpt}\n"
            "Fix the failures minimally without removing tests. Do not start "
            "over. The orchestrator will re-verify mechanically."
        )
    else:
        # budget exhausted: last attempt still happened; trail already has REDs
        pass

    review = goa.goa_review(sid)
    tool_calls += 1

    return {
        "total_tokens": None,
        "total_cost_usd": None,
        "llm_calls": agent_runs,
        "tool_calls": tool_calls,
        "retries": retries,
        "metrics_source": "agent-telemetry",
        "notes": (
            f"track={route.get('track')} complexity={analysis.get('complexity')} "
            f"trail={'->'.join(trail)} review={review.get('recommendation', '')[:80]}"
        ),
        "_duration_s": time.perf_counter() - t0,
        "_goa_trail": trail,
        "_goa_route": {"track": route.get("track"), "steps": plan_steps},
    }
