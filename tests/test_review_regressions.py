"""Regressions from the 2026-09 review: privacy, robustness, detection accuracy."""

from __future__ import annotations

import io
import json
import os
import sys

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from agent_eval import mcp_server
from agent_eval.cli import main
from agent_eval.metrics import detect_loops, evaluate, gate_events, suite_events
from agent_eval.report import render_json, render_text
from agent_eval.transcript import iter_session_files, parse_session, read_cost

from . import fixtures as fx
from .test_mcp_server import payload

SECRET = "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG"


def _repeated_write(path, times=3):
    records = []
    for i in range(times):
        records.append(
            fx.tool_use(
                "Write", {"file_path": "/repo/.env", "content": SECRET}, f"t{i}", uuid=f"a{i}"
            )
        )
        records.append(fx.tool_result(f"t{i}", "ok", uuid=f"r{i}"))
    return fx.write_transcript(path, records)


# --- privacy: file contents never leave the analyser -------------------------


def test_loop_signature_never_carries_file_content(tmp_path):
    session = parse_session(_repeated_write(tmp_path / "s.jsonl"))

    loops = detect_loops(session, threshold=3)

    assert len(loops) == 1
    assert SECRET not in loops[0].signature
    assert "/repo/.env" in loops[0].signature


def test_reports_never_contain_file_content(tmp_path):
    evaluation = evaluate(parse_session(_repeated_write(tmp_path / "s.jsonl")))

    assert evaluation.loops
    assert SECRET not in render_json(evaluation)
    assert SECRET not in render_text(evaluation)


@pytest.mark.anyio
async def test_mcp_tools_never_return_file_content(tmp_path):
    path = str(_repeated_write(tmp_path / "s.jsonl"))

    analysed = await mcp_server.server.call_tool("analyze_session", {"path": path})
    loops = await mcp_server.server.call_tool("find_loops", {"path": path})

    assert payload(loops)
    assert SECRET not in json.dumps(payload(analysed))
    assert SECRET not in json.dumps(payload(loops))


def test_identical_writes_are_one_loop_and_different_ones_are_not(tmp_path):
    same = parse_session(_repeated_write(tmp_path / "same.jsonl"))
    records = []
    for i in range(3):
        records.append(
            fx.tool_use(
                "Write", {"file_path": "/repo/.env", "content": f"v{i}"}, f"t{i}", uuid=f"a{i}"
            )
        )
    different = parse_session(fx.write_transcript(tmp_path / "diff.jsonl", records))

    assert len(detect_loops(same)) == 1
    assert detect_loops(different) == []


@pytest.mark.anyio
async def test_loop_threshold_below_two_is_rejected(tmp_path):
    path = str(fx.write_transcript(tmp_path / "s.jsonl", fx.simple_session()))

    with pytest.raises(ToolError, match="threshold"):
        await mcp_server.server.call_tool("find_loops", {"path": path, "threshold": 1})
    with pytest.raises(ToolError, match="threshold"):
        await mcp_server.server.call_tool("analyze_session", {"path": path, "loop_threshold": 0})


# --- session discovery -------------------------------------------------------


def test_subagent_and_journal_files_are_not_sessions_by_default(tmp_path):
    fx.write_transcript(tmp_path / "p" / "top.jsonl", fx.simple_session())
    fx.write_transcript(tmp_path / "p" / "top" / "subagents" / "agent-1.jsonl", fx.simple_session())
    fx.write_transcript(tmp_path / "p" / "journal.jsonl", [{"type": "started", "agentId": "x"}])

    assert [p.name for p in iter_session_files(tmp_path)] == ["top.jsonl"]
    assert sorted(p.name for p in iter_session_files(tmp_path, include_subagents=True)) == [
        "agent-1.jsonl",
        "top.jsonl",
    ]


def test_sessions_are_listed_newest_first(tmp_path):
    old = fx.write_transcript(tmp_path / "p" / "old.jsonl", fx.simple_session())
    new = fx.write_transcript(tmp_path / "p" / "new.jsonl", fx.simple_session())
    os.utime(old, (1_600_000_000, 1_600_000_000))
    os.utime(new, (1_700_000_000, 1_700_000_000))

    assert [p.name for p in iter_session_files(tmp_path)] == ["new.jsonl", "old.jsonl"]


@pytest.mark.anyio
async def test_directory_tools_reject_a_missing_root(tmp_path):
    missing = str(tmp_path / "nope")

    with pytest.raises(ToolError, match="Not a directory"):
        await mcp_server.server.call_tool("list_sessions", {"root": missing})
    with pytest.raises(ToolError, match="Not a directory"):
        await mcp_server.server.call_tool("cost_report", {"root": missing})


