"""Benchmark runner v4 — GOA (MCP-assisted) vs Solo vs ECC (TASK.md §36–40).

Mechanical, agent-agnostic parts only: sandbox materialization, task
materialization, verification gates, metric collection, aggregation. The
agent-in-the-loop step (arm executor) is a pluggable callable, so the same
runner works with Claude Code, Codex, native GOA, or a stub in tests.

Every run appends one JSON row to results; aggregation is a pure function of
that file. No post-hoc editing of results.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from harness.toolkit import ExecutionToolkit
from harness.verification import VerificationEngine

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class RunResult:
    arm: str  # solo | ecc | goa
    task_id: str
    seed: int
    sandbox: str
    started_at: str
    duration_s: float
    # quality
    tests_pass: bool
    typecheck_pass: bool
    lint_pass: bool
    hidden_tests_pass: bool | None = None
    regression_count: int = 0
    security_findings: int = 0
    judge_score: float | None = None
    # efficiency
    total_tokens: int | None = None
    total_cost_usd: float | None = None
    llm_calls: int | None = None
    tool_calls: int | None = None
    retries: int = 0
    # provenance
    metrics_source: str = "mechanical"  # or "agent-telemetry" / "fallback"
    notes: str = ""


@dataclass
class Aggregate:
    arm: str
    runs: int
    success_rate: float
    mean_quality: float
    median_quality: float
    stdev_quality: float
    mean_duration_s: float
    mean_tokens: float | None
    mean_llm_calls: float | None
    mean_retries: float


QualityFn = Callable[[Path], dict[str, Any]]
"""Given a sandbox, return {tests_pass, typecheck_pass, lint_pass, ...}."""

ExecutorFn = Callable[[Path, str, int], dict[str, Any]]
"""Given (sandbox, task_text, seed) → execute the arm; return telemetry dict."""

# ---------------------------------------------------------------------------
# Mechanical gates (shared by all arms — the SAME gate judges everyone)
# ---------------------------------------------------------------------------


def mechanical_gate(sandbox: Path) -> dict[str, Any]:
    """Run the identical verification gate for any arm's sandbox."""
    toolkit = ExecutionToolkit(sandbox)
    engine = VerificationEngine(toolkit)
    report = engine.run()
    by_check = {c.check: c.ok for c in report.commands}
    return {
        "tests_pass": bool(by_check.get("tests")),
        "typecheck_pass": bool(by_check.get("typecheck")),
        "lint_pass": bool(by_check.get("lint")),
        "verification_summary": report.summary,
        "failure_category": (report.failure_category.value if report.failure_category else None),
    }


def hidden_tests_gate(sandbox: Path, hidden_test_dir: Path) -> bool | None:
    """Run hidden tests inside the sandbox (copied in, never shown to arms)."""
    if not hidden_test_dir.is_dir():
        return None
    target = sandbox / "hidden_tests"
    if target.exists():
        import shutil

        shutil.rmtree(target)
    import shutil

    shutil.copytree(hidden_test_dir, target)
    res = ExecutionToolkit(sandbox).run_command(
        f"{__import__('sys').executable} -m pytest hidden_tests -q", timeout_s=180
    )
    return res.ok


def security_scan(sandbox: Path) -> int:
    """Cheap deterministic security greps (finding count)."""
    patterns = [
        r"api[_-]?key\s*=\s*['\"]",
        r"secret\s*=\s*['\"]",
        r"password\s*=\s*['\"]",
        r"eval\(",
        r"exec\(",
        r"shell=True",
        r"SELECT .* \+ ",
        r"md5\(",
    ]
    import re

    hits = 0
    for f in sandbox.rglob("*.py"):
        if ".venv" in f.parts or "hidden_tests" in f.parts:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                hits += 1
    return hits


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class BenchmarkRunner:
    def __init__(
        self,
        tasks: list[dict[str, Any]],  # {task_id, text, seeds}
        executors: dict[str, ExecutorFn],
        sandbox_root: Path,
        hidden_test_dir: Path | None = None,
        results_path: Path | None = None,
    ) -> None:
        self.tasks = tasks
        self.executors = executors
        self.sandbox_root = sandbox_root
        self.hidden_test_dir = hidden_test_dir
        self.results_path = results_path or sandbox_root / "v4_results.json"

    def run_one(self, arm: str, task: dict[str, Any], seed: int) -> RunResult:
        executor = self.executors[arm]
        sandbox = self.sandbox_root / arm / f"{task['task_id']}-s{seed}"
        sandbox.mkdir(parents=True, exist_ok=True)
        # clean sandbox for re-runs — never mix artifacts
        if any(sandbox.iterdir()):
            import shutil

            shutil.rmtree(sandbox)
            sandbox.mkdir(parents=True)

        start = time.perf_counter()
        telemetry: dict[str, Any] = {}
        error: str | None = None
        try:
            telemetry = executor(sandbox, task["text"], seed) or {}
        except Exception as exc:  # noqa: BLE001 — a failed arm run is data, not a crash
            error = str(exc)
        duration = time.perf_counter() - start

        gate = mechanical_gate(sandbox)
        hidden = hidden_tests_gate(sandbox, self.hidden_test_dir) if self.hidden_test_dir else None
        return RunResult(
            arm=arm,
            task_id=task["task_id"],
            seed=seed,
            sandbox=str(sandbox),
            started_at=datetime.now(UTC).isoformat(),
            duration_s=round(duration, 2),
            tests_pass=bool(gate["tests_pass"]),
            typecheck_pass=bool(gate["typecheck_pass"]),
            lint_pass=bool(gate["lint_pass"]),
            hidden_tests_pass=hidden,
            security_findings=security_scan(sandbox),
            total_tokens=telemetry.get("total_tokens"),
            total_cost_usd=telemetry.get("total_cost_usd"),
            llm_calls=telemetry.get("llm_calls"),
            tool_calls=telemetry.get("tool_calls"),
            retries=telemetry.get("retries", 0),
            metrics_source=telemetry.get("metrics_source", "mechanical"),
            notes=error or telemetry.get("notes", ""),
        )

    def run_all(self, arms: list[str] | None = None) -> list[RunResult]:
        arms = arms or list(self.executors)
        results: list[RunResult] = []
        for task in self.tasks:
            for seed in task.get("seeds", [1]):
                for arm in arms:
                    result = self.run_one(arm, task, seed)
                    results.append(result)
                    self._append(result)
                    print(
                        f"[{arm}] {result.task_id} s{seed}: "
                        f"tests={'PASS' if result.tests_pass else 'FAIL'} "
                        f"({result.duration_s:.0f}s)"
                    )
        return results

    # -- persistence (append-only; aggregation is a pure function of it) -----

    def _append(self, result: RunResult) -> None:
        existing: list[dict[str, Any]] = []
        if self.results_path.is_file():
            existing = json.loads(self.results_path.read_text(encoding="utf-8"))
        existing.append(asdict(result))
        self.results_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Aggregation (pure — reads the results file)
