"""Parse a session transcript into something measurable. Read-only; retains no prompt text."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path.home() / ".claude" / "projects"

RESULT_SNIPPET_CHARS = 400

# Files under a session's subagents/ directory are that session's delegated work, not sessions of
# their own; journal.jsonl is a workflow log with no session records in it.
SUBAGENT_DIR = "subagents"
NON_SESSION_FILES = frozenset({"journal.jsonl"})

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
        """Identity for "the agent did this again".

        A shell command is its own identity. Every other input is reduced to the target path plus a
        fingerprint of the whole input, so two identical edits match while the edited text itself
        (which may be a whole file) never leaves the parser.
        """
        if self.command:
            return f"{self.name}:{_WHITESPACE.sub(' ', self.command).strip()}"
        digest = hashlib.sha256(
            json.dumps(self.tool_input, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:12]
        return f"{self.name}:{self.target}#{digest}"


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
    """A parsed transcript. Holds no prompt or file content."""

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


def resolve_root(root: str | Path | None) -> Path:
    """Expand and check a transcript root.

    Raises:
        NotADirectoryError: when the root does not exist or is not a directory.
    """
    resolved = Path(root).expanduser() if root else DEFAULT_ROOT
    if not resolved.is_dir():
        raise NotADirectoryError(f"Not a directory: {resolved}")
    return resolved


def iter_session_files(root: Path, include_subagents: bool = False) -> Iterator[Path]:
    """Yield session transcripts under a root, newest first. Missing roots yield nothing."""
    root = Path(root)
    if not root.is_dir():
        return
    candidates = []
    for path in root.rglob("*.jsonl"):
        if path.name in NON_SESSION_FILES:
            continue
        if not include_subagents and SUBAGENT_DIR in path.relative_to(root).parts:
            continue
        candidates.append((path.stat().st_mtime_ns, path))
    for _, path in sorted(candidates, key=lambda item: (-item[0], str(item[1]))):
        yield path


def load_session(path: str | Path) -> Session:
    """Parse one transcript by path.

    Raises:
        FileNotFoundError: when the path is not a file.
    """
    resolved = Path(path).expanduser()
    if not resolved.is_file():
        raise FileNotFoundError(f"Transcript not found: {resolved}")
    return parse_session(resolved)


def _iter_records(path: Path) -> Iterator[dict]:
    """Yield parsed JSON objects, skipping incomplete trailing lines."""
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


def _is_human_turn(record: dict) -> bool:
    """A user record that a person typed, as opposed to one the runtime injected.

    The runtime writes many user-role records with string content that no human wrote: meta
    injections, compaction summaries, and tagged payloads such as command output or task
    notifications. Those are recognisable by their flags or by opening with a tag.
    """
    if record.get("isMeta") or record.get("isCompactSummary"):
        return False
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return isinstance(content, str) and not content.lstrip().startswith("<")


def _as_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _cost_from(record: dict) -> Cost:
    usage = record.get("modelUsage")
    return Cost(
        total_usd=_as_float(record.get("totalCostUSD") or 0.0),
        lines_added=_as_int(record.get("totalLinesAdded") or 0),
        lines_removed=_as_int(record.get("totalLinesRemoved") or 0),
        duration_ms=_as_int(record.get("totalDuration") or 0),
        models=sorted(usage) if isinstance(usage, dict) else [],
    )


def read_cost(path: Path) -> Cost:
    """The cost rollup alone, without parsing every tool call. Same answer as a full parse."""
    cost = Cost()
    with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if '"cost-state"' not in line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and record.get("type") == "cost-state":
                cost = _cost_from(record)
    return cost


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
            session.cost = _cost_from(record)
            continue

        if record_type == "attachment":
            attachment = record.get("attachment")
            if isinstance(attachment, dict) and attachment.get("type") == "hook_blocked":
                session.hook_blocks += 1
            continue

        blocks = _content_blocks(record)

        if record_type == "user":
            if _is_human_turn(record):
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
