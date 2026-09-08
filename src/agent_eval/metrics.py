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

# shlex's punctuation_chars mode glues adjacent shell operators into a single
# token, so `...log 2>&1); npm test` arrives with `);` as one token. A plain
# membership test against _SEGMENT_BREAKS misses it, the two commands merge into
# one segment, and the second command's program is hidden behind the first's -
# `npm test` after an `npm ci` was read as part of the `ci`.
#
# This was invisible while any argument anywhere counted as a subcommand match:
# the merged segment still contained the word "test", so the right answer came
# out for the wrong reason. Matching the subcommand slot properly is what
# exposed it.
_PUNCTUATION_ONLY = re.compile(r"^[();&|<>]+$")
_BREAK_CHARS = frozenset({";", "&", "|"})

# Failure markers looked for in a test run's own output.
#
# The transport's `is_error` flag is not enough on its own, and the corpus says
# why: 85% of the test-run commands in it pipe through `2>&1 | head -30` or
# similar. A shell pipeline exits with the status of its *last* command, so
# `pytest ... | head` exits 0 however the tests went, and the run is recorded as
# green. `pytest || true` and a wrapper that swallows the code do the same thing
# for the same reason. A tool whose central claim is that it measures what the
# agent actually did cannot take the exit status at face value.
#
# Deliberately narrow, and only ever used to turn a green *red*, never the other
# way round - see suite_events. A marker that fires wrongly costs a false alarm
# on one session; a missing marker costs nothing that was not already missing.
_FAILURE_MARKERS = (
    re.compile(r"\bFAILED\b"),  # pytest, jest
    re.compile(r"^\s*(?:---\s*)?FAIL\b", re.MULTILINE),  # go test, jest suite lines
    re.compile(r"\bAssertionError\b"),
    re.compile(r"\bBUILD FAILURE\b"),  # maven
    re.compile(r"Tests run:.*?(?:Failures|Errors): [1-9]"),  # maven summary
    re.compile(r"^\s*panic:", re.MULTILINE),  # go
    re.compile(r"=+ FAILURES =+"),  # pytest section header
    re.compile(r"\b[1-9]\d* (?:failed|failing)\b"),  # vitest, jest, mocha
)
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# `uvx` belongs here, not in the two-word set: it takes the tool as its very next
# word, so `uvx pytest` peels to `pytest`. As a ("uvx", "") pair it required an
# empty second token, which no tokeniser produces, so it never matched anything.
_ONE_WORD_WRAPPERS = frozenset(
    {"sudo", "time", "nice", "env", "exec", "command", "npx", "bunx", "uvx"}
)
_TWO_WORD_WRAPPERS = frozenset(
    {
        ("uv", "run"),
        ("poetry", "run"),
        ("pipenv", "run"),
        ("pdm", "run"),
        ("hatch", "run"),
        ("pnpm", "exec"),
        ("pnpm", "dlx"),
        ("npm", "exec"),
        ("python", "-m"),
        ("python3", "-m"),
        ("py", "-m"),
    }
)

# Package managers whose test script hides behind `run`: `npm run test`,
# `yarn run test`. The subcommand slot holds "run", so the slot after it is the
# one to read.
#
# `("yarn", "run")` used to sit in _TWO_WORD_WRAPPERS, which peeled it to
# ["test"] - and "test" is not a runner, so `yarn run test` classified as *not*
# a test run. npm and pnpm only worked by accident: they had no "run" entry, so
# they fell through to the old scan-every-argument match.
_RUN_INDIRECTION = frozenset({"npm", "yarn", "pnpm", "bun", "deno"})

# Tools that take a list of goals or targets rather than one subcommand. Every
# positional is a thing that runs, so any of them may be the test one:
# `mvn clean install` genuinely runs the install phase, and `make lint test`
# genuinely runs tests.
_MULTI_TARGET = frozenset({"mvn", "gradle", "gradlew", "make", "rake"})

