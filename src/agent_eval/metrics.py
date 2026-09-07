"""The analyses.

Each function answers one question you would actually ask about a session, takes
a parsed Session and returns a value. No I/O, no globals, so every one of them is
testable against a handful of synthetic records.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .transcript import Session, ToolCall

# Tools whose repetition is evidence of thrash. Reading the same file five times
# is navigation; running the same failing build five times is a loop.
_STATEFUL_TOOLS = frozenset({"Bash", "PowerShell", "Edit", "Write", "NotebookEdit"})

# Anchored at the start of a command or after a shell separator, so "ls tests/"
# does not count as a test run but "cd x && pytest" does.
_TEST_RUNNER = re.compile(
    r"(?:^|[;&|]\s*)(?:"
    r"pytest|py\.test|tox|nox"
    r"|npm\s+(?:run\s+)?test|yarn\s+test|pnpm\s+test|jest|vitest"
    r"|mvn\s+(?:.*\s)?(?:test|verify)|gradle(?:w)?\s+(?:.*\s)?test"
    r"|go\s+test|cargo\s+test|dotnet\s+test|rspec|phpunit|ctest"
    r")\b",
    re.IGNORECASE,
)

# Permission postures where the agent was not being asked before acting.
_UNPROMPTED_MODES = frozenset({"bypassPermissions", "auto", "acceptEdits", "dontAsk"})


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
    """Everything, plus the short list of things worth a human's attention."""

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
    """
    Find stateful actions repeated at least `threshold` times.

    Repetition is matched on the call signature rather than on adjacency: an agent
    that alternates between the same failing build and the same failing edit is
    looping just as surely as one that repeats a single command, and the
    interleaving is what makes it hard to notice by eye.
    """
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
            all_failed=all(call.failed for call in calls),
        )
        for signature, calls in grouped.items()
        if len(calls) >= threshold
    ]
    return sorted(loops, key=lambda loop: (-loop.occurrences, loop.signature))


def suite_events(session: Session) -> SuiteEvents:
    """Count test runs, how many failed, and whether the session ended green."""
    events = SuiteEvents()
    last_failed: bool | None = None
    for call in session.tool_calls:
        if not call.command or not _TEST_RUNNER.search(call.command):
            continue
        events.runs += 1
        if call.failed:
            events.failures += 1
        last_failed = call.failed
    if last_failed is not None:
        events.ended_green = not last_failed
    return events


def gate_events(session: Session) -> GateEvents:
    """Report the permission posture and how often a hook refused something."""
    modes = list(session.permission_modes)
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


def _findings(
    tools: ToolUsage, loops: list[Loop], tests: SuiteEvents, gates: GateEvents
) -> list[str]:
    """The short list. Only things a reviewer should actually look at."""
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
