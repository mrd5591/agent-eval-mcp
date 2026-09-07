"""MCP server exposing the analyses as tools.

Why an MCP server and not only a CLI: the useful moment for this data is *inside* a
session, when the question is "have I been going in circles for the last twenty
minutes." A CLI answers that afterwards, to a human. An MCP tool answers it during,
to the agent, which can then stop.

Tool schemas are derived from the signatures and docstrings below, so the
description a model reads and the behaviour it gets cannot drift apart.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import __version__
from .metrics import detect_loops, evaluate
from .report import to_dict
from .transcript import iter_session_files, parse_session

DEFAULT_ROOT = Path.home() / ".claude" / "projects"

server = MCPServer(
    name="agent-eval",
    version=__version__,
    instructions=(
        "Analyse coding-agent session transcripts. Use analyze_session to review a "
        "session end to end, find_loops when you suspect repeated failing work, and "
        "cost_report to aggregate spend. Prompt text and file contents are never "
        "returned by these tools, only counts and classifications."
    ),
)


def _load(path: str):
    """Resolve and parse a transcript.

    A missing file is an expected outcome when a model guesses a path, so it is
    raised as a ToolError. That reaches the model as a readable error result it can
    correct from, rather than as a server-side crash.
    """
    resolved = Path(path)
    if not resolved.is_file():
        raise ToolError(f"Transcript not found: {resolved}")
    return parse_session(resolved)


def _summarize(path: Path) -> dict[str, Any]:
    session = parse_session(path)
    return {
        "path": str(path),
        "session_id": session.session_id,
        "cwd": session.cwd,
        "tool_calls": len(session.tool_calls),
        "human_turns": session.human_turns,
        "total_usd": session.cost.total_usd,
    }


@server.tool()
def list_sessions(root: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """List session transcripts under a root directory.

    Returns one summary per session: id, working directory, tool-call count, human
    turn count and cost. Use it to find the session worth analysing.

    Args:
        root: Directory to search. Defaults to the standard transcript location.
        limit: Maximum number of sessions to return.
    """
    search_root = Path(root) if root else DEFAULT_ROOT
    rows: list[dict[str, Any]] = []
    for path in iter_session_files(search_root):
        if len(rows) >= limit:
            break
        rows.append(_summarize(path))
    return rows


@server.tool()
def analyze_session(path: str, loop_threshold: int = 3) -> dict[str, Any]:
    """Analyse one session transcript end to end.

    Returns tool usage and failure rates, detected loops, test-run outcomes,
    permission posture, cost, and a short list of findings worth a human's
    attention. Prompt text and file contents are never included.

    Args:
        path: Path to a .jsonl session transcript.
        loop_threshold: How many repeats of one action count as a loop.
    """
    return to_dict(evaluate(_load(path), loop_threshold=loop_threshold))


@server.tool()
def find_loops(path: str, threshold: int = 3) -> list[dict[str, Any]]:
    """Return only the repeated stateful actions in a session.

    A loop is the same command or edit issued at least `threshold` times. Reads and
    other read-only tools are excluded, because re-reading a file is navigation
    rather than thrash.

    Args:
        path: Path to a .jsonl session transcript.
        threshold: Repeats of one action that count as a loop.
    """
    return [asdict(loop) for loop in detect_loops(_load(path), threshold=threshold)]


@server.tool()
def cost_report(root: str | None = None) -> dict[str, Any]:
    """Aggregate cost across every session under a root.

    Returns total spend, total lines changed, and spend per 100 lines changed.

    Args:
        root: Directory to search. Defaults to the standard transcript location.
    """
    search_root = Path(root) if root else DEFAULT_ROOT
    total_usd = 0.0
    lines_changed = 0
    count = 0
    for path in iter_session_files(search_root):
        session = parse_session(path)
        count += 1
        total_usd += session.cost.total_usd
        lines_changed += session.cost.lines_added + session.cost.lines_removed
    return {
        "root": str(search_root),
        "sessions": count,
        "total_usd": round(total_usd, 4),
        "lines_changed": lines_changed,
        "usd_per_100_lines": (
            round(total_usd / lines_changed * 100, 4) if lines_changed else None
        ),
    }


def main() -> None:  # pragma: no cover - transport wiring
    """Console entry point. Serves over stdio."""
    server.run("stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
