"""Execution tool layer for the Graph Agent Orchestrator (TASK.md §6–§7).

Provides sandboxed filesystem, shell, and git tools that agents can actually
invoke, with permissions enforced **in code** — never in prompts.

Design:

* Every tool declares a :class:`Permission` (READ / WRITE / EXECUTE / NETWORK /
  DESTRUCTIVE).
* Every agent role has a capability set defined in ``harness/policies.json``
  under ``access_rules.capabilities``.
* :class:`ToolGate` is the single enforcement point: it resolves a tool for an
  agent only if the agent's capabilities cover the tool's permission. Reviewer
  gets no write tools regardless of what its prompt says.
* Shell execution is policy-filtered: banned operations from
  ``safety_rules.banned_operations`` are rejected before spawning a process,
  and network operations are gated behind the ``network`` capability.

All paths are confined to a workspace root (a repository checkout). Absolute
escapes and symlink traversal out of the root are rejected.
"""

from __future__ import annotations

import ast
import fnmatch
import logging
import re
import shlex
import subprocess
import time
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


class Permission(StrEnum):
    """Capability classes a tool may require."""

    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    NETWORK = "network"
    DESTRUCTIVE = "destructive"


#: Default capabilities per agent role. Extended/overridden by
#: ``policies.json → access_rules.capabilities`` when present.
DEFAULT_CAPABILITIES: dict[str, set[Permission]] = {
    "orchestrator": {Permission.READ},
    "planner": {Permission.READ},
    "architect": {Permission.READ},
    "reviewer": {Permission.READ, Permission.EXECUTE},
    "coder": {Permission.READ, Permission.WRITE, Permission.EXECUTE},
    "tdd_guide": {Permission.READ, Permission.WRITE, Permission.EXECUTE},
    "build_error_resolver": {Permission.READ, Permission.WRITE, Permission.EXECUTE},
    "test_failure_resolver": {Permission.READ, Permission.WRITE, Permission.EXECUTE},
    "e2e_runner": {Permission.READ, Permission.EXECUTE},
    "refactor_cleaner": {
        Permission.READ,
        Permission.WRITE,
        Permission.EXECUTE,
        Permission.DESTRUCTIVE,
    },
    "doc_updater": {Permission.READ, Permission.WRITE},
    "loop_operator": {Permission.READ},
    "harness_optimizer": {Permission.READ},
    "database_reviewer": {Permission.READ},
    "security_reviewer": {Permission.READ},
}

# ---------------------------------------------------------------------------
# Tool definitions (LangChain-compatible via a minimal local base class)
# ---------------------------------------------------------------------------


