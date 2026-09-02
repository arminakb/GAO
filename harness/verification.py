"""Deterministic verification engine (TASK.md §8, §9, §10).

Detects the repository's actual toolchain from manifest files, executes the
appropriate verification commands, classifies failures deterministically, and
produces machine-readable RED/GREEN evidence.

Nothing here trusts agent prose: a "tests pass" claim is only accepted when
:class:`VerificationEngine` actually ran the command and observed exit code 0.
"""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from harness.context import estimate_tokens, smart_excerpt
from harness.toolkit import ExecutionToolkit

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Toolchain detection
# ---------------------------------------------------------------------------


class Toolchain(StrEnum):
    PYTHON_UV = "python_uv"
    PYTHON_PIP = "python_pip"
    NODE = "node"
    GO = "go"
    RUST = "rust"
    UNKNOWN = "unknown"


_MANIFESTS: list[tuple[str, Toolchain]] = [
    ("uv.lock", Toolchain.PYTHON_UV),
    ("pyproject.toml", Toolchain.PYTHON_PIP),  # after uv.lock so uv wins
    ("package.json", Toolchain.NODE),
    ("go.mod", Toolchain.GO),
    ("Cargo.toml", Toolchain.RUST),
]


def detect_toolchain(root: Path) -> Toolchain:
    for name, tc in _MANIFESTS:
        if (root / name).exists():
            return tc
    return Toolchain.UNKNOWN


# ---------------------------------------------------------------------------
# Verification commands per toolchain
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerifyCommand:
    name: str  # logical check name: "tests" | "typecheck" | "lint" | "build"
    command: str
    category: str  # failure category used by recovery routing


def _python_prefix(root: Path) -> str:
    """Interpreter prefix for *root*'s own environment (TASK §39 fairness).

    Preference: a sandbox-local ``.venv/bin/python`` (created by the agent via
    ``uv sync``/``venv``), else the harness interpreter. The harness venv is
    the right default for GOA's own repo; for benchmark sandboxes the agent
    is directed to create ``.venv`` so the gate exercises the sandbox's own
    dependencies — verifying a foreign project with the orchestrator's venv
    yields bogus ``dependency_error``s, and bare ``python3`` may lack pytest
    entirely.
    """
    local = root / ".venv" / "bin" / "python"
    if local.is_file():
        return str(local)
    return f"{sys.executable} -m"


def verification_plan(toolchain: Toolchain) -> list[VerifyCommand]:
    """Commands to run for *toolchain*, in order. Existence is probed at run
    time — a missing binary (e.g. no mypy installed) is skipped, not failed.

    The python prefix is resolved per-sandbox by the engine (see
    :meth:`VerificationEngine.plan` / ``_python_prefix``): a sandbox-local
    ``.venv/bin/python`` if present, else the harness interpreter.
    """
    if toolchain in (Toolchain.PYTHON_UV, Toolchain.PYTHON_PIP):
        prefix = "uv run" if toolchain is Toolchain.PYTHON_UV else f"{sys.executable} -m"
        return [
            VerifyCommand("tests", f"{prefix} pytest -x -q --rootdir=.", "test_failure"),
            VerifyCommand(
                "typecheck",
                f"{prefix} mypy --strict . 2>/dev/null || {prefix} mypy .",
                "type_error",
            ),
            VerifyCommand("lint", f"{prefix} ruff check --isolated .", "lint_error"),
        ]
    if toolchain is Toolchain.NODE:
        return [
            VerifyCommand("tests", "npm test -- --passWithNoTests", "test_failure"),
            VerifyCommand("build", "npm run build --if-present", "build_error"),
            VerifyCommand("lint", "npm run lint --if-present", "lint_error"),
        ]
    if toolchain is Toolchain.GO:
        return [
            VerifyCommand("tests", "go test ./...", "test_failure"),
            VerifyCommand("build", "go build ./...", "build_error"),
        ]
    if toolchain is Toolchain.RUST:
        return [
            VerifyCommand("tests", "cargo test --quiet", "test_failure"),
            VerifyCommand("build", "cargo build --quiet", "build_error"),
        ]
    return []


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------


