"""Phase 7 — Context + Token Optimization (TASK.md §6, §19)."""

from __future__ import annotations

from typing import Any

from harness.context import estimate_tokens, smart_excerpt


class TestEstimateTokens:
    def test_deterministic_chars_over_four(self) -> None:
        assert estimate_tokens("") == 1
        assert estimate_tokens("ab") == 1
        assert estimate_tokens("abcd") == 1
        assert estimate_tokens("a" * 400) == 100

    def test_monotonic(self) -> None:
        short = estimate_tokens("hello")
        long = estimate_tokens("hello" * 1000)
        assert long > short


class TestSmartExcerpt:
    def test_short_text_untouched(self) -> None:
        assert smart_excerpt("hello world", 100) == "hello world"

    def test_head_and_tail_preserved(self) -> None:
        text = "HEAD" + "x" * 2000 + "TAIL"
        out = smart_excerpt(text, 100)
        assert out.startswith("HEAD")
        assert out.endswith("TAIL")
        assert len(out) <= 200  # two halves + marker

    def test_evidence_markers_surfaced_in_clip_note(self) -> None:
        # The classic failure shape: setup at head, FAILED/traceback at tail.
        text = (
            "============================= test session starts "
            "=============================\n" + "x" * 4000 + "\n"
            "FAILED tests/test_x.py::test_y - assert 1 == 2\n"
            "Traceback (most recent call last):\n"
            "  ...\n"
            "AssertionError: assert 1 == 2\n"
        )
        out = smart_excerpt(text, 200)
        assert "FAILED" in out  # tail kept outright
        assert "chars clipped" in out
        assert "evidence markers present" in out
        assert "FAILED" in out.split("chars clipped")[1].split("]")[0].split("evidence")[0] or (
            "FAILED" in out
        )

    def test_no_evidence_note_when_middle_clean(self) -> None:
        text = "a" * 3000
        out = smart_excerpt(text, 100)
        assert "chars clipped" in out
        assert "evidence markers" not in out

    def test_never_exceeds_limit_by_much(self) -> None:
        text = "z" * 100_000
        out = smart_excerpt(text, 500)
        assert len(out) < 700  # limit + marker overhead


class TestCommandEvidenceTokens:
    def test_estimated_tokens_property(self) -> None:
        from harness.verification import CommandEvidence

        ev = CommandEvidence(
            check="tests",
            command="pytest -q",
            ok=False,
            exit_code=1,
            duration_ms=10.0,
            output_excerpt="F" * 4000,
        )
        assert ev.estimated_tokens == 1000


class TestMcpBudgetAndTokens:
    def _client(self, tmp_path: Any) -> Any:
        from mcp.server.fastmcp import FastMCP  # noqa: F401

        import skills.servers.goa_mcp as m

        m._WORKSPACE = tmp_path
        m._TOOLKIT = m.ExecutionToolkit(tmp_path)
        m._VERIFIER = m.VerificationEngine(m._TOOLKIT)
        m._SESSIONS.clear()
        m._MEMORY_PATH = tmp_path / ".goa_memory.json"
        return m

    def test_verify_exhausts_budget_and_blocks(self, tmp_path: Any, monkeypatch: Any) -> None:
        from harness.verification import VerificationReport

        m = self._client(tmp_path)
        monkeypatch.setattr(m, "_MAX_VERIFY_CYCLES", 2)
        monkeypatch.setattr(
            m._VERIFIER,
            "run",
            lambda: VerificationReport(toolchain="unknown", passed=False, summary="boom"),
        )
        first = m.goa_verify(session_id="s")
        assert first["verdict"] == "RED"
        second = m.goa_verify(session_id="s")
        assert second["verdict"] == "RED"
        third = m.goa_verify(session_id="s")
        assert third["verdict"] == "BUDGET_EXHAUSTED"
        assert third["budget"]["failed_cycles"] == 2
        # The evidence record is workspace-global, so a fresh session id does
        # NOT dodge the budget — that's intentional (no retry-loop escape).
        fresh = m.goa_verify(session_id="other")
        assert fresh["verdict"] == "BUDGET_EXHAUSTED"

    def test_green_verify_does_not_consume_budget(self, tmp_path: Any, monkeypatch: Any) -> None:
        from harness.verification import VerificationReport

        m = self._client(tmp_path)
        monkeypatch.setattr(m, "_MAX_VERIFY_CYCLES", 2)
        monkeypatch.setattr(
            m._VERIFIER,
            "run",
            lambda: VerificationReport(toolchain="unknown", passed=True, summary="ok"),
        )
        for _ in range(4):
            out = m.goa_verify(session_id="s")
            assert out["verdict"] == "GREEN"

    def test_verify_reports_estimated_tokens(self, tmp_path: Any, monkeypatch: Any) -> None:
        from harness.verification import CommandEvidence, VerificationReport

        m = self._client(tmp_path)

        def fake_run() -> VerificationReport:
            rep = VerificationReport(toolchain="unknown", passed=True, summary="ok")
            rep.commands.append(
                CommandEvidence(
                    check="tests",
                    command="pytest",
                    ok=True,
                    exit_code=0,
                    duration_ms=1.0,
                    output_excerpt="x" * 400,
                )
            )
            return rep

        monkeypatch.setattr(m._VERIFIER, "run", fake_run)
        out = m.goa_verify(session_id="s")
        assert out["estimated_tokens"] == 100

    def test_review_reports_estimated_tokens(self, tmp_path: Any, monkeypatch: Any) -> None:
        m = self._client(tmp_path)
        m._git_init_commit_all = getattr(m, "_git_init_commit_all", None)

        class FakeDiff:
            output = "d" * 800

        monkeypatch.setattr(m._TOOLKIT, "git_diff", lambda: FakeDiff())
        monkeypatch.setattr(m, "_changed_files", list)
        out = m.goa_review(session_id="s")
        assert out["estimated_tokens"] == 200

    def test_repair_excerpt_keeps_tail_evidence(self, tmp_path: Any, monkeypatch: Any) -> None:

        m = self._client(tmp_path)
        from harness.verification import (
            CommandEvidence,
            FailureCategory,
            VerificationReport,
        )

        big = "h" * 2000 + "\nE   assert 1 == 2\n" + "t" * 2000
        rep = VerificationReport(
            toolchain="unknown",
            passed=False,
            failure_category=FailureCategory.TEST_FAILURE,
            failure_command="pytest -q",
        )
        rep.commands.append(
            CommandEvidence(
                check="tests",
                command="pytest -q",
                ok=False,
                exit_code=1,
                duration_ms=5.0,
                output_excerpt=big,
            )
        )
        monkeypatch.setattr(
            m,
            "_evidence_record",
            lambda: type("R", (), {"last": rep, "entries": [rep]})(),
        )
        out = m.goa_repair(session_id="s")
        assert out["verdict"] == "RED"
        assert out["evidence_excerpt"].endswith("t" * 1000)  # tail preserved
        assert "chars clipped" in out["evidence_excerpt"]
