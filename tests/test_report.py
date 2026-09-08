"""Rendering."""

from __future__ import annotations

import json

from agent_eval.metrics import evaluate
from agent_eval.report import render_json, render_text
from agent_eval.transcript import parse_session

from . import fixtures as fx


def evaluation_from(tmp_path, records):
    return evaluate(parse_session(fx.write_transcript(tmp_path / "s.jsonl", records)))


def test_text_report_leads_with_findings(tmp_path):
    records = []
    for i in range(3):
        records.append(fx.tool_use("Bash", {"command": "mvn verify"}, f"t{i}", uuid=f"a{i}"))
        records.append(fx.tool_result(f"t{i}", "BUILD FAILURE", is_error=True, uuid=f"r{i}"))

    text = render_text(evaluation_from(tmp_path, records))

    assert text.index("Findings") < text.index("Tools")
    assert "Possible loop" in text


def test_text_report_says_so_when_there_is_nothing_to_flag(tmp_path):
    text = render_text(evaluation_from(tmp_path, fx.simple_session()))

    assert "Nothing flagged" in text


def test_text_report_includes_cost_and_tool_lines(tmp_path):
    text = render_text(evaluation_from(tmp_path, fx.simple_session()))

    assert "$1.50" in text
    assert "Bash" in text and "Edit" in text


def test_text_report_omits_cost_section_when_unknown(tmp_path):
    text = render_text(evaluation_from(tmp_path, [fx.user_message("hi")]))

    assert "Cost" not in text


def test_json_report_round_trips(tmp_path):
    payload = json.loads(render_json(evaluation_from(tmp_path, fx.simple_session())))

    assert payload["session_id"] == fx.SESSION_ID
    assert payload["tools"]["total_calls"] == 2
    assert payload["tests"]["runs"] == 1
    assert payload["cost"]["total_usd"] == 1.5
    assert payload["findings"] == []


def test_json_report_includes_derived_values(tmp_path):
    payload = json.loads(render_json(evaluation_from(tmp_path, fx.simple_session())))

    assert "overall_failure_rate" in payload["tools"]
    assert "usd_per_100_lines" in payload["cost"]


def test_json_report_never_contains_prompt_text(tmp_path):
    payload = render_json(evaluation_from(tmp_path, [fx.user_message("classified material")]))

    assert "classified" not in payload