@pytest.mark.anyio
async def test_directory_tools_expand_tilde(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    fx.write_transcript(tmp_path / "logs" / "one.jsonl", fx.simple_session())

    result = await mcp_server.server.call_tool("list_sessions", {"root": "~/logs"})

    assert len(payload(result)) == 1


def test_cli_sessions_rejects_a_missing_root(tmp_path, capsys):
    assert main(["sessions", "--root", str(tmp_path / "nope")]) == 2
    assert "not a directory" in capsys.readouterr().err.lower()


def test_cli_sessions_can_include_subagents(tmp_path, capsys):
    fx.write_transcript(tmp_path / "p" / "top.jsonl", fx.simple_session())
    fx.write_transcript(tmp_path / "p" / "top" / "subagents" / "agent-1.jsonl", fx.simple_session())

    main(["sessions", "--root", str(tmp_path), "--json"])
    assert len(json.loads(capsys.readouterr().out)) == 1

    main(["sessions", "--root", str(tmp_path), "--json", "--include-subagents"])
    assert len(json.loads(capsys.readouterr().out)) == 2


# --- parser robustness -------------------------------------------------------


def test_shape_malformed_records_do_not_abort_the_parse(tmp_path):
    path = tmp_path / "s.jsonl"
    lines = [
        json.dumps({"type": "user", "message": "hi"}),
        json.dumps({"type": "cost-state", "totalCostUSD": "abc", "totalLinesAdded": "12.5"}),
        json.dumps(fx.tool_use("Bash", {"command": "ls"}, "t1")),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    session = parse_session(path)

    assert len(session.tool_calls) == 1
    assert session.cost.total_usd == 0.0
    assert session.cost.lines_added == 0


def test_read_cost_matches_the_full_parse(tmp_path):
    path = fx.write_transcript(tmp_path / "s.jsonl", fx.simple_session())

    assert read_cost(path) == parse_session(path).cost


def test_human_turns_exclude_system_injected_user_records(tmp_path):
    records = [
        fx.user_message("real question"),
        fx.user_message("<command-name>/help</command-name>", uuid="u2"),
        fx.user_message("<local-command-stdout>ok</local-command-stdout>", uuid="u3"),
        fx.user_message("<task-notification>done</task-notification>", uuid="u4"),
        {**fx.user_message("injected", uuid="u5"), "isMeta": True},
        {**fx.user_message("summary", uuid="u6"), "isCompactSummary": True},
        fx.user_message("second real question", uuid="u7"),
    ]
    path = fx.write_transcript(tmp_path / "s.jsonl", records)

    assert parse_session(path).human_turns == 2


# --- test-run detection ------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "uv run pytest -q",
        "python -m pytest tests/",
        "poetry run pytest",
        "npx jest --ci",
        "./gradlew test",
        "make test",
        "mvn clean install",
        "cd repo && pytest -q",
        "cd repo\npytest -q",
        "PYTHONPATH=src pytest",
        "timeout 600 pytest -x",
        "bun test",
        "npm run test",
    ],
)
def test_recognizes_wrapped_and_multiline_test_runs(tmp_path, command):
    records = [fx.tool_use("Bash", {"command": command}, "t1"), fx.tool_result("t1", "ok")]

    session = parse_session(fx.write_transcript(tmp_path / "s.jsonl", records))

    assert suite_events(session).runs == 1


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m 'fix; pytest passes'",
        "echo test done",
        "ls tests/",
        "grep -r pytest .",
        "cat jest.config.js",
    ],
)
def test_ignores_commands_that_only_mention_a_runner(tmp_path, command):
    records = [fx.tool_use("Bash", {"command": command}, "t1"), fx.tool_result("t1", "ok")]

    session = parse_session(fx.write_transcript(tmp_path / "s.jsonl", records))

    assert suite_events(session).runs == 0


def test_an_unfinished_last_test_run_is_not_green(tmp_path):
    records = [
        fx.tool_use("Bash", {"command": "pytest"}, "t1"),
        fx.tool_result("t1", "1 failed", is_error=True),
        fx.tool_use("Bash", {"command": "pytest"}, "t2", uuid="a2"),
    ]

    events = suite_events(parse_session(fx.write_transcript(tmp_path / "s.jsonl", records)))

    assert events.runs == 2
    assert events.failures == 1
    assert events.ended_green is None


# --- output ------------------------------------------------------------------


def test_permission_modes_are_deduplicated_in_order(tmp_path):
    records = [
        fx.permission_mode("auto"),
        fx.permission_mode("default"),
        fx.permission_mode("auto"),
    ]

    gates = gate_events(parse_session(fx.write_transcript(tmp_path / "s.jsonl", records)))

    assert gates.permission_modes == ["auto", "default"]


def test_cli_text_output_survives_a_cp1252_stdout(tmp_path, monkeypatch):
    records = []
    for i in range(3):
        records.append(
            fx.tool_use("Bash", {"command": "git commit -m '✨ feat'"}, f"t{i}", uuid=f"a{i}")
        )
        records.append(fx.tool_result(f"t{i}", "ok", uuid=f"r{i}"))
    path = fx.write_transcript(tmp_path / "s.jsonl", records)
    buffer = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(buffer, encoding="cp1252"))

    exit_code = main(["analyze", str(path)])

    sys.stdout.flush()
    assert exit_code == 0
    assert b"Possible loop" in buffer.getvalue()


def test_cli_and_mcp_session_rows_have_the_same_shape(tmp_path, capsys):
    fx.write_transcript(tmp_path / "p" / "one.jsonl", fx.simple_session())

    main(["sessions", "--root", str(tmp_path), "--json"])
    cli_row = json.loads(capsys.readouterr().out)[0]
    mcp_row = mcp_server.list_sessions(root=str(tmp_path))[0]

    assert cli_row == mcp_row
