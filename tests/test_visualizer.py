"""Tests for GraphVisualizer (Mermaid diagrams and execution traces)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from harness.state_schema import AuditEntry
from harness.visualizer import GraphVisualizer


def test_generate_static_diagram() -> None:
    """Verify generate_static_diagram produces a valid Mermaid flowchart."""
    viz = GraphVisualizer()
    diagram = viz.generate_static_diagram()

    assert diagram.startswith("flowchart TD")
    assert "orchestrator" in diagram
    assert "coder" in diagram
    assert "reviewer" in diagram
    assert "interrupt_handler" in diagram
    assert "style orchestrator fill:#3B82F6" in diagram
    assert "style coder fill:#10B981" in diagram
    assert "style interrupt_handler fill:#EF4444" in diagram


def test_generate_execution_trace_empty() -> None:
    """Verify generate_execution_trace handles empty audit trail."""
    viz = GraphVisualizer()
    trace = viz.generate_execution_trace([])
    assert "sequenceDiagram" in trace
    assert "No state transitions" in trace


def test_generate_execution_trace_with_entries(sample_audit_trail: list[AuditEntry]) -> None:
    """Verify generate_execution_trace creates valid sequence diagram from audit trail."""
    viz = GraphVisualizer()
    trace = viz.generate_execution_trace(sample_audit_trail)

    assert "sequenceDiagram" in trace
    assert "autonumber" in trace
    assert "Orchestrator->>Planner:" in trace
    assert "Planner->>Architect:" in trace
    assert "Initial plan decomposition" in trace


def test_generate_execution_trace_interrupt() -> None:
    """Verify generate_execution_trace highlights interrupts."""
    viz = GraphVisualizer()
    audit_trail = [
        AuditEntry(
            timestamp=datetime.now(UTC),
            from_node="coder",
            to_node="interrupt_handler",
            rationale="Retry limit reached",
            state_snapshot_hash="hash-abc",
        )
    ]
    trace = viz.generate_execution_trace(audit_trail)
    assert "-->>Interrupt-Handler:" in trace
    assert "INTERRUPT" in trace


def test_export_to_file(tmp_path: Path) -> None:
    """Verify export_to_file saves fenced Mermaid markdown file."""
    viz = GraphVisualizer()
    diagram = viz.generate_static_diagram()
    output_file = tmp_path / "diagrams" / "architecture.md"

    viz.export_to_file(diagram, output_file)
    assert output_file.exists()

    content = output_file.read_text(encoding="utf-8")
    assert content.startswith("```mermaid\n")
    assert content.endswith("\n```\n")
    assert "flowchart TD" in content
