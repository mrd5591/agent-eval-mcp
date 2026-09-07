"""The analyses. Each one answers a question you would actually ask about a session."""

from __future__ import annotations

import pytest

from agent_eval.metrics import (
    cost_summary,
    detect_loops,
    evaluate,
    gate_events,
    suite_events,
    tool_usage,
)
from agent_eval.transcript import parse_session

from . import fixtures as fx


def session_from(tmp_path, records):
    return parse_session(fx.write_transcript(tmp_path / "s.jsonl", records))


# --- tool usage -------------------------------------------------------------


def test_tool_usage_counts_and_failure_rate(tmp_path):
    records = [
        fx.tool_use("Bash", {"command": "a"}, "t1"),
        fx.tool_result("t1", "ok"),
        fx.tool_use("Bash", {"command": "b"}, "t2", uuid="a2"),
        fx.tool_result("t2", "bad", is_error=True, uuid="r2"),
        fx.tool_use("Edit", {"file_path": "x"}, "t3", uuid="a3"),
        fx.tool_result("t3", "ok", uuid="r3"),
    ]
    usage = tool_usage(session_from(tmp_path, records))

    assert usage.total_calls == 3
    assert usage.by_tool["Bash"].calls == 2
    assert usage.by_tool["Bash"].failures == 1
    assert usage.by_tool["Bash"].failure_rate == pytest.approx(0.5)
    assert usage.by_tool["Edit"].failure_rate == 0.0
    assert usage.overall_failure_rate == pytest.approx(1 / 3)


def test_tool_usage_on_empty_session(tmp_path):
    usage = tool_usage(session_from(tmp_path, [fx.user_message("hi")]))

    assert usage.total_calls == 0
    assert usage.overall_failure_rate == 0.0
    assert usage.by_tool == {}


# --- loops ------------------------------------------------------------------


def test_detects_a_repeated_identical_call(tmp_path):
    records = []
    for i in range(3):
        records.append(fx.tool_use("Bash", {"command": "pytest -q"}, f"t{i}", uuid=f"a{i}"))
        records.append(fx.tool_result(f"t{i}", "1 failed", is_error=True, uuid=f"r{i}"))

    loops = detect_loops(session_from(tmp_path, records), threshold=3)

    assert len(loops) == 1
    assert loops[0].tool == "Bash"
    assert loops[0].occurrences == 3
    assert loops[0].all_failed is True


def test_repetition_below_threshold_is_not_a_loop(tmp_path):
    records = []
    for i in range(2):
        records.append(fx.tool_use("Bash", {"command": "ls"}, f"t{i}", uuid=f"a{i}"))
        records.append(fx.tool_result(f"t{i}", "ok", uuid=f"r{i}"))

    assert detect_loops(session_from(tmp_path, records), threshold=3) == []


def test_reads_of_the_same_file_are_not_a_loop(tmp_path):
    # Re-reading a file is normal navigation, not thrash. Only mutating and
    # executing tools count, or every session looks pathological.
    records = []
    for i in range(5):
        records.append(fx.tool_use("Read", {"file_path": "/repo/a.py"}, f"t{i}", uuid=f"a{i}"))
        records.append(fx.tool_result(f"t{i}", "contents", uuid=f"r{i}"))

    assert detect_loops(session_from(tmp_path, records), threshold=3) == []


def test_successful_repetition_is_reported_but_not_all_failed(tmp_path):
    records = []
    for i in range(3):
        records.append(fx.tool_use("Bash", {"command": "git status"}, f"t{i}", uuid=f"a{i}"))
        records.append(fx.tool_result(f"t{i}", "clean", uuid=f"r{i}"))

    loops = detect_loops(session_from(tmp_path, records), threshold=3)

    assert loops[0].all_failed is False


# --- test events ------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    ["pytest -q", "npm test", "mvn verify", "go test ./...", "cargo test", "dotnet test"],
)
def test_recognizes_common_test_runners(tmp_path, command):
    records = [
        fx.tool_use("Bash", {"command": command}, "t1"),
        fx.tool_result("t1", "ok"),
    ]

    events = suite_events(session_from(tmp_path, records))

    assert events.runs == 1


def test_ignores_commands_that_merely_mention_tests(tmp_path):
    records = [
        fx.tool_use("Bash", {"command": "ls tests/"}, "t1"),
        fx.tool_result("t1", "a.py"),
    ]

    assert suite_events(session_from(tmp_path, records)).runs == 0


def test_counts_failures_and_final_state(tmp_path):
    records = [
        fx.tool_use("Bash", {"command": "pytest"}, "t1"),
        fx.tool_result("t1", "1 failed", is_error=True),
        fx.tool_use("Bash", {"command": "pytest"}, "t2", uuid="a2"),
        fx.tool_result("t2", "2 passed", uuid="r2"),
    ]

    events = suite_events(session_from(tmp_path, records))

    assert events.runs == 2
    assert events.failures == 1
    assert events.ended_green is True


