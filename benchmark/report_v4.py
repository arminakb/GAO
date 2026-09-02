#!/usr/bin/env python3
"""Aggregate v4 benchmark results into a readable report (Phase 8).

Reads benchmark/sandboxes/v4/v4_results.json and prints a per-arm table with
quality (mechanical gate), efficiency, and variance. Pure function of the
results file — no post-hoc editing (TASK §39).
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

RESULTS = Path(
    sys.argv[1]
    if len(sys.argv) > 1
    else (
        "benchmark/results_v4.json"
        if Path("benchmark/results_v4.json").is_file()
        else "benchmark/sandboxes/v4/v4_results.json"
    )
)


def main() -> None:
    rows = json.loads(RESULTS.read_text(encoding="utf-8"))
    arms = sorted({r["arm"] for r in rows})
    print(f"runs: {len(rows)} | file: {RESULTS}\n")
    header = (
        f"{'arm':5} {'n':>2} {'tests%':>7} {'lint%':>6} {'type%':>6} "
        f"{'secrets':>7} {'dur_s':>7} {'agent_calls':>11} {'retries':>8}"
    )
    print(header)
    print("-" * len(header))
    for arm in arms:
        rs = [r for r in rows if r["arm"] == arm]
        n = len(rs)
        tests = sum(r["tests_pass"] for r in rs) / n * 100
        lint = sum(r["lint_pass"] for r in rs) / n * 100
        typ = sum(r["typecheck_pass"] for r in rs) / n * 100
        secrets = statistics.mean(r["security_findings"] for r in rs)
        dur = statistics.mean(r["duration_s"] for r in rs)
        calls = [r["llm_calls"] for r in rs if r.get("llm_calls") is not None]
        retries = statistics.mean(r.get("retries", 0) for r in rs)
        calls_s = f"{statistics.mean(calls):.1f}" if calls else "n/a"
        print(
            f"{arm:5} {n:>2} {tests:>6.0f}% {lint:>5.0f}% {typ:>5.0f}% "
            f"{secrets:>7.1f} {dur:>7.0f} {calls_s:>11} {retries:>8.2f}"
        )
    print()
    for arm in arms:
        for r in (r for r in rows if r["arm"] == arm):
            notes = (r.get("notes") or "").replace("\n", " ")[:110]
            print(
                f"{arm:5} {r['task_id']:9} s{r['seed']}: "
                f"tests={'P' if r['tests_pass'] else 'F'} "
                f"lint={'P' if r['lint_pass'] else 'F'} "
                f"type={'P' if r['typecheck_pass'] else 'F'} | {notes}"
            )


if __name__ == "__main__":
    main()
