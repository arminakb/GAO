"""Execution graph and runtime trace visualizer.

Generates Mermaid static architecture flowcharts and dynamic execution trace
sequence diagrams from LangGraph structures and audit trails.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from harness.state_schema import AuditEntry

# ---------------------------------------------------------------------------
# Node style definitions for Mermaid
# ---------------------------------------------------------------------------

_NODE_STYLES: dict[str, str] = {
    # Node name -> CSS fill/stroke styling
    "orchestrator": "fill:#3B82F6,stroke:#1D4ED8,color:#FFFFFF,stroke-width:2px",
    "planner": "fill:#8B5CF6,stroke:#6D28D9,color:#FFFFFF",
    "architect": "fill:#8B5CF6,stroke:#6D28D9,color:#FFFFFF",
    "coder": "fill:#10B981,stroke:#047857,color:#FFFFFF",
    "tdd_guide": "fill:#10B981,stroke:#047857,color:#FFFFFF",
    "reviewer": "fill:#F59E0B,stroke:#B45309,color:#FFFFFF",
    "database_reviewer": "fill:#F59E0B,stroke:#B45309,color:#FFFFFF",
    "security_reviewer": "fill:#F59E0B,stroke:#B45309,color:#FFFFFF",
    "refactor_cleaner": "fill:#6B7280,stroke:#374151,color:#FFFFFF",
    "doc_updater": "fill:#6B7280,stroke:#374151,color:#FFFFFF",
    "build_error_resolver": "fill:#EC4899,stroke:#BE185D,color:#FFFFFF",
    "e2e_runner": "fill:#06B6D4,stroke:#0E7490,color:#FFFFFF",
    "loop_operator": "fill:#6366F1,stroke:#4338CA,color:#FFFFFF",
    "harness_optimizer": "fill:#14B8A6,stroke:#0F766E,color:#FFFFFF",
    "interrupt_handler": "fill:#EF4444,stroke:#B91C1C,color:#FFFFFF,stroke-width:2px",
}


# ---------------------------------------------------------------------------
# GraphVisualizer
# ---------------------------------------------------------------------------


class GraphVisualizer:
    """Generates visual representations of the orchestrator state machine."""

    def generate_static_diagram(self, graph: Any | None = None) -> str:
        """Generate a Mermaid flowchart representing the graph architecture."""
        lines = [
            "flowchart TD",
            "    %% Start and Termination",
            "    START([Start]) --> orchestrator",
            "    interrupt_handler --> END([End])",
            "",
            "    %% Orchestrator Routing",
            "    orchestrator -->|Planning| planner",
            "    orchestrator -->|Architecture| architect",
            "    orchestrator -->|Implementation| coder",
            "    orchestrator -->|TDD Tests| tdd_guide",
            "    orchestrator -->|Review| reviewer",
            "    orchestrator -->|DB Review| database_reviewer",
            "    orchestrator -->|Security Audit| security_reviewer",
            "    orchestrator -->|Cleanup| refactor_cleaner",
            "    orchestrator -->|Documentation| doc_updater",
            "    orchestrator -->|Build Diagnostics| build_error_resolver",
            "    orchestrator -->|E2E Verification| e2e_runner",
            "    orchestrator -->|Optimization| harness_optimizer",
            "    orchestrator -->|Task Completed| END",
            "    orchestrator -.->|Budget / Error Limit| interrupt_handler",
            "",
            "    %% Direct Handoffs",
            "    coder -->|Direct Handoff| reviewer",
            "    tdd_guide -->|Direct Handoff| coder",
            "    build_error_resolver -->|Direct Handoff| coder",
            "",
            "    %% Standard Returns to Orchestrator",
            "    planner --> orchestrator",
            "    architect --> orchestrator",
            "    reviewer --> orchestrator",
            "    database_reviewer --> orchestrator",
            "    security_reviewer --> orchestrator",
            "    refactor_cleaner --> orchestrator",
            "    doc_updater --> orchestrator",
            "    e2e_runner --> orchestrator",
            "    loop_operator --> orchestrator",
            "    harness_optimizer --> orchestrator",
            "",
            "    %% Node Styling",
        ]

        for node, style in _NODE_STYLES.items():
            lines.append(f"    style {node} {style}")

        return "\n".join(lines)

    def generate_execution_trace(self, audit_trail: list[AuditEntry]) -> str:
        """Generate a Mermaid sequence diagram from the audit trail history."""
        if not audit_trail:
            return (
                "sequenceDiagram\n"
                "    autonumber\n"
                "    Note over Orchestrator: No state transitions recorded yet.\n"
            )

        lines = [
            "sequenceDiagram",
            "    autonumber",
        ]

        participants: set[str] = set()
        for entry in audit_trail:
            from_p = (entry.from_node or "System").replace("_", "-").title()
            to_p = entry.to_node.replace("_", "-").title()
            participants.add(from_p)
            participants.add(to_p)

        for entry in audit_trail:
            from_p = (entry.from_node or "System").replace("_", "-").title()
            to_p = entry.to_node.replace("_", "-").title()
            rationale = (entry.rationale or "").replace("\n", " ")[:60]
            ts = entry.timestamp.strftime("%H:%M:%S")

            if entry.to_node == "interrupt_handler":
                lines.append(f"    {from_p}-->>{to_p}: [INTERRUPT {ts}] {rationale}")
            else:
                lines.append(f"    {from_p}->>{to_p}: [{ts}] {rationale}")

        return "\n".join(lines)

    def export_to_file(self, mermaid_str: str, output_path: Path) -> None:
        """Export a Mermaid diagram string to a Markdown file."""
        content = f"```mermaid\n{mermaid_str}\n```\n"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
