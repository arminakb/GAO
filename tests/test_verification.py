"""Tests for the deterministic verification engine (harness/verification.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.toolkit import ExecutionToolkit
from harness.verification import (
    FailureCategory,
    VerificationEngine,
    classify_failure,
    detect_toolchain,
    historical_failure_rate,
)


def make_toolkit(tmp_path: Path, manifests: dict[str, str]) -> ExecutionToolkit:
    root = tmp_path / "proj"
    root.mkdir()
    for name, content in manifests.items():
        (root / name).write_text(content)
    return ExecutionToolkit(root)


# --- toolchain detection ------------------------------------------------------


def test_detect_uv_toolchain(tmp_path: Path) -> None:
    tk = make_toolkit(tmp_path, {"uv.lock": "", "pyproject.toml": "[project]"})
    assert detect_toolchain(tk.root).value == "python_uv"


def test_detect_pip_toolchain(tmp_path: Path) -> None:
    tk = make_toolkit(tmp_path, {"pyproject.toml": "[project]"})
    assert detect_toolchain(tk.root).value == "python_pip"


def test_detect_node_toolchain(tmp_path: Path) -> None:
    tk = make_toolkit(tmp_path, {"package.json": "{}"})
    assert detect_toolchain(tk.root).value == "node"


def test_detect_unknown(tmp_path: Path) -> None:
    tk = make_toolkit(tmp_path, {})
    assert detect_toolchain(tk.root).value == "unknown"


# --- failure classification -----------------------------------------------------


def test_classify_test_failure_marker_wins() -> None:
    assert (
        classify_failure("type_error", "FAILED tests/test_x.py::test_a", 1)
        == FailureCategory.TEST_FAILURE
    )


def test_classify_dependency_marker_wins() -> None:
    assert (
        classify_failure("test_failure", "ModuleNotFoundError: No module named 'requests'", 1)
        == FailureCategory.DEPENDENCY_ERROR
    )


def test_classify_by_hint() -> None:
    assert classify_failure("lint_error", "ruff: 2 errors", 1) == FailureCategory.LINT_ERROR
    assert classify_failure("build_error", "compile failed", 2) == FailureCategory.BUILD_ERROR
    assert (
        classify_failure("test_failure", "AssertionError: 1 != 2", 1)
        == FailureCategory.TEST_FAILURE
    )


def test_classify_syntax_error() -> None:
    assert (
        classify_failure("test_failure", "SyntaxError: invalid syntax", 1)
        == FailureCategory.SYNTAX_ERROR
    )


def test_classify_timeout() -> None:
    assert classify_failure("test_failure", "", None) == FailureCategory.TIMEOUT


# --- engine on a real repository -------------------------------------------------


def test_engine_green_on_passing_repo(tmp_path: Path) -> None:
    root = tmp_path / "green"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (root / "test_ok.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n")
    tk = ExecutionToolkit(root)
    engine = VerificationEngine(tk)
    report = engine.run()
    assert report.passed
    assert report.red_green == "GREEN"
    assert {c.check for c in report.commands} >= {"tests"}
    assert engine.recovery_target(report) is None


def test_engine_red_on_failing_test_and_routes_to_resolver(tmp_path: Path) -> None:
    root = tmp_path / "red"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (root / "test_bad.py").write_text("def test_bad():\n    assert False\n")
    tk = ExecutionToolkit(root)
    engine = VerificationEngine(tk)
    report = engine.run()
    assert not report.passed
    assert report.red_green == "GREEN" or report.failure_category is not None
    assert report.failure_category == FailureCategory.TEST_FAILURE
    assert engine.recovery_target(report) == "test_failure_resolver"


def test_engine_records_evidence_fields(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (root / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    engine = VerificationEngine(ExecutionToolkit(root))
    report = engine.run()
    ev = report.commands[0]
    assert ev.command and ev.executed_at and ev.exit_code == 0


def test_historical_failure_rate() -> None:
    from harness.verification import VerificationReport

    r1 = VerificationReport(toolchain="python_uv", passed=True)
    r2 = VerificationReport(toolchain="python_uv", passed=False)
    assert historical_failure_rate([r1, r1, r2]) == pytest.approx(1 / 3)
    assert historical_failure_rate([]) == 0.0