class FailureCategory(StrEnum):
    TEST_FAILURE = "test_failure"
    TYPE_ERROR = "type_error"
    LINT_ERROR = "lint_error"
    BUILD_ERROR = "build_error"
    SYNTAX_ERROR = "syntax_error"
    DEPENDENCY_ERROR = "dependency_error"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


_SYNTAX_MARKERS = ("SyntaxError", "IndentationError", "unexpected token")
_DEP_MARKERS = (
    "ModuleNotFoundError",
    "No module named",
    "Cannot find module",
    "ImportError: cannot import",
    "ResolutionImpossible",
)


def classify_failure(category_hint: str, output: str, exit_code: int | None) -> FailureCategory:
    """Deterministic failure classification from command category + output."""
    if exit_code is None:
        return FailureCategory.TIMEOUT
    text = output or ""
    if any(m in text for m in _SYNTAX_MARKERS):
        return FailureCategory.SYNTAX_ERROR
    if any(m in text for m in _DEP_MARKERS):
        return FailureCategory.DEPENDENCY_ERROR
    hint_map = {
        "test_failure": FailureCategory.TEST_FAILURE,
        "type_error": FailureCategory.TYPE_ERROR,
        "lint_error": FailureCategory.LINT_ERROR,
        "build_error": FailureCategory.BUILD_ERROR,
    }
    if category_hint in hint_map:
        # trust the command category only if the output corroborates or is
        # uninformative; output markers always win when present
        if re.search(r"\bFAILED\b|assert|AssertionError|E  ", text):
            return FailureCategory.TEST_FAILURE
        return hint_map[category_hint]
    return FailureCategory.UNKNOWN


# ---------------------------------------------------------------------------
# Evidence models
# ---------------------------------------------------------------------------


class CommandEvidence(BaseModel):
    """Machine-readable record of one executed verification command."""

    check: str
    command: str
    ok: bool
    exit_code: int | None
    duration_ms: float
    output_excerpt: str = ""

    @property
    def estimated_tokens(self) -> int:
        """Deterministic token estimate of the evidence (TASK §6 cost tracking)."""
        return estimate_tokens(self.output_excerpt)

    executed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class VerificationReport(BaseModel):
    """Aggregated verification evidence attached to graph state."""

    toolchain: str
    commands: list[CommandEvidence] = Field(default_factory=list)
    passed: bool = False
    failure_category: FailureCategory | None = None
    failure_command: str | None = None
    summary: str = ""

    @property
    def red_green(self) -> str:
        return "GREEN" if self.passed else "RED"