def test_session_ending_on_a_failing_test_is_not_green(tmp_path):
    records = [
        fx.tool_use("Bash", {"command": "pytest"}, "t1"),
        fx.tool_result("t1", "1 failed", is_error=True),
    ]

    assert suite_events(session_from(tmp_path, records)).ended_green is False


def test_no_test_runs_means_unknown_not_green(tmp_path):
    events = suite_events(session_from(tmp_path, [fx.user_message("hi")]))

    assert events.runs == 0
    assert events.ended_green is None


# --- gates ------------------------------------------------------------------


def test_reports_permission_mode_and_hook_blocks(tmp_path):
    records = [
        fx.permission_mode("bypassPermissions"),
        fx.hook_block("PreToolUse:Bash", "rm -rf refused"),
    ]

    gates = gate_events(session_from(tmp_path, records))

    assert gates.permission_modes == ["bypassPermissions"]
    assert gates.ran_without_prompts is True
    assert gates.hook_blocks == 1


def test_default_permission_mode_is_not_flagged(tmp_path):
    gates = gate_events(session_from(tmp_path, [fx.permission_mode("default")]))

    assert gates.ran_without_prompts is False
    assert gates.hook_blocks == 0


# --- cost -------------------------------------------------------------------


def test_cost_per_changed_line(tmp_path):
    session = session_from(tmp_path, [fx.cost_state(total_cost=3.0, lines_added=90, lines_removed=10)])

    summary = cost_summary(session)

    assert summary.total_usd == pytest.approx(3.0)
    assert summary.lines_changed == 100
    assert summary.usd_per_100_lines == pytest.approx(3.0)


def test_cost_per_line_is_none_when_nothing_changed(tmp_path):
    session = session_from(tmp_path, [fx.cost_state(total_cost=2.0, lines_added=0, lines_removed=0)])

    assert cost_summary(session).usd_per_100_lines is None


# --- aggregate --------------------------------------------------------------


def test_evaluate_assembles_every_section(tmp_path):
    session = session_from(tmp_path, fx.simple_session())

    result = evaluate(session)

    assert result.session_id == fx.SESSION_ID
    assert result.tools.total_calls == 2
    assert result.tests.runs == 1
    assert result.cost.total_usd == pytest.approx(1.5)
    assert result.loops == []
    assert result.gates.permission_modes == ["auto"]


def test_evaluate_flags_a_thrashing_session(tmp_path):
    records = []
    for i in range(4):
        records.append(fx.tool_use("Bash", {"command": "mvn verify"}, f"t{i}", uuid=f"a{i}"))
        records.append(fx.tool_result(f"t{i}", "BUILD FAILURE", is_error=True, uuid=f"r{i}"))
    result = evaluate(session_from(tmp_path, records))

    assert result.loops and result.loops[0].occurrences == 4
    assert result.tests.ended_green is False
    assert "loop" in " ".join(result.findings).lower()


def test_findings_are_empty_for_a_clean_session(tmp_path):
    result = evaluate(session_from(tmp_path, fx.simple_session()))

    assert result.findings == []


# --- regression: loop signatures for editing tools --------------------------
#
# Found by running the analyser over a real 7.7-hour session. Five separate edits
# to one file were reported as a loop, because the Edit signature keyed on
# file_path alone. Editing the same file repeatedly is ordinary iterative work;
# only an identical repeated edit is thrash.


def test_different_edits_to_the_same_file_are_not_a_loop(tmp_path):
    records = []
    for i in range(5):
        records.append(
            fx.tool_use(
                "Edit",
                {"file_path": "/repo/a.py", "old_string": f"before {i}", "new_string": f"after {i}"},
                f"t{i}",
                uuid=f"a{i}",
            )
        )
        records.append(fx.tool_result(f"t{i}", "ok", uuid=f"r{i}"))

    assert detect_loops(session_from(tmp_path, records), threshold=3) == []


def test_the_identical_edit_repeated_is_still_a_loop(tmp_path):
    records = []
    for i in range(3):
        records.append(
            fx.tool_use(
                "Edit",
                {"file_path": "/repo/a.py", "old_string": "x", "new_string": "y"},
                f"t{i}",
                uuid=f"a{i}",
            )
        )
        records.append(fx.tool_result(f"t{i}", "no match", is_error=True, uuid=f"r{i}"))

    loops = detect_loops(session_from(tmp_path, records), threshold=3)

    assert len(loops) == 1
    assert loops[0].occurrences == 3
    assert loops[0].all_failed is True


def test_writes_of_different_content_to_one_path_are_not_a_loop(tmp_path):
    records = []
    for i in range(4):
        records.append(
            fx.tool_use(
                "Write", {"file_path": "/repo/b.py", "content": f"version {i}"}, f"t{i}", uuid=f"a{i}"
            )
        )
        records.append(fx.tool_result(f"t{i}", "ok", uuid=f"r{i}"))

    assert detect_loops(session_from(tmp_path, records), threshold=3) == []
