"""GOA CLI — minimal developer experience (TASK.md §34).

    goa init                  scaffold MCP registration snippets for your agent
    goa mcp serve             start the GOA MCP server (stdio) for external agents
    goa analyze "<task>"      classify a task and print the adaptive plan
    goa verify                run real repository verification, print evidence
    goa status                current orchestration session summary

No LLM API key is required for any command in this CLI — these are the
deterministic core capabilities that External Agent Mode (PRIMARY) consumes.
Native mode is exercised through the LangGraph harness (harness/validator.py).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from harness.task_analyzer import TaskAnalyzer, build_plan
from harness.toolkit import ExecutionToolkit
from harness.verification import VerificationEngine


def _banned() -> list[str]:
    return [
        "rm -rf /",
        "sudo rm",
        "DROP DATABASE",
        "TRUNCATE TABLE",
        "rm -rf ~",
        "mkfs",
        "> /dev/sda",
        "git push --force",
        "git reset --hard",
    ]


# ---------------------------------------------------------------------------
# goa init
# ---------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.workspace).resolve()
    server = Path(__file__).parent.parent / "skills" / "servers" / "goa_mcp.py"
    py, srv, cwd = sys.executable, str(server), str(root)
    print("GOA MCP registration snippets — pick your coding agent:\n")

    print(f"""
# Claude Code — register GOA MCP (run inside the target project):
claude mcp add goa -- {py} {srv} --workspace {cwd}
""")

    print(f"""
# Codex — add to ~/.codex/config.toml:
[mcp_servers.goa]
command = "{py}"
args = ["{srv}", "--workspace", "{cwd}"]
""")

    print(f"""
# OpenCode — add to opencode.json:
{{
  "mcp": {{
    "goa": {{
      "type": "local",
      "command": {{
        "command": "{py}",
        "args": ["{srv}", "--workspace", "{cwd}"]
      }}
    }}
  }}
}}
""")
    print("No separate LLM API key is needed: your coding agent already has one.")
    return 0


# ---------------------------------------------------------------------------
# goa mcp serve
# ---------------------------------------------------------------------------


def cmd_serve(args: argparse.Namespace) -> int:
    os_environ = __import__("os").environ
    if args.workspace:
        os_environ["GOA_WORKSPACE"] = str(Path(args.workspace).resolve())
    from skills.servers import goa_mcp

    goa_mcp.main()
    return 0


# ---------------------------------------------------------------------------
# goa analyze
# ---------------------------------------------------------------------------


def cmd_analyze(args: argparse.Namespace) -> int:
    root = Path(args.workspace).resolve()
    analyzer = TaskAnalyzer(root)
    analysis = analyzer.analyze(args.task)
    plan = build_plan(analysis)
    conf = analysis.confidence
    print(f"complexity : {analysis.complexity.value}  (score={analysis.score}, confidence={conf})")
    print(f"dimensions : {[d.value for d in analysis.impact_dimensions] or 'none'}")
    print(f"blast      : {analysis.blast_radius}")
    print(f"est tokens : {analysis.estimated_tokens:,}")
    print(f"track      : {plan.track}")
    print(f"steps      : {' -> '.join(plan.steps)}")
    if plan.specialists:
        print(f"specialists: {', '.join(plan.specialists)}")
    if plan.budget:
        b = plan.budget
        print(
            f"budget     : {b.max_tokens:,} tokens / {b.max_llm_calls} llm calls"
            f" / {b.max_retries} retries"
        )
    print(f"rationale  : {plan.rationale}")
    return 0


# ---------------------------------------------------------------------------
# goa verify
# ---------------------------------------------------------------------------


def cmd_verify(args: argparse.Namespace) -> int:
    root = Path(args.workspace).resolve()
    toolkit = ExecutionToolkit(root, banned_patterns=_banned())
    engine = VerificationEngine(toolkit)
    report = engine.run()
    print(f"toolchain  : {report.toolchain}")
    for cmd in report.commands:
        status = "PASS" if cmd.ok else "FAIL"
        ms = cmd.duration_ms
        print(f"[{status}] {cmd.check:<9} {cmd.command}  ({ms:.0f}ms, exit={cmd.exit_code})")
        if not cmd.ok:
            excerpt = (cmd.output_excerpt or "")[-2000:]
            if excerpt:
                print("----- output excerpt -----")
                print(excerpt)
    print(f"verdict    : {report.red_green} — {report.summary}")
    target = engine.recovery_target(report)
    if target:
        print(f"recovery   : route to {target}")
    return 0 if report.passed else 1


# ---------------------------------------------------------------------------
# goa status
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    root = Path(args.workspace).resolve()
    toolkit = ExecutionToolkit(root, banned_patterns=_banned())
    status = toolkit.git_status()
    files = (
        [line[3:].strip() for line in status.output.splitlines() if len(line) > 3]
        if status.ok
        else []
    )
    print(f"workspace  : {root}")
    from harness.verification import detect_toolchain

    print(f"toolchain  : {detect_toolchain(root).value}")
    print(f"changed    : {', '.join(files) if files else '(clean)'}")
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="goa", description="GOA orchestration CLI")
    parser.add_argument("--workspace", default=".", help="target repository (default: cwd)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "init", help="print MCP registration snippets for Claude Code / Codex / OpenCode"
    )
    sp = sub.add_parser("mcp", help="MCP server management")
    mcp_sub = sp.add_subparsers(dest="mcp_command", required=True)
    mcp_sub.add_parser("serve", help="start GOA MCP server on stdio")

    an = sub.add_parser("analyze", help="classify a task and print the adaptive plan")
    an.add_argument("task")

    sub.add_parser("verify", help="run real repository verification")
    sub.add_parser("status", help="session/workspace summary")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "init":
        return cmd_init(args)
    if args.command == "mcp":
        if getattr(args, "mcp_command", None) == "serve":
            return cmd_serve(args)
        parser.error("unknown mcp command")
        return 2
    if args.command == "analyze":
        return cmd_analyze(args)
    if args.command == "verify":
        return cmd_verify(args)
    if args.command == "status":
        return cmd_status(args)
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