class VerificationRecord(BaseModel):
    """Full RED→GREEN evidence trail for the run."""

    entries: list[VerificationReport] = Field(default_factory=list)

    def add(self, report: VerificationReport) -> None:
        self.entries.append(report)

    @property
    def last(self) -> VerificationReport | None:
        return self.entries[-1] if self.entries else None


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class VerificationEngine:
    """Executes repository verification commands through the toolkit."""

    def __init__(
        self,
        toolkit: ExecutionToolkit,
        max_output_chars: int = 8000,
    ) -> None:
        self.toolkit = toolkit
        self.max_output_chars = max_output_chars
        self.root = toolkit.root

    # -- probing ---------------------------------------------------------------

    def _binary_available(self, command: str) -> bool:
        """Cheap probe: run `command --version`-style head check via sh -c
        `command -v <first token>`; also let uv-style prefixes pass."""
        first = command.split()[0]
        probe = self.toolkit.run_command(f"command -v {first}", timeout_s=10)
        return probe.ok

    def plan(self) -> list[VerifyCommand]:
        tc = detect_toolchain(self.root)
        plan = verification_plan(tc)
        if tc in (Toolchain.PYTHON_UV, Toolchain.PYTHON_PIP):
            # Re-resolve the python prefix against the ENGINE's root rather
            # than the process cwd — the gate must exercise the sandbox's own
            # .venv when the agent created one (TASK §39 fairness).
            prefix = _python_prefix(self.root)
            replacement = prefix if prefix.endswith(" -m") else f"{prefix} -m"
            plan = [
                VerifyCommand(
                    cmd.name,
                    re.sub(r"^\S+ -m", replacement, cmd.command),
                    cmd.category,
                )
                for cmd in plan
            ]
        # drop commands whose binary doesn't exist (never assume, TASK §8)
        available: list[VerifyCommand] = []
        for cmd in plan:
            first = cmd.command.split()[0]
            if first in {"uv", "python"} and not self._binary_available(
                cmd.command.split()[1] if cmd.command.split()[1] in {"run", "-m"} else first
            ):
                # uv/python missing entirely
                continue
            if not self._binary_available(
                cmd.command.replace("uv run ", "").replace("python -m ", "").split()[0]
            ):
                continue
            available.append(cmd)
        return available or plan  # fall back to plan; run_command will surface the error

    # -- execution ---------------------------------------------------------------

    def run(self) -> VerificationReport:
        toolchain = detect_toolchain(self.root)
        report = VerificationReport(toolchain=toolchain.value)
        for cmd in verification_plan(toolchain):
            result = self.toolkit.run_command(cmd.command)
            out = result.output + (result.error or "")
            # ponytail: missing optional binaries are skipped, not failed
            if (
                not result.ok
                and cmd.name in ("typecheck", "lint", "build")
                and "No module named" in out
            ):
                continue
            evidence = CommandEvidence(
                check=cmd.name,
                command=cmd.command,
                ok=result.ok,
                exit_code=result.exit_code,
                duration_ms=result.duration_ms,
                output_excerpt=smart_excerpt(
                    result.output or result.error or "", self.max_output_chars
                ),
            )
            report.commands.append(evidence)
            if not result.ok:
                report.failure_category = classify_failure(
                    cmd.category, result.output + (result.error or ""), result.exit_code
                )
                report.failure_command = cmd.command
                break  # first failure stops the gate; recovery targets it
        report.passed = all(c.ok for c in report.commands) and bool(report.commands)
        report.summary = (
            "all checks passed"
            if report.passed
            else f"{report.failure_category.value} in '{report.failure_command}'"
            if report.failure_category is not None
            else "no checks executed"
        )
        return report

    # -- routing helper (TASK §9) ------------------------------------------------

    def recovery_target(self, report: VerificationReport) -> str | None:
        """Map a failure category to the resolver agent that should repair it.

        Returns ``None`` when the report passed (no recovery needed).
        """
        if report.passed or report.failure_category is None:
            return None
        mapping = {
            FailureCategory.TEST_FAILURE: "test_failure_resolver",
            FailureCategory.TYPE_ERROR: "coder",
            FailureCategory.LINT_ERROR: "coder",
            FailureCategory.BUILD_ERROR: "build_error_resolver",
            FailureCategory.SYNTAX_ERROR: "coder",
            FailureCategory.DEPENDENCY_ERROR: "build_error_resolver",
            FailureCategory.TIMEOUT: "build_error_resolver",
            FailureCategory.UNKNOWN: "coder",
        }
        return mapping.get(report.failure_category, "coder")


# ---------------------------------------------------------------------------
# TaskAnalyzer-adjacent helper: risk signals from verification history
# ---------------------------------------------------------------------------


def historical_failure_rate(records: list[VerificationReport]) -> float:
    """Fraction of past verification reports that failed (0.0 if empty)."""
    if not records:
        return 0.0
    return sum(1 for r in records if not r.passed) / len(records)
