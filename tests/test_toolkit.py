"""Tests for the execution tool layer (harness/toolkit.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.toolkit import ExecutionToolkit, ToolGate

BANNED = ["rm -rf /", "sudo rm", "DROP DATABASE"]


@pytest.fixture
def toolkit(tmp_path: Path) -> ExecutionToolkit:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("def main() -> None:\n    print('hi')\n")
    (root / "README.md").write_text("# demo\n")
    return ExecutionToolkit(root, banned_patterns=BANNED)


# --- path confinement -------------------------------------------------------


def test_read_file_within_root(toolkit: ExecutionToolkit) -> None:
    res = toolkit.read_file("src/app.py")
    assert res.ok
    assert "def main" in res.output


@pytest.mark.parametrize("bad", ["../outside.txt", "/etc/passwd", "~/.ssh/id_rsa", ""])
def test_path_escape_rejected(toolkit: ExecutionToolkit, bad: str) -> None:
    res = toolkit.read_file(bad)
    assert not res.ok


def test_symlink_escape_rejected(toolkit: ExecutionToolkit, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    link = toolkit.root / "link.txt"
    link.symlink_to(outside)
    assert not toolkit.read_file("link.txt").ok


# --- filesystem tools ---------------------------------------------------------


def test_write_and_edit_file(toolkit: ExecutionToolkit) -> None:
    w = toolkit.write_file("src/new.py", "x = 1\n")
    assert w.ok and "created" in w.output
    e = toolkit.edit_file("src/new.py", "x = 1", "x = 2")
    assert e.ok
    assert "x = 2" in toolkit.read_file("src/new.py").output


def test_edit_rejects_ambiguous_match(toolkit: ExecutionToolkit) -> None:
    toolkit.write_file("dup.txt", "a\na\n")
    res = toolkit.edit_file("dup.txt", "a", "b")
    assert not res.ok and "2 locations" in (res.error or "")


def test_delete_file_refuses_directory(toolkit: ExecutionToolkit) -> None:
    res = toolkit.delete_file("src")
    assert not res.ok


def test_search_files_name_and_content(toolkit: ExecutionToolkit) -> None:
    names = toolkit.search_files("*.py")
    assert names.ok and "src/app.py" in names.output
    contents = toolkit.search_files("print", regex=True)
    assert contents.ok and "src/app.py" in contents.output


# --- shell tools ---------------------------------------------------------------


def test_run_command_captures_output_and_exit_code(toolkit: ExecutionToolkit) -> None:
    res = toolkit.run_command("echo hello")
    assert res.ok and "hello" in res.output and res.exit_code == 0
    res2 = toolkit.run_command("false")
    assert not res2.ok and res2.exit_code == 1


def test_run_command_banned_patterns(toolkit: ExecutionToolkit) -> None:
    for cmd in ("echo rm -rf /", "echo DROP DATABASE x"):
        res = toolkit.run_command(cmd)
        assert not res.ok and "banned" in (res.error or "")


def test_run_command_cwd_confined(toolkit: ExecutionToolkit) -> None:
    res = toolkit.run_command("pwd")
    assert res.ok and str(toolkit.root) in res.output


# --- git tools ------------------------------------------------------------------


def test_git_show_rejects_injection(toolkit: ExecutionToolkit) -> None:
    res = toolkit.git_show("HEAD; rm -rf /")
    assert not res.ok and "invalid ref" in (res.error or "")


# --- ToolGate ---------------------------------------------------------------------


def test_gate_allows_reader_read_only(toolkit: ExecutionToolkit) -> None:
    gate = ToolGate.for_agent(toolkit, "reviewer")
    allowed = gate.allowed_tools()
    assert "read_file" in allowed and "write_file" not in allowed
    assert "run_command" in allowed  # reviewer may run tests


def test_gate_blocks_write_for_reviewer(toolkit: ExecutionToolkit) -> None:
    gate = ToolGate.for_agent(toolkit, "reviewer")
    with pytest.raises(PermissionError):
        gate.invoke("write_file", path="x.txt", content="nope")


def test_gate_blocks_destructive_for_coder(toolkit: ExecutionToolkit) -> None:
    toolkit.write_file("tmp.txt", "x")
    gate = ToolGate.for_agent(toolkit, "coder")
    with pytest.raises(PermissionError):
        gate.invoke("delete_file", path="tmp.txt")
    assert Path(toolkit.root / "tmp.txt").exists()


def test_gate_allows_destructive_for_refactor_cleaner(toolkit: ExecutionToolkit) -> None:
    toolkit.write_file("tmp.txt", "x")
    gate = ToolGate.for_agent(toolkit, "refactor_cleaner")
    assert gate.invoke("delete_file", path="tmp.txt").ok


def test_gate_counts_invocations(toolkit: ExecutionToolkit) -> None:
    gate = ToolGate.for_agent(toolkit, "coder")
    gate.invoke("read_file", path="README.md")
    gate.invoke("read_file", path="README.md")
    assert gate.invocation_counts["read_file"] == 2


def test_capability_overrides(toolkit: ExecutionToolkit) -> None:
    gate = ToolGate.for_agent(
        toolkit, "planner", capability_overrides={"planner": ["read", "execute"]}
    )
    assert "run_command" in gate.allowed_tools()


def test_unknown_tool(toolkit: ExecutionToolkit) -> None:
    gate = ToolGate.for_agent(toolkit, "coder")
    res = gate.invoke("does_not_exist")
    assert not res.ok
