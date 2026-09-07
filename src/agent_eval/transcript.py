"""Parse a coding-agent session transcript into something measurable.

Design constraints, in order of importance:

1. **Read-only.** Nothing in this package writes to a transcript directory.
2. **No content retention.** Prompt text and file contents are counted, classified
   and thrown away. A tool that analyses transcripts must not become a second,
   less careful copy of them. Only tool results are kept, truncated, because the
   analyses need to read exit states out of them.
3. **Tolerant.** Session files are appended to by a live process. A truncated
   final line is normal and must not crash a read.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

# Tool result text is kept only up to this length. Enough to read an exit
# state or a test summary out of, short enough not to be a copy of the file.
RESULT_SNIPPET_CHARS = 400

_WHITESPACE = re.compile(r"\s+")


@dataclass
class ToolCall:
    """One tool invocation and, if it came back, its result."""

    tool_id: str
    name: str
    tool_input: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    result_text: str = ""
    resolved: bool = False
    failed: bool = False

    @property
    def command(self) -> str:
        """The shell command, for tools that run one. Empty otherwise."""
        value = self.tool_input.get("command", "")
        return value if isinstance(value, str) else ""

    @property
    def target(self) -> str:
        """The file a tool acted on, for tools that act on one. Empty otherwise."""
        value = self.tool_input.get("file_path", "")
        return value if isinstance(value, str) else ""

    @property
    def signature(self) -> str:
        """
        A stable identity for "the agent did this same thing again".

        Whitespace is collapsed so trivially reformatted commands compare equal;
        everything else is left alone, because a changed flag is a different
        attempt and should not be folded into a loop.

        Note what the signature deliberately does *not* do: for a file-editing
        tool it keys on the whole input rather than on the path. Editing one file
        five times is ordinary iterative work; only the identical edit repeated is
        thrash. Keying on the path alone reported every long session as a loop,
        which is how this was found.
        """
        if self.command:
            salient = self.command
        else:
            salient = json.dumps(self.tool_input, sort_keys=True)
        return f"{self.name}:{_WHITESPACE.sub(' ', salient).strip()}"


@dataclass
class Cost:
    """The session's cost rollup, as reported by the agent runtime."""

    total_usd: float = 0.0
    lines_added: int = 0
    lines_removed: int = 0
    duration_ms: int = 0
    models: list[str] = field(default_factory=list)


@dataclass
class Session:
    """A parsed transcript. Deliberately holds no prompt or file content."""

    path: Path
    session_id: str = ""
    cwd: str = ""
    git_branch: str = ""
    version: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    human_turns: int = 0
    permission_modes: list[str] = field(default_factory=list)
    hook_blocks: int = 0
    cost: Cost = field(default_factory=Cost)

    def __repr__(self) -> str:  # pragma: no cover - trivial, but load-bearing
        return (
            f"Session(session_id={self.session_id!r}, cwd={self.cwd!r}, "
            f"tool_calls={len(self.tool_calls)}, human_turns={self.human_turns})"
        )


def iter_session_files(root: Path) -> Iterator[Path]:
    """Yield every transcript under a root directory. Missing roots yield nothing."""
    root = Path(root)
    if not root.is_dir():
        return
    yield from sorted(root.rglob("*.jsonl"))


def _iter_records(path: Path) -> Iterator[dict]:
    """Yield parsed JSON objects, skipping lines that are not yet complete."""
    with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


def _content_blocks(record: dict) -> list[dict]:
    message = record.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def parse_session(path: Path) -> Session:
    """Read one transcript into a Session."""
    path = Path(path)
    session = Session(path=path)
    pending: dict[str, ToolCall] = {}

    for record in _iter_records(path):
        record_type = record.get("type")

        if not session.session_id:
            session.session_id = str(record.get("sessionId") or "")
        for attr, key in (("cwd", "cwd"), ("git_branch", "gitBranch"), ("version", "version")):
            if not getattr(session, attr) and record.get(key):
                setattr(session, attr, str(record[key]))

        if record_type == "permission-mode":
            mode = record.get("permissionMode")
            if isinstance(mode, str):
                session.permission_modes.append(mode)
            continue

        if record_type == "cost-state":
            usage = record.get("modelUsage")
            session.cost = Cost(
                total_usd=float(record.get("totalCostUSD") or 0.0),
                lines_added=int(record.get("totalLinesAdded") or 0),
                lines_removed=int(record.get("totalLinesRemoved") or 0),
                duration_ms=int(record.get("totalDuration") or 0),
                models=sorted(usage) if isinstance(usage, dict) else [],
            )
            continue

        if record_type == "attachment":
            attachment = record.get("attachment")
            if isinstance(attachment, dict) and attachment.get("type") == "hook_blocked":
                session.hook_blocks += 1
            continue

        blocks = _content_blocks(record)

        if record_type == "user":
            # A human turn carries a plain string; a tool-result turn carries blocks.
            # Counting the former and never storing it is the whole policy.
            if isinstance((record.get("message") or {}).get("content"), str):
                session.human_turns += 1
            for block in blocks:
                if block.get("type") != "tool_result":
                    continue
                call = pending.get(str(block.get("tool_use_id")))
                if call is None:
                    continue
                content = block.get("content")
                text = content if isinstance(content, str) else json.dumps(content)
                call.result_text = text[:RESULT_SNIPPET_CHARS]
                call.failed = bool(block.get("is_error"))
                call.resolved = True

        elif record_type == "assistant":
            for block in blocks:
                if block.get("type") != "tool_use":
                    continue
                tool_input = block.get("input")
                call = ToolCall(
                    tool_id=str(block.get("id") or ""),
                    name=str(block.get("name") or ""),
                    tool_input=tool_input if isinstance(tool_input, dict) else {},
                    timestamp=str(record.get("timestamp") or ""),
                )
                session.tool_calls.append(call)
                pending[call.tool_id] = call

    return session