# ---------------------------------------------------------------------------


def aggregate(results_path: Path, arm: str) -> Aggregate:
    rows = json.loads(results_path.read_text(encoding="utf-8"))
    rows = [r for r in rows if r["arm"] == arm]
    if not rows:
        return Aggregate(
            arm=arm,
            runs=0,
            success_rate=0.0,
            mean_quality=0.0,
            median_quality=0.0,
            stdev_quality=0.0,
            mean_duration_s=0.0,
            mean_tokens=None,
            mean_llm_calls=None,
            mean_retries=0.0,
        )
    quality = [
        (1.0 if r["tests_pass"] else 0.0) * 7
        + (1.0 if r["typecheck_pass"] else 0.0) * 2
        + (1.0 if r["lint_pass"] else 0.0) * 1
        for r in rows
    ]
    tokens = [r["total_tokens"] for r in rows if r.get("total_tokens") is not None]
    calls = [r["llm_calls"] for r in rows if r.get("llm_calls") is not None]
    return Aggregate(
        arm=arm,
        runs=len(rows),
        success_rate=sum(1 for r in rows if r["tests_pass"]) / len(rows),
        mean_quality=round(statistics.mean(quality), 2),
        median_quality=round(statistics.median(quality), 2),
        stdev_quality=round(statistics.stdev(quality), 2) if len(quality) > 1 else 0.0,
        mean_duration_s=round(statistics.mean(r["duration_s"] for r in rows), 1),
        mean_tokens=round(statistics.mean(tokens)) if tokens else None,
        mean_llm_calls=round(statistics.mean(calls), 1) if calls else None,
        mean_retries=round(statistics.mean(r.get("retries", 0) for r in rows), 2),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="benchmark-v4")
    parser.add_argument("--tasks", required=True, help="tasks JSON file")
    parser.add_argument(
        "--arm", action="append", required=True, help="executor module:attr (repeatable)"
    )
    parser.add_argument("--sandbox-root", default="benchmark/sandboxes/v4")
    parser.add_argument("--hidden-tests", default=None)
    parser.add_argument("--domain", default=None, help="restrict to one task_id (e.g. backend)")
    args = parser.parse_args(argv)

    tasks = json.loads(Path(args.tasks).read_text(encoding="utf-8"))
    if args.domain:
        for t in tasks:
            if "text_path" in t and not Path(t["text_path"]).is_file():
                t["text"] = ""
        tasks = [
            dict(
                t,
                text=Path(t["text_path"]).read_text(encoding="utf-8")
                if t.get("text_path")
                else t.get("text", ""),
            )
            for t in tasks
            if t["task_id"] == args.domain or args.domain is None
        ]
    else:
        tasks = [
            dict(
                t,
                text=Path(t["text_path"]).read_text(encoding="utf-8")
                if t.get("text_path")
                else t.get("text", ""),
            )
            for t in tasks
        ]
    executors: dict[str, ExecutorFn] = {}
    for spec in args.arm:
        name, attr = spec.split("=", 1)
        module_path, fn_name = attr.split(":", 1)
        import importlib

        executors[name] = getattr(importlib.import_module(module_path), fn_name)

    runner = BenchmarkRunner(
        tasks=tasks,
        executors=executors,
        sandbox_root=Path(args.sandbox_root),
        hidden_test_dir=Path(args.hidden_tests) if args.hidden_tests else None,
    )
    if not tasks:
        print("no tasks matched; nothing to run")
        return 1
    runner.run_all()
    for name in executors:
        agg = aggregate(runner.results_path, name)
        print(
            f"{name}: n={agg.runs} success={agg.success_rate:.0%} "
            f"quality={agg.mean_quality}±{agg.stdev_quality} "
            f"dur={agg.mean_duration_s}s tokens={agg.mean_tokens} calls={agg.mean_llm_calls}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
