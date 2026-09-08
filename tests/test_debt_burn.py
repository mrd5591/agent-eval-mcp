"""Regressions for the 2026-09-08 debt pass.

Each test names the finding it closes, so a later change that reopens one fails
against a statement of intent rather than an unexplained assertion.
"""

from __future__ import annotations

import pytest

from agent_eval.metrics import evaluate, is_test_run, looks_failed, suite_events
from agent_eval.transcript import ToolCall, load_session

from .fixtures import (
    cost_state,
    hook_block,
    permission_mode,
    tool_result,
    tool_use,
    user_message,
    write_transcript,
)

# --------------------------------------------------------------------------
# Finding 2: `is_test_run` misses common real invocations.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        # `("uvx", "")` required an empty second token, which no tokeniser
        # produces, so this never matched.
        "uvx pytest",
        "uvx pytest -q tests/",
        # `("yarn", "run")` peeled to ["test"], which is not a runner, so yarn
        # was the one package manager whose `run test` did not classify.
        "yarn run test",
        "npm run test",
        "pnpm run test",
    ],
)
def test_recognises_invocations_that_were_missed(command: str) -> None:
    assert is_test_run(command), f"{command!r} runs tests"


# --------------------------------------------------------------------------
# Finding 3: subcommand matched anywhere in the argument list.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        # The subcommand is positional. These name "test" somewhere, but not in
        # the slot that decides what runs.
        "go run ./cmd/seed test",
        "cargo run -- test",
        "npm run build -- --env test",
        # `-C` takes a directory; the directory is merely called "test".
        "make -C test all",
        # Skip flags: the suite is explicitly not run.
        "mvn clean install -DskipTests",
        "mvn -Dmaven.test.skip=true verify",
        "gradle build -x test",
        # Queries, not runs.
        "pytest --version",
        "pytest --collect-only",
        "dotnet test --help",
        # Not a test runner at all.
        "git commit -m test",
        "echo test",
    ],
)
def test_rejects_commands_that_do_not_run_tests(command: str) -> None:
    assert not is_test_run(command), f"{command!r} does not run a suite"


@pytest.mark.parametrize(
    "command",
    [
        # Multi-target tools take a list of goals, all of which run.
        "mvn clean install",
        "mvn clean verify",
        "make lint test",
        "gradle -p service test",
        "mvn -s settings.xml test",
        "make -C build test",
        # `make -s` is silent and takes no value, unlike `mvn -s`. A shared
        # value-flag set would swallow the target here.
        "make -s test",
        "dotnet -c Release test",
        # Verbosity and parallelism are not skip flags, however much they look
        # like query flags. These are the two most common pytest invocations
        # there are.
        "pytest -v",
        "pytest -n 4",
    ],
)
def test_still_recognises_real_runs(command: str) -> None:
    assert is_test_run(command), f"{command!r} runs tests"


def test_windows_paths_survive_tokenising() -> None:
    """POSIX shlex eats backslashes, so a Windows path lost its separators.

    `_program` carried backslash-normalising code that could never fire, because
    there were no backslashes left by the time it ran - on a project with a
    Windows CI matrix.
    """
    assert is_test_run(r"C:\repo\.venv\Scripts\pytest.exe -q")
    assert is_test_run(r".\.venv\Scripts\pytest.exe")


def test_a_command_that_does_not_lex_still_segments() -> None:
    """The escape-free reading is preferred, not mandatory.

    Disabling the backslash escape makes `\\"` read as an unbalanced quote. If that
    were the only reading, the naive `split()` fallback would lose `;` and `&&` as
    separators, merge every segment into one, and hide the runner behind whatever
    came first. Falling back to the POSIX reading keeps these working.
    """
    assert is_test_run('grep -E "^- [0-9]+ (a|b)" out.txt ; npx vitest run')
    assert is_test_run('echo "unbalanced ; pytest -q')


def test_glued_shell_operators_still_separate_commands() -> None:
    """shlex glues adjacent operators, so `);` arrives as one token.

    A plain membership test against the separator set missed it, merging two
    commands into one segment and hiding the second program behind the first.
    While any argument counted as a match this produced the right answer for the
    wrong reason; matching the subcommand slot exposed it.
    """
    assert is_test_run("(ls node_modules || npm ci) ; npm test")
    assert is_test_run("(cd app && npm ci 2>&1); npm test > out.log 2>&1")
    # And the guard does not fire where there is no second command.
    assert not is_test_run("(ls node_modules || npm ci)")


# --------------------------------------------------------------------------
# Finding 1: green/red decided by the transport flag alone.
# --------------------------------------------------------------------------


def _call(command: str, output: str, *, is_error: bool = False) -> ToolCall:
    call = ToolCall(tool_id="t1", name="Bash", tool_input={"command": command})
    call.result_text = output
    call.failed = is_error
    call.resolved = True
    return call


