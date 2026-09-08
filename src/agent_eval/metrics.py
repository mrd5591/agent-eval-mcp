"""The analyses. Pure functions over a parsed Session."""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .transcript import Session, ToolCall, parse_session, read_cost

# Repetition of a read-only tool is navigation, not thrash.
_STATEFUL_TOOLS = frozenset({"Bash", "PowerShell", "Edit", "Write", "NotebookEdit"})

MIN_LOOP_THRESHOLD = 2

_UNPROMPTED_MODES = frozenset({"bypassPermissions", "auto", "acceptEdits", "dontAsk"})

# Test-run detection works on the parsed command, not the raw string: each shell segment is
# tokenised, wrapper prefixes are peeled off, and the first real word is looked up here. A runner
# that needs a subcommand lists the subcommands that run tests.
_RUNNERS: dict[str, frozenset[str] | None] = {
    "pytest": None,
    "py.test": None,
    "tox": None,
    "nox": None,
    "jest": None,
    "vitest": None,
    "mocha": None,
    "rspec": None,
    "phpunit": None,
    "ctest": None,
    "npm": frozenset({"test", "t", "tst"}),
    "yarn": frozenset({"test"}),
    "pnpm": frozenset({"test"}),
    "bun": frozenset({"test"}),
    "deno": frozenset({"test"}),
    "mvn": frozenset({"test", "verify", "install", "package"}),
    "gradle": frozenset({"test", "check", "build"}),
    "gradlew": frozenset({"test", "check", "build"}),
    "go": frozenset({"test"}),
    "cargo": frozenset({"test", "nextest"}),
    "dotnet": frozenset({"test"}),
    "make": frozenset({"test", "check"}),
    "mix": frozenset({"test"}),
    "rake": frozenset({"test", "spec"}),
    "swift": frozenset({"test"}),
}

_SEGMENT_BREAKS = frozenset({";", "&&", "||", "|", "&"})
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_ONE_WORD_WRAPPERS = frozenset({"sudo", "time", "nice", "env", "exec", "command", "npx", "bunx"})
_TWO_WORD_WRAPPERS = frozenset(
    {
        ("uv", "run"),
        ("uvx", ""),
        ("poetry", "run"),
        ("pipenv", "run"),
        ("pdm", "run"),
        ("hatch", "run"),
        ("pnpm", "exec"),
        ("pnpm", "dlx"),
        ("yarn", "run"),
        ("npm", "exec"),
        ("python", "-m"),
        ("python3", "-m"),
        ("py", "-m"),
    }
)


@dataclass
class ToolStat:
    """Per-tool counts."""

    calls: int = 0
    failures: int = 0

    @property
    def failure_rate(self) -> float:
        return self.failures / self.calls if self.calls else 0.0


@dataclass
class ToolUsage:
    """How the agent spent its actions."""

    total_calls: int = 0
    total_failures: int = 0
    by_tool: dict[str, ToolStat] = field(default_factory=dict)

    @property
    def overall_failure_rate(self) -> float:
        return self.total_failures / self.total_calls if self.total_calls else 0.0


@dataclass
class Loop:
    """The same stateful action, repeated."""

    tool: str
    signature: str
    occurrences: int
    all_failed: bool


@dataclass
class SuiteEvents:
    """What the test suite did during the session."""

    runs: int = 0
    failures: int = 0
    ended_green: bool | None = None


@dataclass
class GateEvents:
    """What the guardrails did during the session."""

    permission_modes: list[str] = field(default_factory=list)
    ran_without_prompts: bool = False
    hook_blocks: int = 0


@dataclass
class CostSummary:
    """What it cost, and per unit of change."""

    total_usd: float = 0.0
    lines_changed: int = 0
    duration_ms: int = 0
    models: list[str] = field(default_factory=list)

    @property
    def usd_per_100_lines(self) -> float | None:
        if not self.lines_changed:
            return None
        return self.total_usd / self.lines_changed * 100


@dataclass
class Evaluation:
    """Every analysis, plus the findings list."""

    session_id: str
    path: str
    cwd: str
    human_turns: int
    tools: ToolUsage
    loops: list[Loop]
    tests: SuiteEvents
    gates: GateEvents
    cost: CostSummary
    findings: list[str]


def tool_usage(session: Session) -> ToolUsage:
    """Count calls and failures, overall and per tool."""
    usage = ToolUsage()
    for call in session.tool_calls:
        usage.total_calls += 1
        stat = usage.by_tool.setdefault(call.name, ToolStat())
        stat.calls += 1
        if call.failed:
            usage.total_failures += 1
            stat.failures += 1
    return usage


def detect_loops(session: Session, threshold: int = 3) -> list[Loop]:
    """Find stateful actions repeated at least `threshold` times, matched on signature.

    Raises:
        ValueError: when the threshold is below 2, since one occurrence is not a repeat.
    """
    if threshold < MIN_LOOP_THRESHOLD:
        raise ValueError(f"threshold must be at least {MIN_LOOP_THRESHOLD}, got {threshold}")
    grouped: dict[str, list[ToolCall]] = {}
    for call in session.tool_calls:
        if call.name not in _STATEFUL_TOOLS:
            continue
        grouped.setdefault(call.signature, []).append(call)

    loops = [
        Loop(
            tool=calls[0].name,
            signature=signature,
            occurrences=len(calls),
            all_failed=all(call.resolved and call.failed for call in calls),
        )
        for signature, calls in grouped.items()
        if len(calls) >= threshold
    ]
    return sorted(loops, key=lambda loop: (-loop.occurrences, loop.signature))


