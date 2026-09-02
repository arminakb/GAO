"""Task complexity / risk classifier (TASK.md §4) and adaptive graph selection
(TASK.md §3, §5).

Deterministic first: signal scoring from the task text, repository shape, and
verification history. An LLM-assist hook exists for ambiguity, but the default
path needs NO API key — essential for External Agent Mode where GOA must work
as a pure MCP service beside the user's coding agent.

The analyzer estimates: complexity, files likely affected, architectural /
database / API / frontend / security / concurrency impact, test requirements,
regression risk, blast radius, and produces a TRIVIAL..CRITICAL classification
plus a confidence score. The router then maps the class to the minimum
orchestration plan:

    TRIVIAL/SIMPLE  → solo path (one coder pass + verification)
    MEDIUM          → planner + coder + verification + review
    COMPLEX/CRITICAL→ planner + specialist fan-out + coder + verify + review
                      + bounded repair loop
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Classification enums
# ---------------------------------------------------------------------------


class Complexity(StrEnum):
    TRIVIAL = "trivial"
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"
    CRITICAL = "critical"


class ImpactDimension(StrEnum):
    ARCHITECTURE = "architecture"
    DATABASE = "database"
    API = "api"
    FRONTEND = "frontend"
    SECURITY = "security"
    CONCURRENCY = "concurrency"
    TESTING = "testing"


# Keyword signals per dimension. Ordered; first match wins within a dimension.
_DIMENSION_KEYWORDS: dict[ImpactDimension, list[str]] = {
    ImpactDimension.ARCHITECTURE: [
        "refactor the architecture",
        "module boundaries",
        "design the system",
        "restructure",
        "introduce a service",
        "extract a library",
    ],
    ImpactDimension.DATABASE: [
        "migration",
        "schema",
        "sql",
        "database",
        "postgres",
        "sqlite",
        "alembic",
        "orm model",
        "table",
    ],
    ImpactDimension.API: [
        "endpoint",
        "api",
        "rest",
        "graphql",
        "route",
        "controller",
        "request handler",
        "openapi",
    ],
    ImpactDimension.FRONTEND: [
        "react",
        "component",
        "css",
        "frontend",
        "ui",
        "page",
        "button",
        "form",
        "layout",
        "browser",
    ],
    ImpactDimension.SECURITY: [
        "security",
        "auth",
        "authentication",
        "authorization",
        "password",
        "token",
        "secret",
        "encryption",
        "vulnerability",
        "injection",
        "sanitize",
        "xss",
        "csrf",
    ],
    ImpactDimension.CONCURRENCY: [
        "race condition",
        "concurrent",
        "thread",
        "lock",
        "mutex",
        "asyncio",
        "parallel",
        "atomic",
        "deadlock",
    ],
    ImpactDimension.TESTING: [
        "test",
        "pytest",
        "coverage",
        "e2e",
        "regression",
    ],
}

# Scale signals → weight toward higher complexity
_COMPLEXITY_BOOSTS: list[tuple[str, int]] = [
    (r"\b(multiple|several|all)\s+(modules|services|files|components)\b", 2),
    (r"\bacross (the|all)\b", 2),
    (r"\b(migrate|migration)\b", 2),
    (r"\b(redesign|re architect|re-architect|rewrite)\b", 3),
    (r"\b(breaking change|backward compat\w*|deprecat\w+)\b", 2),
    (r"\b(security|vulnerab\w+|exploit)\b", 2),
    (r"\b(concurren\w+|race|deadlock|lock)\b", 2),
    (r"\b(integrate|integration)\b", 1),
    (r"\b(performance|optimi[sz]e|latency|throughput)\b", 1),
]

_TRIVIAL_SIGNALS: list[tuple[str, int]] = [
    (r"\btypo\b", 3),
    (r"\brename\b", 2),
    (r"\bcomment\b", 2),
    (r"\bdocstring\b", 2),
    (r"\bformatting\b|\blint fix\b", 2),
    (r"\b(one[- ]liner|single line)\b", 3),
    (r"\bfix (the )?(spelling|word)\b", 3),
    (r"\bupdate (the )?(readme|changelog|docs?)\b", 2),
    (r"\badd (a )?(log|print|type hint)\b", 2),
    (r"\bversion bump\b|\bbump version\b", 3),
]

_SIMPLE_SIGNALS: list[tuple[str, int]] = [
    (r"\bfix\b", 2),
    (r"\badd\b", 1),
    (r"\bimplement\b", 2),
    (r"\bwrite\b", 1),
    (r"\brefactor\b", 2),
    (r"\bbug\b", 2),
    (r"\bfunction\b|\bmethod\b|\bclass\b", 1),
]


class TaskAnalysis(BaseModel):
    """Machine-readable result of task analysis (consumed by router + MCP)."""

    task: str
    complexity: Complexity
    confidence: float = Field(ge=0.0, le=1.0)
    score: int = 0
    estimated_files_affected: int = 1
    impact_dimensions: list[ImpactDimension] = Field(default_factory=list)
    blast_radius: str = "local"  # local | module | cross-module | global
    security_risk: bool = False
    database_impact: bool = False
    concurrency_risk: bool = False
    test_required: bool = True
    regression_risk: float = Field(ge=0.0, le=1.0, default=0.0)
    estimated_tokens: int = 0
    historical_failure_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    signals: dict[str, Any] = Field(default_factory=dict)


class OrchestrationPlan(BaseModel):
    """The minimum orchestration plan for a task — what the graph executes."""

    track: str  # solo | medium | complex
    steps: list[str] = Field(default_factory=list)
    specialists: list[str] = Field(default_factory=list)
    needs_orchestrator_llm: bool = False
    budget: OrchestrationBudget | None = None
    rationale: str = ""


class OrchestrationBudget(BaseModel):
    """Cost envelope for one orchestration run (TASK.md §5)."""

    max_tokens: int = 100_000
    max_llm_calls: int = 12
    max_wall_time_s: int = 900
    max_retries: int = 3
    max_cost_usd: float = 5.0


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

_TRIVIAL_THRESHOLD = 1
_SIMPLE_MAX = 8
_MEDIUM_MAX = 15


class TaskAnalyzer:
    """Deterministic + optionally LLM-assisted task analysis.

    Parameters
    ----------
    repo_root:
        Repository the task targets (used for repo-size + toolchain signals).
    historical_failure_rate:
        0..1 — recent verification failure rate for this repo (adaptivity,
        TASK.md §23). High rates push tasks one class up.
    """

    def __init__(
        self,
        repo_root: Path,
        historical_failure_rate: float = 0.0,
    ) -> None:
        self.repo_root = repo_root
        self.historical_failure_rate = max(0.0, min(1.0, historical_failure_rate))

    # -- signals ---------------------------------------------------------------

    def _repo_python_files(self) -> int:
        try:
            return sum(
                1
                for p in self.repo_root.rglob("*.py")
                if ".venv" not in p.parts and "__pycache__" not in p.parts
            )
        except OSError:
            return 0

    def analyze(self, task: str) -> TaskAnalysis:
        text = task.lower()
        score = 0
        signals: dict[str, Any] = {}

        # 1. Trivial signals subtract; simple/base signals add.
        trivial_hits = sum(w for pat, w in _TRIVIAL_SIGNALS if re.search(pat, text))
        simple_hits = sum(w for pat, w in _SIMPLE_SIGNALS if re.search(pat, text))
        score += simple_hits - trivial_hits
        signals["trivial_hits"] = trivial_hits
        signals["simple_hits"] = simple_hits

        # 2. Complexity boosts.
        boosts = sum(w for pat, w in _COMPLEXITY_BOOSTS if re.search(pat, text))
        score += boosts * 2
        signals["boosts"] = boosts

        # 3. Impact dimensions.
        dims = [dim for dim, kws in _DIMENSION_KEYWORDS.items() if any(kw in text for kw in kws)]
        score += len(dims)
        signals["dimensions"] = [d.value for d in dims]

        # 4. Scope heuristics.
        words = len(text.split())
        if words > 120:
            score += 3
        elif words > 60:
            score += 1
        signals["task_words"] = words

        # 5. Repository size: big repos raise regression stakes slightly.
        py_files = self._repo_python_files()
        if py_files > 500:
            score += 2
        elif py_files > 100:
            score += 1
        signals["repo_python_files"] = py_files

        # 6. Historical failure rate (adaptive signal, TASK §4/§23).
        if self.historical_failure_rate >= 0.5:
            score += 2
        elif self.historical_failure_rate > 0.2:
            score += 1

        # 7. Security is never cheap: force at least MEDIUM.
        if ImpactDimension.SECURITY in dims and score < _SIMPLE_MAX + 1:
            score = _SIMPLE_MAX + 1

        # Any functional impact dimension forces at least SIMPLE.
        if dims and score < _TRIVIAL_THRESHOLD + 1:
            score = _TRIVIAL_THRESHOLD + 1

        # Clamp into a class. Strong trivial signals dominate any lingering
        # simple-hit base words ("fix", "docs", "version" appear everywhere).
        if trivial_hits >= 2 or score <= _TRIVIAL_THRESHOLD:
            complexity = Complexity.TRIVIAL
        elif score <= _SIMPLE_MAX:
            complexity = Complexity.SIMPLE
        elif score <= _MEDIUM_MAX:
            complexity = Complexity.MEDIUM
        elif score <= 22:
            complexity = Complexity.COMPLEX
        else:
            complexity = Complexity.CRITICAL

        # Confidence: distance from boundaries + signal agreement.
        boundaries = (_TRIVIAL_THRESHOLD, _SIMPLE_MAX, _MEDIUM_MAX, 22)
        distance = min(abs(score - b) for b in boundaries)
        confidence = max(0.35, min(0.95, 0.55 + 0.05 * distance + 0.04 * len(signals)))

        estimated_files = max(1, min(50, score // 2 + (2 if dims else 0)))
        regression_risk = min(1.0, 0.1 * len(dims) + (0.2 if py_files > 100 else 0.0))

        return TaskAnalysis(
            task=task,
            complexity=complexity,
            confidence=round(confidence, 2),
            score=score,
            estimated_files_affected=estimated_files,
            impact_dimensions=dims,
            blast_radius=(
                "global"
                if complexity in (Complexity.COMPLEX, Complexity.CRITICAL)
                else "cross-module"
                if complexity is Complexity.MEDIUM
                else "local"
            ),
            security_risk=ImpactDimension.SECURITY in dims,
            database_impact=ImpactDimension.DATABASE in dims,
            concurrency_risk=ImpactDimension.CONCURRENCY in dims,
            test_required=True,
            regression_risk=round(regression_risk, 2),
            estimated_tokens={
                Complexity.TRIVIAL: 4_000,
                Complexity.SIMPLE: 15_000,
                Complexity.MEDIUM: 45_000,
                Complexity.COMPLEX: 120_000,
                Complexity.CRITICAL: 250_000,
            }[complexity],
            historical_failure_rate=self.historical_failure_rate,
            signals=signals,
        )

    # -- LLM-assist hook (optional; never required) -------------------------------

    def analyze_with_llm(self, task: str, llm: Any) -> TaskAnalysis:
        """Optionally refine a low-confidence deterministic analysis with an
        LLM judgment. Falls back to the deterministic result on any failure.
        Requires a callable returning a str with a Complexity value in it."""
        base = self.analyze(task)
        if base.confidence >= 0.7:
            return base
        try:
            judgement = llm(
                "Classify this coding task as one of: trivial, simple, medium, "
                f"complex, critical. Task: {task[:500]}\nAnswer with one word."
            )
            word = str(judgement).strip().lower()
            refined = Complexity(word)
            # LLM may only move one class from the deterministic estimate.
            order = list(Complexity)
            idx_b, idx_r = order.index(base.complexity), order.index(refined)
            if abs(idx_b - idx_r) <= 1:
                return base.model_copy(update={"complexity": refined, "confidence": 0.75})
        except Exception:  # noqa: BLE001, S110 — deterministic result is the fallback
            pass
        return base


# ---------------------------------------------------------------------------
# Adaptive router: analysis → minimum orchestration plan (TASK §3, §5)
# ---------------------------------------------------------------------------

_SPECIALISTS_BY_DIMENSION: dict[ImpactDimension, str] = {
    ImpactDimension.FRONTEND: "frontend_specialist",
    ImpactDimension.DATABASE: "database_specialist",
    ImpactDimension.SECURITY: "security_specialist",
    ImpactDimension.ARCHITECTURE: "architect",
    ImpactDimension.API: "backend_specialist",
    ImpactDimension.CONCURRENCY: "backend_specialist",
}


def build_plan(analysis: TaskAnalysis, max_parallel: int = 3) -> OrchestrationPlan:
    """Map a task analysis to the MINIMUM orchestration that can handle it.

    Solo tasks never touch the orchestrator LLM; that is the cost win.
    """
    c = analysis.complexity
    if c in (Complexity.TRIVIAL, Complexity.SIMPLE):
        return OrchestrationPlan(
            track="solo",
            steps=["implement", "verify"],
            budget=OrchestrationBudget(max_tokens=30_000, max_llm_calls=3, max_retries=2),
            needs_orchestrator_llm=False,
            rationale=(
                f"{c.value} task (score={analysis.score}, confidence={analysis.confidence}): "
                "solo coder + verification; multi-agent overhead not justified."
            ),
        )

    if c is Complexity.MEDIUM:
        return OrchestrationPlan(
            track="medium",
            steps=["plan", "implement", "verify", "review"],
            budget=OrchestrationBudget(max_tokens=80_000, max_llm_calls=8),
            needs_orchestrator_llm=True,
            rationale=f"medium task (score={analysis.score}): plan → implement → verify → review.",
        )

    # COMPLEX / CRITICAL: specialist fan-out capped by max_parallel.
    specialists = [
        _SPECIALISTS_BY_DIMENSION[d]
        for d in analysis.impact_dimensions
        if d in _SPECIALISTS_BY_DIMENSION
    ]
    specialists = list(dict.fromkeys(specialists))[:max_parallel]
    steps = ["plan", "architect"] if analysis.complexity is Complexity.CRITICAL else ["plan"]
    steps += (["specialists"] if specialists else []) + [
        "implement",
        "verify",
        "review",
        "repair-if-needed",
        "final-verify",
    ]
    return OrchestrationPlan(
        track="complex",
        steps=steps,
        specialists=specialists,
        budget=OrchestrationBudget(
            max_tokens=250_000 if c is Complexity.CRITICAL else 150_000,
            max_llm_calls=25 if c is Complexity.CRITICAL else 15,
            max_retries=4,
        ),
        needs_orchestrator_llm=True,
        rationale=(
            f"{c.value} task (score={analysis.score}, "
            f"dims={[d.value for d in analysis.impact_dimensions]}): "
            f"fan-out to {specialists or 'coder only'}, bounded repair loop."
        ),
    )
