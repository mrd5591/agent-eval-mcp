"""Parsing a session file."""

from __future__ import annotations

import pytest

from agent_eval.transcript import ToolCall, iter_session_files, parse_session

from . import fixtures as fx


def test_parses_session_identity(tmp_path):
    path = fx.write_transcript(tmp_path / "s.jsonl", fx.simple_session())

    session = parse_session(path)

    assert session.session_id == fx.SESSION_ID
    assert session.cwd == fx.CWD
    assert session.git_branch == "main"
    assert session.path == path


def test_pairs_tool_calls_with_their_results(tmp_path):
    path = fx.write_transcript(tmp_path / "s.jsonl", fx.simple_session())

    session = parse_session(path)

    assert [call.name for call in session.tool_calls] == ["Edit", "Bash"]
    edit, bash = session.tool_calls
    assert edit.tool_id == "t1"
    assert edit.failed is False
    assert bash.command == "pytest -q"
    assert bash.result_text.startswith("12 passed")


def test_marks_failed_tool_results(tmp_path):
    records = [
        fx.tool_use("Bash", {"command": "make build"}, "t1"),
        fx.tool_result("t1", "Exit code 1\nboom", is_error=True),
    ]
    path = fx.write_transcript(tmp_path / "s.jsonl", records)

    call = parse_session(path).tool_calls[0]

    assert call.failed is True
    assert "boom" in call.result_text


def test_tool_call_without_result_is_unresolved(tmp_path):
    path = fx.write_transcript(
        tmp_path / "s.jsonl", [fx.tool_use("Read", {"file_path": "x"}, "t9")]
    )

    call = parse_session(path).tool_calls[0]

    assert call.failed is False
    assert call.result_text == ""
    assert call.resolved is False


def test_reads_cost_state(tmp_path):
    path = fx.write_transcript(tmp_path / "s.jsonl", fx.simple_session())

    session = parse_session(path)

    assert session.cost.total_usd == pytest.approx(1.5)
    assert session.cost.lines_added == 120
    assert session.cost.lines_removed == 30
    assert session.cost.models == ["claude-opus-5"]


def test_missing_cost_state_is_zeroed(tmp_path):
    path = fx.write_transcript(tmp_path / "s.jsonl", [fx.user_message("hi")])

    cost = parse_session(path).cost

    assert cost.total_usd == 0.0
    assert cost.lines_added == 0
    assert cost.models == []


def test_counts_human_turns_not_tool_results(tmp_path):
    records = [
        fx.user_message("first"),
        fx.tool_use("Edit", {"file_path": "a"}, "t1"),
        fx.tool_result("t1", "ok"),
        fx.user_message("second", uuid="u2"),
    ]
    path = fx.write_transcript(tmp_path / "s.jsonl", records)

    assert parse_session(path).human_turns == 2


def test_skips_malformed_lines_without_failing(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text('{"broken": \n\n{"type":"user"}\n', encoding="utf-8")

    session = parse_session(path)

    assert session.tool_calls == []


def test_records_permission_mode(tmp_path):
    path = fx.write_transcript(tmp_path / "s.jsonl", fx.simple_session())

    assert parse_session(path).permission_modes == ["auto"]


def test_iter_session_files_finds_transcripts(tmp_path):
    fx.write_transcript(tmp_path / "proj-a" / "one.jsonl", [fx.user_message("x")])
    fx.write_transcript(tmp_path / "proj-b" / "two.jsonl", [fx.user_message("y")])
    (tmp_path / "proj-b" / "notes.txt").write_text("ignore me", encoding="utf-8")

    found = sorted(p.name for p in iter_session_files(tmp_path))

    assert found == ["one.jsonl", "two.jsonl"]


def test_iter_session_files_on_missing_root_is_empty(tmp_path):
    assert list(iter_session_files(tmp_path / "nope")) == []


def test_tool_call_signature_normalizes_whitespace_and_paths():
    a = ToolCall(tool_id="1", name="Bash", tool_input={"command": "pytest   -q"})
    b = ToolCall(tool_id="2", name="Bash", tool_input={"command": "pytest -q"})
    c = ToolCall(tool_id="3", name="Bash", tool_input={"command": "pytest -x"})

    assert a.signature == b.signature
    assert a.signature != c.signature


def test_transcript_never_exposes_prompt_text(tmp_path):
    path = fx.write_transcript(tmp_path / "s.jsonl", [fx.user_message("my secret business plan")])

    session = parse_session(path)

    assert "secret" not in repr(session)
