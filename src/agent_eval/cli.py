"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .metrics import MIN_LOOP_THRESHOLD, evaluate, summarize_path
from .report import render_json, render_text
from .transcript import DEFAULT_ROOT, iter_session_files, load_session, resolve_root


def _loop_threshold(value: str) -> int:
    threshold = int(value)
    if threshold < MIN_LOOP_THRESHOLD:
        raise argparse.ArgumentTypeError(f"must be at least {MIN_LOOP_THRESHOLD}")
    return threshold


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
        type=_loop_threshold,
        default=3,
        help="how many repeats of one action count as a loop (default: 3, minimum: 2)",
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
    sessions.add_argument(
        "--include-subagents",
        action="store_true",
        help="also list the transcripts of subagents a session spawned",
    )

    return parser


def _analyze(args: argparse.Namespace) -> int:
    try:
        session = load_session(args.path)
    except FileNotFoundError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    evaluation = evaluate(session, loop_threshold=args.loop_threshold)
    print(render_json(evaluation) if args.json else render_text(evaluation))
    return 1 if (args.strict and evaluation.findings) else 0


def _sessions(args: argparse.Namespace) -> int:
    try:
        root = resolve_root(args.root)
    except NotADirectoryError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    rows = [
        summarize_path(path)
        for path in iter_session_files(root, include_subagents=args.include_subagents)
    ]

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


def _tolerate_narrow_encodings() -> None:
    """Transcripts carry arbitrary Unicode; a cp1252 console must not turn that into a crash."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="backslashreplace")


def main(argv: list[str] | None = None) -> int:
    """Run the CLI. Returns the process exit code."""
    _tolerate_narrow_encodings()
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "analyze":
        return _analyze(args)
    if args.command == "sessions":
        return _sessions(args)

    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
