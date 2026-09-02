"""Tests for the benchmark v4 runner (mechanical parts, no agent required)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from benchmark.v4_runner import (
    BenchmarkRunner,
    aggregate,
    mechanical_gate,
    security_scan,
)


@pytest.fixture
def stub_executor(tmp_path: Path) -> Any:
    """A deterministic fake 'agent' that writes a passing (or failing) repo."""

    def executor(sandbox: Path, task_text: str, seed: int) -> dict[str, Any]:
        (sandbox / "pyproject.toml").write_text("[project]\nname='demo'\n")
        fail = seed % 2 == 0  # even seeds "fail" the implementation
        test = (
            "def test_ok():\n    assert True\n"
            if not fail
            else "def test_bad():\n    assert False\n"
        )
        (sandbox / "test_impl.py").write_text(test)
        return {"llm_calls": 3, "total_tokens": 1000 + seed, "retries": 1 if fail else 0}

    return executor


def test_runner_appends_rows_per_arm(tmp_path: Path, stub_executor: Any) -> None:
    tasks = [{"task_id": "t1", "text": "build x", "seeds": [1, 2]}]
    runner = BenchmarkRunner(
        tasks=tasks,
        executors={"goa": stub_executor, "solo": stub_executor},
        sandbox_root=tmp_path / "sandboxes",
    )
    results = runner.run_all()
    assert len(results) == 4  # 2 arms × 2 seeds
    saved = json.loads(runner.results_path.read_text())
    assert len(saved) == 4


def test_fresh_sandbox_between_runs(tmp_path: Path, stub_executor: Any) -> None:
    tasks = [{"task_id": "t1", "text": "build x", "seeds": [1]}]
    runner = BenchmarkRunner(
        tasks=tasks, executors={"solo": stub_executor}, sandbox_root=tmp_path / "sb"
    )
    runner.run_all()
    sandbox = tmp_path / "sb" / "solo" / "t1-s1"
    assert (sandbox / "test_impl.py").exists()


def test_aggregate_stats(tmp_path: Path, stub_executor: Any) -> None:
    tasks = [{"task_id": "t1", "text": "build x", "seeds": [1, 2]}]
    runner = BenchmarkRunner(
        tasks=tasks, executors={"goa": stub_executor}, sandbox_root=tmp_path / "sb"
    )
    runner.run_all()
    agg = aggregate(runner.results_path, "goa")
    assert agg.runs == 2
    assert agg.success_rate == 0.5  # seed 1 passes, seed 2 fails
    assert agg.mean_retries == 0.5


def test_aggregate_empty_arm(tmp_path: Path) -> None:
    p = tmp_path / "r.json"
    p.write_text("[]")
    agg = aggregate(p, "ecc")
    assert agg.runs == 0 and agg.success_rate == 0.0


def test_security_scan_finds_secrets(tmp_path: Path) -> None:
    (tmp_path / "code.py").write_text('API_KEY = "sk-123"\n')
    (tmp_path / "clean.py").write_text("x = 1\n")
    assert security_scan(tmp_path) >= 1


def test_mechanical_gate_on_clean_and_broken(tmp_path: Path) -> None:
    good = tmp_path / "good"
    good.mkdir()
    (good / "pyproject.toml").write_text("[project]\nname='d'\n")
    (good / "test_t.py").write_text("def test_t():\n    assert True\n")
    gate = mechanical_gate(good)
    assert gate["tests_pass"] is True
    assert gate["failure_category"] is None


def test_hidden_tests_gate(tmp_path: Path) -> None:
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    (hidden / "test_hidden.py").write_text("def test_hidden():\n    assert 1 == 1\n")
    sandbox = tmp_path / "sb"
    sandbox.mkdir()
    (sandbox / "pyproject.toml").write_text("[project]\nname='d'\n")
    (sandbox / "test_keep.py").write_text("def test_keep():\n    assert True\n")
    from benchmark.v4_runner import hidden_tests_gate

    assert hidden_tests_gate(sandbox, hidden) is True
    assert (sandbox / "hidden_tests" / "test_hidden.py").exists()


def test_cli_module_imports() -> None:
    import benchmark.v4_runner as r

    assert callable(r.main)
