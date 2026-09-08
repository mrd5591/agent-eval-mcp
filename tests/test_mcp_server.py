"""The MCP surface: tool advertisement, dispatch, and error shapes."""

from __future__ import annotations

import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from agent_eval import mcp_server

from . import fixtures as fx

EXPECTED_TOOLS = {"list_sessions", "analyze_session", "find_loops", "cost_report"}


def payload(result):
    """Read a tool result the way a client would.

    Structured content is the contract in MCP 2.x. A tool returning a list is
    wrapped as {"result": [...]}, so unwrap that one case and return the rest
    as-is.
    """
    structured = result.structured_content
    if isinstance(structured, dict) and set(structured) == {"result"}:
        return structured["result"]
    return structured


@pytest.mark.anyio
async def test_lists_the_advertised_tools():
    tools = await mcp_server.server.list_tools()

    assert {tool.name for tool in tools} == EXPECTED_TOOLS


@pytest.mark.anyio
async def test_every_tool_has_a_description_and_object_schema():
    for tool in await mcp_server.server.list_tools():
        assert tool.description, f"{tool.name} has no description"
        assert tool.input_schema["type"] == "object"


@pytest.mark.anyio
async def test_path_taking_tools_require_a_path():
    by_name = {tool.name: tool for tool in await mcp_server.server.list_tools()}

    for name in ("analyze_session", "find_loops"):
        assert by_name[name].input_schema["required"] == ["path"]


@pytest.mark.anyio
async def test_analyze_session_returns_the_evaluation(tmp_path):
    path = fx.write_transcript(tmp_path / "s.jsonl", fx.simple_session())

    result = await mcp_server.server.call_tool("analyze_session", {"path": str(path)})

    body = payload(result)
    assert result.is_error is False
    assert body["session_id"] == fx.SESSION_ID
    assert body["tools"]["total_calls"] == 2
    assert body["tests"]["runs"] == 1


@pytest.mark.anyio
async def test_analyze_session_also_returns_structured_content(tmp_path):
    path = fx.write_transcript(tmp_path / "s.jsonl", fx.simple_session())

    result = await mcp_server.server.call_tool("analyze_session", {"path": str(path)})

    assert result.structured_content["session_id"] == fx.SESSION_ID


@pytest.mark.anyio
async def test_analyze_session_never_returns_prompt_text(tmp_path):
    records = [fx.user_message("classified material"), *fx.simple_session()]
    path = fx.write_transcript(tmp_path / "s.jsonl", records)

    result = await mcp_server.server.call_tool("analyze_session", {"path": str(path)})

    assert "classified" not in json.dumps(payload(result))


@pytest.mark.anyio
async def test_list_sessions_reads_a_root(tmp_path):
    fx.write_transcript(tmp_path / "p" / "one.jsonl", fx.simple_session())

    result = await mcp_server.server.call_tool("list_sessions", {"root": str(tmp_path)})

    body = payload(result)
    assert len(body) == 1
    assert body[0]["session_id"] == fx.SESSION_ID


@pytest.mark.anyio
async def test_list_sessions_honours_the_limit(tmp_path):
    for name in ("a", "b", "c"):
        fx.write_transcript(tmp_path / "p" / f"{name}.jsonl", fx.simple_session())

    result = await mcp_server.server.call_tool("list_sessions", {"root": str(tmp_path), "limit": 2})

    assert len(payload(result)) == 2


@pytest.mark.anyio
async def test_find_loops_reports_repetition(tmp_path):
    records = []
    for i in range(3):
        records.append(fx.tool_use("Bash", {"command": "mvn verify"}, f"t{i}", uuid=f"a{i}"))
        records.append(fx.tool_result(f"t{i}", "fail", is_error=True, uuid=f"r{i}"))
    path = fx.write_transcript(tmp_path / "s.jsonl", records)

    result = await mcp_server.server.call_tool("find_loops", {"path": str(path), "threshold": 3})

    loops = payload(result)
    assert loops[0]["occurrences"] == 3
    assert loops[0]["all_failed"] is True


@pytest.mark.anyio
async def test_find_loops_returns_empty_for_a_clean_session(tmp_path):
    path = fx.write_transcript(tmp_path / "s.jsonl", fx.simple_session())

    result = await mcp_server.server.call_tool("find_loops", {"path": str(path)})

    assert payload(result) == []


@pytest.mark.anyio
async def test_cost_report_aggregates_a_root(tmp_path):
    fx.write_transcript(tmp_path / "p" / "one.jsonl", fx.simple_session())
    fx.write_transcript(tmp_path / "p" / "two.jsonl", fx.simple_session())

    result = await mcp_server.server.call_tool("cost_report", {"root": str(tmp_path)})

    body = payload(result)
    assert body["sessions"] == 2
    assert body["total_usd"] == pytest.approx(3.0)
    assert body["usd_per_100_lines"] == pytest.approx(1.0)


@pytest.mark.anyio
async def test_cost_report_on_an_empty_root(tmp_path):
    result = await mcp_server.server.call_tool("cost_report", {"root": str(tmp_path)})

    body = payload(result)
    assert body["sessions"] == 0
    assert body["usd_per_100_lines"] is None


@pytest.mark.anyio
async def test_unknown_tool_is_rejected():
    with pytest.raises(ToolError, match="Unknown tool"):
        await mcp_server.server.call_tool("nope", {})


@pytest.mark.anyio
async def test_missing_transcript_is_a_readable_tool_error():
    with pytest.raises(ToolError, match="Transcript not found"):
        await mcp_server.server.call_tool("analyze_session", {"path": "/does/not/exist.jsonl"})


@pytest.mark.anyio
async def test_missing_required_argument_is_rejected():
    with pytest.raises(ToolError):
        await mcp_server.server.call_tool("analyze_session", {})
