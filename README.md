# agent-eval-mcp

Measure what a coding agent actually did. Tool efficiency, loops, test outcomes, permission posture
and cost, read out of session transcripts. Ships as a CLI and as an MCP server.

```bash
uv pip install -e .

agent-eval sessions                          # what sessions exist
agent-eval analyze path/to/session.jsonl     # what happened in one
agent-eval analyze path/to/session.jsonl --json --strict   # for CI
```

Companion to [agentic-harness-jvm](https://github.com/mrd5591/agentic-harness-jvm), which gates the
code an agent writes. This gates the agent's own behaviour.

---

## Why

You can read a diff and see what an agent produced. You cannot easily see that it spent forty
minutes running the same failing build, that it ended the session on a red test, or that it burned
nine dollars to change eleven lines. That information is in the transcript, and nobody reads
transcripts.

So: read them mechanically, and report only what a person should look at.

The findings list is the point. Everything else is supporting detail.

```
Session 4f2a...

Findings
  - Possible loop: Bash ran the same action 7 times (every attempt failed) - Bash:mvn -q verify
  - Session ended with a failing test run

Tools
  118 calls, 31 failed (26%)
    Bash: 74, 28 failed
    Edit: 31, 3 failed
    Read: 13

Tests
  9 runs, 6 failed, ended red

Cost
  $8.44 over 51m
  312 lines changed, $2.71 per 100 lines
```

## What it measures

| Signal | What it answers |
|---|---|
| Tool usage | Where did the effort go, and which tools kept failing? |
| Loops | Did it issue the same command or edit over and over? Read-only tools are excluded, because re-reading a file is navigation, not thrash. |
| Test events | Did the suite run, how often did it fail, and did the session end green? |
| Gates | What permission posture was it running under, and did a hook ever refuse a call? |
| Cost | Total spend, and spend per 100 lines changed. |
| Findings | The short list: loops, a red ending, a tool failure rate above 25% over a meaningful sample, hook blocks. |

Loop detection matches on a normalized call signature rather than on adjacency. An agent that
alternates between the same failing build and the same failing edit is looping just as surely as one
that repeats a single command, and the interleaving is exactly what makes it hard to see by eye.

## Use it from an agent, not just after the fact

The useful moment for this data is *inside* a session, when the question is "have I been going in
circles for the last twenty minutes." A CLI answers that afterwards, to a human. An MCP tool answers
it during, to the agent, which can then stop.

```jsonc
// .mcp.json
{
  "mcpServers": {
    "agent-eval": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/agent-eval-mcp", "python", "-m", "agent_eval.mcp_server"]
    }
  }
}
```

Four tools: `list_sessions`, `analyze_session`, `find_loops`, `cost_report`. Schemas and
descriptions are derived from the function signatures and docstrings, so what a model reads and what
it gets cannot drift apart.

Results come back as MCP structured content. A tool returning an object gives you that object; a
tool returning a list is wrapped as `{"result": [...]}`. A missing transcript or a root that is not
a directory raises `ToolError` naming the path, so a model that guessed wrong can correct itself
from the error alone rather than concluding that the corpus is empty. `~` is expanded.

`list_sessions` and `cost_report` walk top-level sessions, newest first. A session's delegated
subagent transcripts live under its `subagents/` directory and are that session's work, not
sessions of their own; pass `include_subagents=true` to see them as well.

## Privacy is a design constraint, not a setting

Transcripts contain prompts, file contents, paths and occasionally credentials that were pasted into
a terminal. A tool whose job is to analyse them should not become a second, less careful copy of
them.

So:

- **Prompt text is counted and discarded.** `human_turns` is a number. The text never enters a
  `Session` object, which is asserted by a test.
- **File contents never leave the parser.** An edit or write is identified by its path plus a
  fingerprint of the whole input, so identical edits still match as a loop while the edited text,
  which may be a whole file, is never carried into a finding, a report, or an MCP result. A test
  writes a fake credential three times and asserts it appears nowhere in any output.
- **A repeated shell command is echoed** in its loop finding, because the command is what a reviewer
  needs to see. That is the one place raw transcript text reaches the output.
- **Tool results are truncated to 400 characters** and kept only because exit states and test
  summaries have to be read out of them.
- **Nothing is written to a transcript directory.** The parser opens files read-only, and there is
  no code path in the package that writes there.
- **No real transcript is committed.** Every test fixture is hand-written synthetic JSONL, and the
  suite never reads a real transcript directory.

The output is counts and classifications. Run `--json` and grep it for anything you consider
sensitive; the tests do exactly that.

## Run against a real corpus

The analyser was run over a personal archive of **3,617 transcript files** (629 top-level sessions
plus the subagent transcripts they spawned) containing **211,062 tool calls**, in 34 seconds on a
laptop:

| | Top-level sessions | Including subagents |
|---|---|---|
| Transcripts parsed | 629 (555 with at least one tool call) | 3,617 (3,538) |
| Tool calls | 86,590, of which 3.2% returned an error | 211,062, 2.7% |
| Sessions with a detected loop | 116, or 18.4% | 280, or 7.7% |
| Sessions ending green / red on a recognized test run | 137 / 0 | 627 / 7 |
| Findings raised | 232 loops | 511 loops, 7 red endings, 5 high-failure-rate sessions |

Two of this tool's own defects were found by running it, not by reading it.

That run is also how the loop detector's own bug was found. On the first pass, a seven-hour session
reported five separate edits to one file as a loop, because the signature for an editing tool keyed
on the file path alone. Editing one file repeatedly is ordinary iterative work; only the *identical*
edit repeated is thrash. The signature now keys on the whole tool input, three regression tests pin
the behaviour, and the false positives disappeared while the genuine ones (the same shell command
re-run four times) stayed.

The second pass, a code review after publication, found that the fix had swung too far: keying on
the whole input put the whole input into the loop's signature, so a repeated `Write` reported its
file contents verbatim in the findings, contradicting the privacy claim below it. The signature is
now a path plus a fingerprint, and the test that would have caught it exists. The same review found
that the first published figure of 3,904 sessions counted every subagent transcript as a session of
its own, inflating the count about sixfold; the table above separates them.

Worth stating plainly: a tool that measures agent behaviour is only trustworthy if you have pointed
it at messy real data and fixed what it got wrong. One synthetic fixture would never have surfaced
either.

## Reading the numbers honestly

**Loop detection is a heuristic.** Three identical `git status` calls is not a problem. The
threshold is a parameter (`--loop-threshold`) because the right value depends on your work. What the
tool is actually good at is surfacing *failing* repetition, which is why `all_failed` is reported
separately.

**Loops are counted across the whole session, not within a time window.** The same command run four
times over seven hours reads the same as four times in five minutes, and the first is usually fine.
Adding a window is the obvious next improvement.

**Test detection parses the shell command.** Each simple command is tokenised, launcher prefixes
(`uv run`, `python -m`, `poetry run`, `npx`, `timeout`, environment assignments, `cd x &&`) are
peeled off, and the program is looked up in a table: pytest, tox, nox, jest, vitest, mocha, npm/yarn/
pnpm/bun/deno test, mvn test/verify/install, gradle test/check/build, go, cargo, dotnet, make, mix,
rake and swift test, rspec, phpunit, ctest. A commit message that mentions pytest does not count,
and neither does `ls tests/`. It will still miss a suite invoked through a custom script.

**"Ended green" means the last recognized test run passed.** It is not a statement about whether the
work was correct. A session with no test runs, or whose last run never returned a result because the
session was cut off, reports `null` rather than pretending.

**Human turns are the prompts a person typed.** The runtime also writes user-role records nobody
typed (command output, task notifications, compaction summaries, meta injections); those are
recognised by their flags or their opening tag and excluded, which matters on sessions that delegate
to many subagents.

**Cost comes from the runtime's own rollup**, not from re-pricing tokens. Dollars per 100 lines is a
crude ratio: a session that spends its budget on reading and reasoning will look expensive per line
and may have been the right call.

**None of this measures whether the change was any good.** It measures process. A session can be
clean on every metric here and still produce the wrong feature.

## Development

```bash
uv venv --python 3.11
uv pip install -e ".[dev]"
pytest                 #  tests, coverage gate at 90%
ruff check . && ruff format --check .   # lint and format gates
```

Built test-first. The coverage gate is in `pyproject.toml` and currently sits at 97%.

Tests are organized by module: transcript parsing, the analyses, rendering, the CLI, and the MCP
surface. The MCP tests exercise the server's own dispatch rather than a stdio transport, because
transport framing is the SDK's job and is tested there.

## License

MIT. See [LICENSE](LICENSE).