# Flags that mean "do not actually run the tests". A command carrying one of
# these is a build or a query, and counting it as a test run is how a suite
# nobody ran becomes evidence that the suite passed.
#
# Deliberately conservative. `-v` and `-n` are *not* here despite looking like
# query flags: `pytest -v` is a verbose run and `pytest -n 4` is a parallel one,
# and suppressing either would trade a false positive for a false negative on
# two of the most common invocations there are. Matched case-insensitively so
# `-DskipTests` and `-Dskiptests` behave the same.
_SKIP_FLAGS = frozenset(
    {
        "-dskiptests",
        "-dskiptests=true",
        "-dmaven.test.skip",
        "-dmaven.test.skip=true",
        "--collect-only",
        "--co",
        "--version",
        "--help",
        "-h",
        "--dry-run",
        "--list-tests",
    }
)

# Gradle spells exclusion as a separate argument: `gradle build -x test`.
_SKIP_PAIRS = frozenset({("-x", "test"), ("--exclude-task", "test")})

# Flags that consume the word after them, which is therefore an option value and
# not a subcommand or target. Without this, `make -C test all` reads "test" as a
# target and counts as a test run - the directory merely happens to be named
# after the thing it is not doing.
#
# Per program, and case-sensitive, because a global set collides: `make -s` is
# silent and takes no value, while `mvn -s` names a settings file; `-C` is a
# directory for make and Go, while `-c` is a configuration for dotnet. Attached
# forms (`-C=test`, `--prefix=test`) need no entry - they are one token starting
# with "-", so they are skipped as flags anyway.
_VALUE_FLAGS: dict[str, frozenset[str]] = {
    "make": frozenset(
        {"-C", "--directory", "-f", "--file", "--makefile", "-j", "--jobs", "-o", "-W"}
    ),
    "go": frozenset({"-C"}),
    "mvn": frozenset({"-f", "--file", "-s", "--settings", "-P", "--activate-profiles", "-T"}),
    "gradle": frozenset({"-p", "--project-dir", "-b", "--build-file", "-I", "--init-script"}),
    "gradlew": frozenset({"-p", "--project-dir", "-b", "--build-file", "-I", "--init-script"}),
    "cargo": frozenset({"--manifest-path", "-p", "--package", "--features", "-j", "--jobs"}),
    "npm": frozenset({"--prefix", "-w", "--workspace"}),
    "yarn": frozenset({"--cwd"}),
    "pnpm": frozenset({"-C", "--dir", "-w", "--workspace"}),
    "dotnet": frozenset({"-c", "--configuration", "-f", "--framework", "-o", "--output"}),
    "rake": frozenset({"-f", "--rakefile", "-C", "--directory"}),
}


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


def _is_break(token: str) -> bool:
    """Whether a token separates two commands.

    A token of nothing but shell operators counts if any of them is a separator,
    which covers the glued forms shlex produces (`);`, `|&`) as well as the plain
    ones. A redirection such as `>&` also breaks; that is harmless, because what
    identifies a command is the program at the head of its segment and no
    redirection ever precedes one.
    """
    if token in _SEGMENT_BREAKS:
        return True
    return bool(_PUNCTUATION_ONLY.match(token)) and any(c in _BREAK_CHARS for c in token)


def _lex(line: str, *, escape: bool) -> list[str] | None:
    """Tokenise one line, or None if it does not parse."""
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    if not escape:
        lexer.escape = ""
    try:
        return list(lexer)
    except ValueError:
        return None


def _tokenise(line: str) -> list[str]:
    """Tokenise a shell line, preferring the reading that keeps Windows paths intact.

    POSIX shlex treats backslash as an escape, so `C:\\repo\\.venv\\Scripts\\pytest.exe`
    came out as `C:repo.venvScriptspytest.exe` and never classified - the backslash
    normalising in `_program` could not fire, because there were no backslashes left
    by the time it ran.

    Disabling the escape fixes that, but it cannot simply be disabled: a command
    containing `\\"` then reads as an unbalanced quote, and the naive `split()`
    fallback loses `;` and `&&` as separators, which merges every segment into one
    and hides the runner. Three real test runs in a 135k-command corpus regressed
    that way. So the escape-free reading is *preferred* and the POSIX one is the
    fallback, which is a strict improvement over either alone.
    """
    for escape in (False, True):
        tokens = _lex(line, escape=escape)
        if tokens is not None:
            return tokens
    return line.split()


