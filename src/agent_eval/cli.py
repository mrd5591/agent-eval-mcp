"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .metrics import evaluate
from .report import render_json, render_text, to_dict
from .transcript import iter_session_files, parse_session

DEFAULT_ROOT = Path.home() / ".claude" / "projects"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-eval",
        description="Measure what a coding agent actually did, from its session transcript.",
    )
    sub = parser.add_subparsers(dest="command")

    analyze = sub.add_parser("analyze", help="analyse one session transcript")
    analyze.add_argument("path", help="path to a .jsonl session transcript")
    analyze.add_argument("--json", action="store_true", help="emit JSON instead of text")
    analyze.add_argument(
        "--loop-threshold",
        type=int,
        default=3,
        help="how many repeats of one action count as a loop (default: 3)",
    )
    analyze.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero when there are findings, for use in CI",
    )

    sessions = sub.add_parser("sessions", help="list session transcripts under a root")
    sessions.add_argument(
        "--root",
        default=str(DEFAULT_ROOT),
        help=f"directory to search (default: {DEFAULT_ROOT})",
    )
    sessions.add_argument("--json", action="store_true", help="emit JSON instead of text")

    return parser


def _analyze(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if not path.is_file():
        print(f"error: transcript not found: {path}", file=sys.stderr)
        return 2

    evaluation = evaluate(parse_session(path), loop_threshold=args.loop_threshold)
    print(render_json(evaluation) if args.json else render_text(evaluation))
    return 1 if (args.strict and evaluation.findings) else 0


def _sessions(args: argparse.Namespace) -> int:
    rows = []
    for path in iter_session_files(Path(args.root)):
        session = parse_session(path)
        rows.append(
            {
                "path": str(path),
                "session_id": session.session_id,
                "cwd": session.cwd,
                "tool_calls": len(session.tool_calls),
                "human_turns": session.human_turns,
                "total_usd": session.cost.total_usd,
            }
        )

    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=True))
        return 0

    if not rows:
        print("No sessions found.")
        return 0

    for row in rows:
        print(
            f"{Path(row['path']).name}  "
            f"calls={row['tool_calls']:<5} turns={row['human_turns']:<4} "
            f"${row['total_usd']:.2f}  {row['cwd']}"
        )
    print(f"\n{len(rows)} session(s).")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the CLI. Returns the process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.command == "analyze":
        return _analyze(args)
    if args.command == "sessions":
        return _sessions(args)

    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
