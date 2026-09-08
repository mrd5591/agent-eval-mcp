"""MCP server exposing the analyses as tools.

Tool schemas and descriptions are derived from the signatures and docstrings below.
"""

from __future__ import annotations

from dataclasses import asdict
from itertools import islice
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import __version__
from .metrics import aggregate_cost, detect_loops, evaluate, summarize_path
from .report import to_dict
from .transcript import Session, iter_session_files, load_session, resolve_root

server = MCPServer(
    name="agent-eval",
    version=__version__,
    instructions=(
        "Analyse coding-agent session transcripts. Use analyze_session to review a "
        "session end to end, find_loops when you suspect repeated failing work, and "
        "cost_report to aggregate spend. Prompt text and file contents are never "
        "returned by these tools; a repeated shell command is echoed in loop findings, "
        "everything else is counts and classifications."
    ),
)


def _load(path: str) -> Session:
    """Resolve and parse a transcript. A missing file is a ToolError the model can correct from."""
    try:
        return load_session(path)
    except FileNotFoundError as error:
        raise ToolError(str(error)) from error


def _root(root: str | None):
    """Resolve a root directory. A missing one is a ToolError, not an empty result."""
    try:
        return resolve_root(root)
    except NotADirectoryError as error:
        raise ToolError(str(error)) from error


@server.tool()
def list_sessions(
    root: str | None = None, limit: int = 50, include_subagents: bool = False
) -> list[dict[str, Any]]:
    """List session transcripts under a root directory, newest first.

    Returns one summary per session: id, working directory, tool-call count, human
    turn count and cost. Use it to find the session worth analysing.

    Args:
        root: Directory to search. Defaults to the standard transcript location.
        limit: Maximum number of sessions to return.
        include_subagents: Also list the transcripts of subagents a session spawned.
    """
    paths = iter_session_files(_root(root), include_subagents=include_subagents)
    return [summarize_path(path) for path in islice(paths, max(limit, 0))]


@server.tool()
def analyze_session(path: str, loop_threshold: int = 3) -> dict[str, Any]:
    """Analyse one session transcript end to end.

    Returns tool usage and failure rates, detected loops, test-run outcomes,
    permission posture, cost, and a short list of findings worth a human's
    attention. Prompt text and file contents are never included.

    Args:
        path: Path to a .jsonl session transcript.
        loop_threshold: How many repeats of one action count as a loop (at least 2).
    """
    try:
        return to_dict(evaluate(_load(path), loop_threshold=loop_threshold))
    except ValueError as error:
        raise ToolError(str(error)) from error


@server.tool()
def find_loops(path: str, threshold: int = 3) -> list[dict[str, Any]]:
    """Return only the repeated stateful actions in a session.

    A loop is the same command or edit issued at least `threshold` times. Reads and
    other read-only tools are excluded, because re-reading a file is navigation
    rather than thrash. An edit is identified by its path and a fingerprint, never
    by its content.

    Args:
        path: Path to a .jsonl session transcript.
        threshold: Repeats of one action that count as a loop (at least 2).
    """
    try:
        return [asdict(loop) for loop in detect_loops(_load(path), threshold=threshold)]
    except ValueError as error:
        raise ToolError(str(error)) from error


@server.tool()
def cost_report(root: str | None = None, include_subagents: bool = False) -> dict[str, Any]:
    """Aggregate cost across every session under a root.

    Returns total spend, total lines changed, and spend per 100 lines changed.

    Args:
        root: Directory to search. Defaults to the standard transcript location.
        include_subagents: Also count subagent transcripts (they carry no cost rollup of
            their own, so this changes the session count, not the spend).
    """
    search_root = _root(root)
    report = aggregate_cost(iter_session_files(search_root, include_subagents=include_subagents))
    return {"root": str(search_root), **report}


def main() -> None:  # pragma: no cover - transport wiring
    """Console entry point. Serves over stdio."""
    server.run("stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
