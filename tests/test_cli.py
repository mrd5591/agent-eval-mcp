"""The command line."""

from __future__ import annotations

import json

from agent_eval.cli import main

from . import fixtures as fx


def test_analyze_prints_a_text_report(tmp_path, capsys):
    path = fx.write_transcript(tmp_path / "s.jsonl", fx.simple_session())

    exit_code = main(["analyze", str(path)])

    assert exit_code == 0
    assert "Session" in capsys.readouterr().out


def test_analyze_json_is_machine_readable(tmp_path, capsys):
    path = fx.write_transcript(tmp_path / "s.jsonl", fx.simple_session())

    main(["analyze", str(path), "--json"])

    assert json.loads(capsys.readouterr().out)["session_id"] == fx.SESSION_ID


def test_analyze_returns_nonzero_when_findings_exist_and_strict(tmp_path, capsys):
    records = []
    for i in range(3):
        records.append(fx.tool_use("Bash", {"command": "mvn verify"}, f"t{i}", uuid=f"a{i}"))
        records.append(fx.tool_result(f"t{i}", "fail", is_error=True, uuid=f"r{i}"))
    path = fx.write_transcript(tmp_path / "s.jsonl", records)

    assert main(["analyze", str(path)]) == 0
    assert main(["analyze", str(path), "--strict"]) == 1


def test_analyze_missing_file_is_an_error(tmp_path, capsys):
    exit_code = main(["analyze", str(tmp_path / "nope.jsonl")])

    assert exit_code == 2
    assert "not found" in capsys.readouterr().err.lower()


def test_sessions_lists_transcripts(tmp_path, capsys):
    fx.write_transcript(tmp_path / "p" / "one.jsonl", fx.simple_session())
    fx.write_transcript(tmp_path / "p" / "two.jsonl", fx.simple_session())

    exit_code = main(["sessions", "--root", str(tmp_path)])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "one.jsonl" in out and "two.jsonl" in out


def test_sessions_json_output(tmp_path, capsys):
    fx.write_transcript(tmp_path / "p" / "one.jsonl", fx.simple_session())

    main(["sessions", "--root", str(tmp_path), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == 1
    assert payload[0]["session_id"] == fx.SESSION_ID


def test_sessions_on_empty_root(tmp_path, capsys):
    assert main(["sessions", "--root", str(tmp_path)]) == 0
    assert "No sessions" in capsys.readouterr().out


def test_no_command_prints_help(capsys):
    assert main([]) == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_loop_threshold_is_configurable(tmp_path, capsys):
    records = []
    for i in range(3):
        records.append(fx.tool_use("Bash", {"command": "ls"}, f"t{i}", uuid=f"a{i}"))
        records.append(fx.tool_result(f"t{i}", "ok", uuid=f"r{i}"))
    path = fx.write_transcript(tmp_path / "s.jsonl", records)

    main(["analyze", str(path), "--json", "--loop-threshold", "9"])
    assert json.loads(capsys.readouterr().out)["loops"] == []

    main(["analyze", str(path), "--json", "--loop-threshold", "3"])
    assert json.loads(capsys.readouterr().out)["loops"]
