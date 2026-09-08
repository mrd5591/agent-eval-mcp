# Deep-dive resolved

Closed findings, newest first. This file is an audit trail: it is **not** loaded at the start of a
run. Grep it by symbol before re-opening anything, since a later pass can otherwise re-find and
re-file something already closed with evidence.

## Resolved

### 2026-09-08 - full pass (commit 08cb9d1)

**Fixed.**

- **The echoed command in a loop finding was uncapped** - `transcript.py` `ToolCall.signature`.
  A shell command is its own identity by design (the README discloses this), but nothing bounded
  it, so a heredoc writing a file put the whole body into `findings`, `render_text`, `render_json`
  and the MCP result. Capped at `COMMAND_SNIPPET_CHARS` (400), the bound results already had; a
  truncated command keeps a `_fingerprint` so two long commands sharing a prefix do not merge into
  one loop. This **bounds** inline-content exposure, it does not eliminate it - a short secret
  written inline is still echoed, which the README now says plainly.
- **`resolve_root("")` silently returned the real transcript archive** - `transcript.py`. The falsy
  check treated an empty string as "give me the default". Only `None` does now; empty and
  whitespace raise `NotADirectoryError`. Proven live by a reviewer: `cost_report(root="")` had
  scanned 631 real sessions.
- **`list_sessions(limit=-1)` returned `[]` instead of erroring** - `mcp_server.py`, via
  `max(limit, 0)`. A caller error read as an empty corpus, the exact failure `_root`'s own
  docstring says the error contract prevents. `limit=0` still returns nothing legitimately.
- **One `stat()` could kill a whole listing** - `transcript.py` `iter_session_files`. A transcript
  rotated or deleted mid-scan raised out of the generator, so scanning a live session directory
  crashed. Now skipped per file.
- **`ToolCall` had the default dataclass repr**, which printed the retained `tool_input` and
  `result_text`. Guarded. Also removed the `pragma: no cover` from `Session.__repr__`, which had
  exempted a privacy-load-bearing guard from the coverage gate.
- **The CI lint step was a false green on windows** - `build.yml`. Under the default shell (`pwsh`
  on windows runners) a block's exit status is its last command's, so a failing `ruff check .` was
  invisible on three of six matrix cells. Reproduced locally on pwsh 7.5.4. Pinned `shell: bash`.
- **The coverage floor could not notice a lost test file** - `pyproject.toml`. 90 against a real
  97.06 left enough slack that deleting all 297 lines of `test_review_regressions.py` still passed
  at 92.65%. Raised to 95: clean tree 97.52% (passes), the same deletion 91.98% (fails).
- **`_duration(90_000)` rendered "2m"** - `report.py`. `f"{1.5:.0f}"` is `2`, a 33% overstatement
  at the boundary. Seconds now hold to 120.
- **README inaccuracies** - the quickstart could not run as written (`uv pip install` with no
  venv); "the coverage gate ... currently sits at 97%" quoted measured coverage rather than the
  floor; the test count was stale; and the `result_text` bullet justified retention by a consumer
  that does not exist.

**Below the line** - real, but not worth a queue entry that a `money` item would always outrank:

- **The loop-threshold default `3` is duplicated in five places** (`metrics.py:172,362`,
  `cli.py:35`, `mcp_server.py:68,86`), and the minimum is additionally duplicated in prose in two
  MCP docstrings that are served to models as schema text. All five agree today; the harm is future
  drift only. `MIN_LOOP_THRESHOLD` is already centralized. Re-find cheaply with
  `grep -n "= 3" src/agent_eval/`.
- **The version is declared twice** - `pyproject.toml:3` and `__init__.py:3`, with no
  `dynamic = ["version"]` and no test pinning them together. `MCPServer(version=...)` advertises
  the `__init__` copy, so a release bumping only `pyproject.toml` would advertise the old one.
- **Public-repo hygiene** - no `[project.urls]`, no CI badge, no `SECURITY.md` or
  `CONTRIBUTING.md`, no `permissions:` block in the workflow (so it inherits the default
  `GITHUB_TOKEN` scope), actions pinned to floating tags (`checkout@v4`, `setup-uv@v5`) rather than
  SHAs, and no `concurrency` group. None has a reachable harm for a repo with no secrets and no
  deploy step.

**Corrections to agent analysis** - recorded so a later pass does not re-derive them:

- Three reviewers independently reported the inline-content leak as *contradicting* the README's
  "File contents never leave the parser". That framing is wrong: the README explicitly discloses
  the command echo two bullets later as "the one place raw transcript text reaches the output". The
  two bullets are each defensible and jointly contradictory only for a command carrying content
  inline. The real defects were the missing **cap** and the **underspecified disclosure**, both
  fixed - not an undisclosed privacy hole. Do not re-file this as a leak.
- One reviewer rated `resolve_root("")` Critical. Re-rated High on blast radius: `list_sessions`
  and `cost_report` return project paths, counts and costs - metadata, not prompt text or file
  contents. It is a silent wrong-scope against the documented contract, not a content leak.
- One reviewer proposed deleting `ToolCall.result_text` as dead retention (nothing reads it).
  Rejected as this pass's fix: it is the input to the real repair of the `suite_events` green/red
  defect (open item 1), and deleting it would foreclose the documented intent. The README was
  corrected to describe it as debt instead. Do not delete it without ruling on open item 1 first.