@pytest.mark.parametrize(
    "output",
    [
        "FAILED tests/test_x.py::test_y - assert 1 == 2",
        "=========================== FAILURES ===========================",
        "--- FAIL: TestThing (0.00s)",
        "AssertionError: expected 3, got 4",
        "[INFO] BUILD FAILURE",
        "Tests run: 12, Failures: 1, Errors: 0, Skipped: 0",
        "panic: runtime error: index out of range",
        "Tests  2 failed | 40 passed",
    ],
)
def test_a_failing_run_is_read_from_its_output(output: str) -> None:
    """A pipeline exits with its last command's status, so the flag says nothing.

    `pytest ... 2>&1 | head -30` exits 0 however the tests went, and that shape is
    85% of the test-run commands in the corpus this tool was measured against.
    """
    assert looks_failed(_call("pytest -q 2>&1 | head -30", output))


@pytest.mark.parametrize(
    "output",
    [
        "===== 41 passed in 1.20s =====",
        "ok  \tgithub.com/x/y\t0.31s",
        "Tests run: 12, Failures: 0, Errors: 0, Skipped: 0",
        "BUILD SUCCESS",
        "Tests  0 failed | 42 passed",
        "",
    ],
)
def test_a_passing_run_stays_green(output: str) -> None:
    assert not looks_failed(_call("pytest -q 2>&1 | head -30", output))


def test_output_reading_can_only_add_failures() -> None:
    """A run the transport already called failed stays failed.

    The second opinion exists to catch failures the flag missed. If it could also
    clear one, a runner whose output this code does not understand would start
    laundering real failures into greens.
    """
    assert looks_failed(_call("pytest -q", "===== 41 passed =====", is_error=True))


def test_suite_events_reads_the_ending_from_output(tmp_path) -> None:
    """The whole point, end to end: a suite that failed while the command exited 0."""
    records = [
        user_message("run the tests"),
        tool_use("Bash", {"command": "pytest -q 2>&1 | head -30"}, "t1"),
        tool_result("t1", "FAILED tests/test_x.py::test_y - assert 1 == 2", is_error=False),
    ]
    session = load_session(write_transcript(tmp_path / "s.jsonl", records))

    events = suite_events(session)
    assert events.runs == 1
    assert events.failures == 1
    assert events.ended_green is False, "a red suite behind a green exit code must read as red"


def test_suite_events_still_reports_green(tmp_path) -> None:
    records = [
        user_message("run the tests"),
        tool_use("Bash", {"command": "pytest -q"}, "t1"),
        tool_result("t1", "===== 41 passed in 1.20s =====", is_error=False),
    ]
    session = load_session(write_transcript(tmp_path / "s.jsonl", records))
    assert suite_events(session).ended_green is True


# --------------------------------------------------------------------------
# Finding 6: two user-facing findings never exercised end to end.
# --------------------------------------------------------------------------


def test_high_failure_rate_finding_reaches_the_output(tmp_path) -> None:
    """Twelve Bash calls, five of them errors: over the 25% threshold at >= 10 calls.

    The string is the tool's own output and no test reached it through `evaluate`;
    the existing coverage called `gate_events` directly, one layer below where a
    user would ever see it.
    """
    records: list[dict] = [user_message("do the thing")]
    for index in range(12):
        tool_id = f"t{index}"
        records.append(tool_use("Bash", {"command": f"echo {index}"}, tool_id, uuid=f"a{index}"))
        failed = index < 5
        records.append(
            tool_result(tool_id, "boom" if failed else "ok", is_error=failed, uuid=f"r{index}")
        )
    records.append(cost_state())

    session = load_session(write_transcript(tmp_path / "s.jsonl", records))
    evaluation = evaluate(session)

    assert evaluation.tools.total_calls == 12
    assert evaluation.tools.total_failures == 5
    finding = next((f for f in evaluation.findings if f.startswith("High tool failure rate")), None)
    assert finding is not None, evaluation.findings
    assert "42% of 12 calls" in finding


def test_hook_block_finding_reaches_the_output(tmp_path) -> None:
    records = [
        user_message("do the thing"),
        permission_mode("auto"),
        hook_block(),
        tool_use("Bash", {"command": "echo hi"}, "t1"),
        tool_result("t1", "hi"),
        cost_state(),
    ]
    session = load_session(write_transcript(tmp_path / "s.jsonl", records))
    evaluation = evaluate(session)

    assert evaluation.gates.hook_blocks == 1
    assert "Hooks blocked 1 tool call(s)" in evaluation.findings


def test_no_findings_on_a_clean_session(tmp_path) -> None:
    """The counterpart: the strings above must not appear when nothing is wrong."""
    records = [
        user_message("do the thing"),
        tool_use("Bash", {"command": "pytest -q"}, "t1"),
        tool_result("t1", "===== 41 passed in 1.20s ====="),
        cost_state(),
    ]
    session = load_session(write_transcript(tmp_path / "s.jsonl", records))
    assert evaluate(session).findings == []
