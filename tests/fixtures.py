"""Synthetic transcripts.

Every fixture here is written by hand. No real session data is committed to this
repository, and the test suite never reads from a real transcript directory. That
is a deliberate constraint: transcripts contain prompts, file contents and paths,
and a tool whose job is to analyse them should not be the reason they leak.
"""

from __future__ import annotations

import json
from pathlib import Path

SESSION_ID = "11111111-2222-3333-4444-555555555555"
CWD = "/repo/example"


def _base(record_type: str, **kwargs: object) -> dict:
    record = {
        "type": record_type,
        "sessionId": SESSION_ID,
        "cwd": CWD,
        "gitBranch": "main",
        "version": "2.1.0",
    }
    record.update(kwargs)
    return record


def user_message(text: str, uuid: str = "u1", timestamp: str = "2026-01-01T00:00:00.000Z") -> dict:
    """A typed human turn."""
    return _base(
        "user",
        uuid=uuid,
        timestamp=timestamp,
        promptSource="typed",
        origin={"kind": "human"},
        message={"role": "user", "content": text},
    )


def tool_use(
    name: str,
    tool_input: dict,
    tool_id: str,
    uuid: str = "a1",
    timestamp: str = "2026-01-01T00:00:01.000Z",
) -> dict:
    """An assistant turn that calls one tool."""
    return _base(
        "assistant",
        uuid=uuid,
        timestamp=timestamp,
        message={
            "role": "assistant",
            "model": "claude-opus-5",
            "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}],
        },
    )


def tool_result(
    tool_id: str,
    content: str,
    is_error: bool = False,
    uuid: str = "r1",
    timestamp: str = "2026-01-01T00:00:02.000Z",
) -> dict:
    """The result turn carrying a tool's output."""
    block: dict = {"type": "tool_result", "tool_use_id": tool_id, "content": content}
    if is_error:
        block["is_error"] = True
    return _base(
        "user",
        uuid=uuid,
        timestamp=timestamp,
        message={"role": "user", "content": [block]},
    )


def cost_state(
    total_cost: float = 1.5,
    lines_added: int = 120,
    lines_removed: int = 30,
    duration_ms: int = 600_000,
) -> dict:
    """The rollup record Claude Code writes for a session."""
    return _base(
        "cost-state",
        totalCostUSD=total_cost,
        totalLinesAdded=lines_added,
        totalLinesRemoved=lines_removed,
        totalDuration=duration_ms,
        totalAPIDuration=duration_ms // 2,
        modelUsage={
            "claude-opus-5": {
                "inputTokens": 1000,
                "outputTokens": 500,
                "costUSD": total_cost,
            }
        },
    )


def permission_mode(mode: str = "auto") -> dict:
    """The session's permission posture."""
    return _base("permission-mode", permissionMode=mode)


def hook_block(hook_name: str = "PreToolUse:Bash", reason: str = "blocked by policy") -> dict:
    """A hook that refused a tool call."""
    return _base(
        "attachment",
        uuid="h1",
        timestamp="2026-01-01T00:00:03.000Z",
        attachment={
            "type": "hook_blocked",
            "hookName": hook_name,
            "hookEvent": "PreToolUse",
            "content": reason,
        },
    )


def write_transcript(path: Path, records: list[dict]) -> Path:
    """Write records as JSONL, the way a session file is stored."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    return path


def simple_session() -> list[dict]:
    """One prompt, one successful edit, one passing test run, and a cost rollup."""
    return [
        permission_mode("auto"),
        user_message("add the thing"),
        tool_use("Edit", {"file_path": "/repo/example/a.py"}, "t1"),
        tool_result("t1", "edited"),
        tool_use("Bash", {"command": "pytest -q"}, "t2", uuid="a2"),
        tool_result("t2", "12 passed in 1.2s", uuid="r2"),
        cost_state(),
    ]