def _segments(command: str) -> Iterable[list[str]]:
    """Split a shell command into its simple commands, one token list each."""
    for line in command.splitlines():
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        try:
            tokens = list(lexer)
        except ValueError:
            tokens = line.split()
        segment: list[str] = []
        for token in tokens:
            if token in _SEGMENT_BREAKS:
                if segment:
                    yield segment
                segment = []
            else:
                segment.append(token)
        if segment:
            yield segment


def _strip_wrappers(tokens: list[str]) -> list[str]:
    """Peel off env assignments and launcher prefixes until the real program is first."""
    while tokens:
        head = tokens[0]
        if _ENV_ASSIGNMENT.match(head):
            tokens = tokens[1:]
        elif head == "timeout" and len(tokens) > 2:
            tokens = tokens[2:]
        elif head in _ONE_WORD_WRAPPERS:
            tokens = tokens[1:]
        elif len(tokens) > 1 and (head, tokens[1]) in _TWO_WORD_WRAPPERS:
            tokens = tokens[2:]
        else:
            break
    return tokens


def _program(token: str) -> str:
    name = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
    for suffix in (".exe", ".cmd", ".bat", ".sh"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name


def is_test_run(command: str) -> bool:
    """Whether a shell command runs a test suite."""
    for segment in _segments(command):
        tokens = _strip_wrappers(segment)
        if not tokens:
            continue
        subcommands = _RUNNERS.get(_program(tokens[0]), False)
        if subcommands is None:
            return True
        if subcommands and any(arg in subcommands for arg in tokens[1:]):
            return True
    return False


def suite_events(session: Session) -> SuiteEvents:
    """Count test runs, how many failed, and whether the session ended green.

    A run whose result never arrived (the session was cut off) counts as a run but decides
    nothing: if it was the last one, the ending is unknown rather than green.
    """
    events = SuiteEvents()
    last: ToolCall | None = None
    for call in session.tool_calls:
        if not call.command or not is_test_run(call.command):
            continue
        events.runs += 1
        if call.failed:
            events.failures += 1
        last = call
    if last is not None and last.resolved:
        events.ended_green = not last.failed
    return events


def gate_events(session: Session) -> GateEvents:
    """Report the permission posture and how often a hook refused something."""
    modes = list(dict.fromkeys(session.permission_modes))
    return GateEvents(
        permission_modes=modes,
        ran_without_prompts=any(mode in _UNPROMPTED_MODES for mode in modes),
        hook_blocks=session.hook_blocks,
    )


def cost_summary(session: Session) -> CostSummary:
    """Total spend and spend per unit of change."""
    cost = session.cost
    return CostSummary(
        total_usd=cost.total_usd,
        lines_changed=cost.lines_added + cost.lines_removed,
        duration_ms=cost.duration_ms,
        models=list(cost.models),
    )


def summarize(session: Session) -> dict[str, Any]:
    """The one-line view of a session used by every listing."""
    return {
        "path": str(session.path),
        "session_id": session.session_id,
        "cwd": session.cwd,
        "tool_calls": len(session.tool_calls),
        "human_turns": session.human_turns,
        "total_usd": session.cost.total_usd,
    }


def summarize_path(path: Path) -> dict[str, Any]:
    """Parse and summarize one transcript."""
    return summarize(parse_session(path))


def aggregate_cost(paths: Iterable[Path]) -> dict[str, Any]:
    """Spend across many sessions, reading only each file's cost rollup."""
    total = CostSummary()
    count = 0
    for path in paths:
        cost = read_cost(path)
        count += 1
        total.total_usd += cost.total_usd
        total.lines_changed += cost.lines_added + cost.lines_removed
    return {
        "sessions": count,
        "total_usd": total.total_usd,
        "lines_changed": total.lines_changed,
        "usd_per_100_lines": total.usd_per_100_lines,
    }


def _findings(
    tools: ToolUsage, loops: list[Loop], tests: SuiteEvents, gates: GateEvents
) -> list[str]:
    """The short list a reviewer should look at."""
    findings: list[str] = []

    for loop in loops:
        state = "every attempt failed" if loop.all_failed else "results varied"
        findings.append(
            f"Possible loop: {loop.tool} ran the same action {loop.occurrences} times "
            f"({state}) - {loop.signature}"
        )

    if tests.ended_green is False:
        findings.append("Session ended with a failing test run")

    if tools.total_calls >= 10 and tools.overall_failure_rate > 0.25:
        findings.append(
            f"High tool failure rate: {tools.overall_failure_rate:.0%} of "
            f"{tools.total_calls} calls returned an error"
        )

    if gates.hook_blocks:
        findings.append(f"Hooks blocked {gates.hook_blocks} tool call(s)")

    return findings


def evaluate(session: Session, loop_threshold: int = 3) -> Evaluation:
    """Run every analysis over one session."""
    tools = tool_usage(session)
    loops = detect_loops(session, threshold=loop_threshold)
    tests = suite_events(session)
    gates = gate_events(session)

    return Evaluation(
        session_id=session.session_id,
        path=str(session.path),
        cwd=session.cwd,
        human_turns=session.human_turns,
        tools=tools,
        loops=loops,
        tests=tests,
        gates=gates,
        cost=cost_summary(session),
        findings=_findings(tools, loops, tests, gates),
    )