def _segments(command: str) -> Iterable[list[str]]:
    """Split a shell command into its simple commands, one token list each."""
    for line in command.splitlines():
        tokens = _tokenise(line)
        segment: list[str] = []
        for token in tokens:
            if _is_break(token):
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


def _has_skip_flag(tokens: list[str]) -> bool:
    """Whether an argument list says the tests are not to be run."""
    lowered = [token.lower() for token in tokens]
    if any(token in _SKIP_FLAGS for token in lowered):
        return True
    return any(pair in _SKIP_PAIRS for pair in zip(lowered, lowered[1:], strict=False))


def _positionals(program: str, args: list[str]) -> list[str]:
    """The non-flag arguments, lowercased, with option values removed."""
    value_flags = _VALUE_FLAGS.get(program, frozenset())
    out: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg.startswith("-"):
            # A flag that takes a separate value swallows the next word, which is
            # then an option value rather than a subcommand or target.
            skip_next = arg in value_flags
            continue
        out.append(arg.lower())
    return out


def _names_a_test(program: str, subcommands: frozenset[str], args: list[str]) -> bool:
    """Whether the arguments name one of a runner's test subcommands.

    Two shapes, because two kinds of tool are in the table.

    A **multi-target** tool takes a list of goals or targets, all of them positional
    and all of them run: `mvn clean install` really does run the install phase, and
    `make lint test` really does run tests. Any target may be the test one.

    Everything else has a single subcommand *slot*, and only the word in that slot
    counts. Scanning the whole argument list is what made `go run ./cmd/seed test`,
    `cargo run -- test` and `npm run build -- --env test` register as test runs.
    `run` is transparent for the JS package managers, where the script name after it
    is what names the suite.
    """
    positionals = _positionals(program, args)
    if not positionals:
        return False

    if program in _MULTI_TARGET:
        return any(target in subcommands for target in positionals)

    slot = positionals[0]
    if slot == "run" and program in _RUN_INDIRECTION and len(positionals) > 1:
        slot = positionals[1]
    return slot in subcommands


def is_test_run(command: str) -> bool:
    """Whether a shell command runs a test suite.

    The subcommand is matched in its *slot*, not anywhere in the argument list. Scanning
    every argument counted `go run ./cmd/seed test`, `cargo run -- test` and
    `make -C test all` as test runs, and a skip flag was invisible, so
    `mvn clean install -DskipTests` counted too. A suite nobody ran is the worst thing
    for this tool to record as a suite that passed.
    """
    for segment in _segments(command):
        tokens = _strip_wrappers(segment)
        if not tokens:
            continue
        program = _program(tokens[0])
        subcommands = _RUNNERS.get(program, False)
        if subcommands is False:
            continue
        if _has_skip_flag(tokens[1:]):
            continue
        if subcommands is None:
            return True
        if _names_a_test(program, subcommands, tokens[1:]):
            return True
    return False


def looks_failed(call: ToolCall) -> bool:
    """Whether a test run failed, by its transport flag or by its own output.

    The flag alone is not enough. A pipeline exits with the status of its last command,
    so `pytest ... 2>&1 | head -30` reports success whatever the tests did - and that
    shape is 85% of the test-run commands in the corpus this tool was measured against.
    `pytest || true` and a wrapper that eats the code fail the same way.

    The output is therefore read as a second opinion, but only ever to turn a green run
    red. A run the transport already called failed stays failed, so this can add
    detections and cannot hide one.
    """
    if call.failed:
        return True
    return any(marker.search(call.result_text) for marker in _FAILURE_MARKERS)


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
        if looks_failed(call):
            events.failures += 1
        last = call
    if last is not None and last.resolved:
        events.ended_green = not looks_failed(last)
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
