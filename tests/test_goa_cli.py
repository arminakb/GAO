"""Tests for the GOA CLI (goa_cli.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

import goa_cli


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (root / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    return root


def test_init_prints_snippets(capsys: pytest.CaptureFixture[str]) -> None:
    assert goa_cli.main(["--workspace", ".", "init"]) == 0
    out = capsys.readouterr().out
    assert "claude mcp add goa" in out
    assert "[mcp_servers.goa]" in out


def test_analyze_trivial_is_solo(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert goa_cli.main(["--workspace", str(repo), "analyze", "Fix typo in README"]) == 0
    out = capsys.readouterr().out
    assert "trivial" in out
    assert "track      : solo" in out


def test_analyze_critical_fans_out(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    task = (
        "Redesign the architecture, rewrite the database schema with migration, "
        "harden security auth, fix the concurrency race, rework frontend components"
    )
    goa_cli.main(["--workspace", str(repo), "analyze", task])
    out = capsys.readouterr().out
    assert "track      : complex" in out
    assert "security_specialist" in out


def test_verify_green(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = goa_cli.main(["--workspace", str(repo), "verify"])
    assert rc == 0
    assert "GREEN" in capsys.readouterr().out


def test_verify_red_exit_code_and_recovery(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / "test_bad.py").write_text("def test_bad():\n    assert False\n")
    rc = goa_cli.main(["--workspace", str(repo), "verify"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "RED" in out
    assert "test_failure_resolver" in out


def test_status(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert goa_cli.main(["--workspace", str(repo), "status"]) == 0
    out = capsys.readouterr().out
    assert "toolchain  : python_pip" in out