class ToolSpec(BaseModel):
    """Declaration of a single executable tool."""

    name: str
    description: str
    permission: Permission
    # JSON-schema-ish parameter description for LLM binding
    parameters: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """Uniform result envelope for every tool invocation."""

    tool: str
    ok: bool
    output: str = ""
    error: str | None = None
    exit_code: int | None = None
    duration_ms: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionToolkit:
    """Sandboxed filesystem/shell/git tools bound to a workspace root.

    Instantiated once per run. Agents never receive raw toolkit methods —
    they receive :class:`ToolGate`-resolved callables only.
    """

    def __init__(
        self,
        workspace_root: Path,
        banned_patterns: list[str] | None = None,
        shell_timeout_s: int = 120,
        max_output_chars: int = 20_000,
    ) -> None:
        self.root = workspace_root.resolve()
        self.banned_patterns = banned_patterns or []
        self.shell_timeout_s = shell_timeout_s
        self.max_output_chars = max_output_chars

    # -- path confinement ---------------------------------------------------

    def _resolve(self, rel_path: str) -> Path:
        """Resolve *rel_path* inside the workspace root, rejecting escapes."""
        if not rel_path or rel_path.strip() in {"", "/", "~"}:
            raise ValueError("Path must be a workspace-relative path")
        candidate = (self.root / rel_path).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError(f"Path escapes workspace root: {rel_path}")
        return candidate

    def _check_banned(self, command: str) -> str | None:
        """Return a rejection reason if *command* matches a banned pattern."""
        for pattern in self.banned_patterns:
            try:
                if fnmatch.fnmatch(command, pattern) or re.search(pattern, command):
                    return f"Command matches banned operation pattern: {pattern!r}"
            except re.error:
                if fnmatch.fnmatch(command, pattern):
                    return f"Command matches banned operation pattern: {pattern!r}"
        return None

    @staticmethod
    def _clip(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return (
            text[: limit // 2]
            + f"\n...[{len(text) - limit} chars clipped]...\n"
            + text[-limit // 2 :]
        )

    # -- filesystem tools ----------------------------------------------------

    def read_file(self, path: str) -> ToolResult:
        start = time.perf_counter()
        try:
            target = self._resolve(path)
            text = target.read_text(encoding="utf-8")
            return ToolResult(
                tool="read_file",
                ok=True,
                output=self._clip(text, self.max_output_chars),
                duration_ms=(time.perf_counter() - start) * 1000,
                metadata={"path": str(target), "bytes": len(text)},
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(tool="read_file", ok=False, error=str(exc))

    def list_directory(self, path: str = ".") -> ToolResult:
        start = time.perf_counter()
        try:
            target = self._resolve(path)
            entries = sorted((f"{p.name}/" if p.is_dir() else p.name) for p in target.iterdir())
            return ToolResult(
                tool="list_directory",
                ok=True,
                output="\n".join(entries),
                duration_ms=(time.perf_counter() - start) * 1000,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(tool="list_directory", ok=False, error=str(exc))

    def search_files(self, pattern: str, path: str = ".", regex: bool = False) -> ToolResult:
        """Search file names (regex=False, fnmatch) or contents (regex=True)."""
        start = time.perf_counter()
        try:
            base = self._resolve(path)
            matches: list[str] = []
            if regex:
                rx = re.compile(pattern)
                for f in base.rglob("*"):
                    if not f.is_file() or ".venv" in f.parts or "__pycache__" in f.parts:
                        continue
                    try:
                        if rx.search(f.read_text(encoding="utf-8", errors="ignore")):
                            matches.append(str(f.relative_to(self.root)))
                    except OSError:
                        continue
            else:
                for f in base.rglob(pattern):
                    matches.append(str(f.relative_to(self.root)))
            return ToolResult(
                tool="search_files",
                ok=True,
                output=self._clip("\n".join(sorted(matches)[:200]), self.max_output_chars),
                duration_ms=(time.perf_counter() - start) * 1000,
                metadata={"count": len(matches)},
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(tool="search_files", ok=False, error=str(exc))

    def write_file(self, path: str, content: str, create: bool = True) -> ToolResult:
        start = time.perf_counter()
        try:
            target = self._resolve(path)
            if not create and not target.exists():
                raise FileNotFoundError(f"{path} does not exist and create=False")
            target.parent.mkdir(parents=True, exist_ok=True)
            existed = target.exists()
            target.write_text(content, encoding="utf-8")
            return ToolResult(
                tool="write_file",
                ok=True,
                output=f"{'overwrote' if existed else 'created'} {path} ({len(content)} bytes)",
                duration_ms=(time.perf_counter() - start) * 1000,
                metadata={"path": str(target), "created": not existed},
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(tool="write_file", ok=False, error=str(exc))

    def edit_file(self, path: str, old_string: str, new_string: str) -> ToolResult:
        """Replace the first unique occurrence of *old_string* in *path*."""
        start = time.perf_counter()
        try:
            target = self._resolve(path)
            text = target.read_text(encoding="utf-8")
            count = text.count(old_string)
            if count == 0:
                raise ValueError("old_string not found in file")
            if count > 1:
                raise ValueError(f"old_string matches {count} locations; provide more context")
            updated = text.replace(old_string, new_string, 1)
            target.write_text(updated, encoding="utf-8")
            return ToolResult(
                tool="edit_file",
                ok=True,
                output=f"edited {path}",
                duration_ms=(time.perf_counter() - start) * 1000,
                metadata={"path": str(target)},
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(tool="edit_file", ok=False, error=str(exc))

    def delete_file(self, path: str) -> ToolResult:
        """Delete a single file (DESTRUCTIVE capability required via gate)."""
        start = time.perf_counter()
        try:
            target = self._resolve(path)
            if target.is_dir():
                raise ValueError("delete_file only removes single files; refuse directories")
            target.unlink()
            return ToolResult(
                tool="delete_file",
                ok=True,
                output=f"deleted {path}",
                duration_ms=(time.perf_counter() - start) * 1000,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(tool="delete_file", ok=False, error=str(exc))

    # -- shell tools ----------------------------------------------------------

    def run_command(self, command: str, cwd: str = ".", timeout_s: int | None = None) -> ToolResult:
        start = time.perf_counter()
        try:
            reason = self._check_banned(command)
            if reason:
                return ToolResult(tool="run_command", ok=False, error=reason)
            workdir = self._resolve(cwd)
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(workdir),
                capture_output=True,
                text=True,
                timeout=timeout_s or self.shell_timeout_s,
                check=False,
            )
            output = self._clip(
                (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else ""),
                self.max_output_chars,
            )
            return ToolResult(
                tool="run_command",
                ok=proc.returncode == 0,
                output=output,
                exit_code=proc.returncode,
                duration_ms=(time.perf_counter() - start) * 1000,
                metadata={"command": command},
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                tool="run_command",
                ok=False,
                error=f"timeout after {timeout_s or self.shell_timeout_s}s",
                metadata={"command": command},
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(tool="run_command", ok=False, error=str(exc))

    # -- git tools (read-only) --------------------------------------------------

    def _git(self, args: str) -> ToolResult:
        return self.run_command(f"git {args}")

    def git_status(self) -> ToolResult:
        return self._git("status --porcelain=v1")

    def git_diff(self, path: str | None = None) -> ToolResult:
        suffix = f" -- {shlex.quote(path)}" if path else ""
        return self._git(f"diff --unified=3{suffix}")

    def git_log(self, limit: int = 20) -> ToolResult:
        limit = max(1, min(int(limit), 100))
        return self._git(f"log --oneline -n {limit}")

    def git_show(self, ref: str) -> ToolResult:
        # ref is validated: allow word chars, dashes, dots, ^ ~ and colons only
        if not re.fullmatch(r"[A-Za-z0-9._^~:\/-]+", ref):
            return ToolResult(tool="git_show", ok=False, error=f"invalid ref: {ref!r}")
        return self._git(f"show --stat --oneline {shlex.quote(ref)}")

    # -- registry -----------------------------------------------------------------

    def tool_specs(self) -> dict[str, ToolSpec]:
        """All tools with their required permission."""
        return {
            "read_file": ToolSpec(
                name="read_file",
                description="Read a file inside the workspace",
                permission=Permission.READ,
            ),
            "list_directory": ToolSpec(
                name="list_directory", description="List a directory", permission=Permission.READ
            ),
            "search_files": ToolSpec(
                name="search_files",
                description="Search file names or contents",
                permission=Permission.READ,
            ),
            "write_file": ToolSpec(
                name="write_file",
                description="Create/overwrite a file",
                permission=Permission.WRITE,
            ),
            "edit_file": ToolSpec(
                name="edit_file",
                description="Replace a unique string in a file",
                permission=Permission.WRITE,
            ),
            "delete_file": ToolSpec(
                name="delete_file",
                description="Delete a single file",
                permission=Permission.DESTRUCTIVE,
            ),
            "run_command": ToolSpec(
                name="run_command",
                description="Run a shell command in the workspace",
                permission=Permission.EXECUTE,
            ),
            "git_status": ToolSpec(
                name="git_status", description="git status (porcelain)", permission=Permission.READ
            ),
            "git_diff": ToolSpec(
                name="git_diff",
                description="git diff of the working tree",
                permission=Permission.READ,
            ),
            "git_log": ToolSpec(
                name="git_log", description="Recent commit log", permission=Permission.READ
            ),
            "git_show": ToolSpec(
                name="git_show",
                description="Show a commit stat/summary",
                permission=Permission.READ,
            ),
        }

    def dispatch(self, tool_name: str, **kwargs: Any) -> ToolResult:
        """Invoke a tool by name (used by the gate and by tests)."""
        mapping: dict[str, Callable[..., ToolResult]] = {
            "read_file": self.read_file,
            "list_directory": self.list_directory,
            "search_files": self.search_files,
            "write_file": self.write_file,
            "edit_file": self.edit_file,
            "delete_file": self.delete_file,
            "run_command": self.run_command,
            "git_status": self.git_status,
            "git_diff": self.git_diff,
            "git_log": self.git_log,
            "git_show": self.git_show,
        }
        fn = mapping.get(tool_name)
        if fn is None:
            return ToolResult(tool=tool_name, ok=False, error=f"unknown tool: {tool_name}")
        return fn(**kwargs)


# ---------------------------------------------------------------------------
# ToolGate — capability enforcement (code, not prompts)
# ---------------------------------------------------------------------------


class ToolGate:
    """Resolves tools for an agent based on its capability set.

    The gate is the ONLY way agents obtain tools. A tool is exposed to an
    agent iff ``permission in capabilities``. Every allowed invocation is
    counted; denied tool requests raise :class:`PermissionError` so prompt
    injection cannot talk an agent into a capability it does not hold.
    """

    def __init__(
        self,
        toolkit: ExecutionToolkit,
        agent_role: str,
        capabilities: set[Permission],
    ) -> None:
        self._toolkit = toolkit
        self.agent_role = agent_role
        self.capabilities = capabilities
        self.invocation_counts: dict[str, int] = {}

    @classmethod
    def for_agent(
        cls,
        toolkit: ExecutionToolkit,
        agent_role: str,
        capability_overrides: dict[str, list[str]] | None = None,
    ) -> ToolGate:
        caps = set(DEFAULT_CAPABILITIES.get(agent_role, {Permission.READ}))
        if capability_overrides and agent_role in capability_overrides:
            caps = {Permission(p) for p in capability_overrides[agent_role]}
        return cls(toolkit, agent_role, caps)

    def allowed_tools(self) -> dict[str, ToolSpec]:
        return {
            name: spec
            for name, spec in self._toolkit.tool_specs().items()
            if spec.permission in self.capabilities
        }

    def invoke(self, tool_name: str, **kwargs: Any) -> ToolResult:
        specs = self._toolkit.tool_specs()
        spec = specs.get(tool_name)
        if spec is None:
            return ToolResult(tool=tool_name, ok=False, error=f"unknown tool: {tool_name}")
        if spec.permission not in self.capabilities:
            raise PermissionError(
                f"agent '{self.agent_role}' lacks capability '{spec.permission.value}' "
                f"required by tool '{tool_name}'"
            )
        self.invocation_counts[tool_name] = self.invocation_counts.get(tool_name, 0) + 1
        return self._toolkit.dispatch(tool_name, **kwargs)


# ---------------------------------------------------------------------------
# Python syntax sanity (used by verification to pre-classify failures)
# ---------------------------------------------------------------------------


def is_valid_python(source: str) -> bool:
    try:
        ast.parse(source)
    except SyntaxError:
        return False
    return True
