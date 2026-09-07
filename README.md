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

## Privacy is a design constraint, not a setting

Transcripts contain prompts, file contents, paths and occasionally credentials that were pasted into
a terminal. A tool whose job is to analyse them should not become a second, less careful copy of
them.

So:

- **Prompt text is counted and discarded.** `human_turns` is a number. The text never enters a
  `Session` object, which is asserted by a test.
- **Tool results are truncated to 400 characters** and kept only because exit states and test
  summaries have to be read out of them.
- **Nothing is written to a transcript directory.** The parser opens files read-only, and there is
  no code path in the package that writes there.
- **No real transcript is committed.** Every test fixture is hand-written synthetic JSONL, and the
  suite never reads a real transcript directory.

The output is counts and classifications. Run `--json` and grep it for anything you consider
sensitive; the tests do exactly that.

## Run against a real corpus

The analyser was run over a personal archive of **3,904 session transcripts** containing **216,509
tool calls**, in 59 seconds on a laptop:

| | |
|---|---|
| Sessions parsed | 3,904 (3,625 with at least one tool call) |
| Tool calls | 216,509, of which 2.7% returned an error |
| Sessions with a detected loop | 290, or 8.0% |
| Sessions ending green / red on a recognized test run | 452 / 6 |
| Findings raised | 530 loops, 6 red endings, 4 high-failure-rate sessions |

That run is also how the loop detector's own bug was found. On the first pass, a seven-hour session
reported five separate edits to one file as a loop, because the signature for an editing tool keyed
on the file path alone. Editing one file repeatedly is ordinary iterative work; only the *identical*
edit repeated is thrash. The signature now keys on the whole tool input, three regression tests pin
the behaviour, and the false positives disappeared while the genuine ones (the same shell command
re-run four times) stayed.

Worth stating plainly: a tool that measures agent behaviour is only trustworthy if you have pointed
it at messy real data and fixed what it got wrong. One synthetic fixture would never have surfaced
that.

## Reading the numbers honestly

**Loop detection is a heuristic.** Three identical `git status` calls is not a problem. The
threshold is a parameter (`--loop-threshold`) because the right value depends on your work. What the
tool is actually good at is surfacing *failing* repetition, which is why `all_failed` is reported
separately.

**Loops are counted across the whole session, not within a time window.** The same command run four
times over seven hours reads the same as four times in five minutes, and the first is usually fine.
Adding a window is the obvious next improvement.

**Test detection is a regex over shell commands.** It knows pytest, npm/yarn/pnpm test, jest,
vitest, mvn test/verify, gradle test, go test, cargo test, dotnet test, rspec, phpunit and ctest,
anchored so that `ls tests/` does not count. It will miss a suite invoked through a custom script.

**"Ended green" means the last recognized test run passed.** It is not a statement about whether the
work was correct, and a session with no test runs reports `null` rather than pretending.

**Cost comes from the runtime's own rollup**, not from re-pricing tokens. Dollars per 100 lines is a
crude ratio: a session that spends its budget on reading and reasoning will look expensive per line
and may have been the right call.

**None of this measures whether the change was any good.** It measures process. A session can be
clean on every metric here and still produce the wrong feature.

## Development

```bash
uv venv --python 3.11
uv pip install -e ".[dev]"
pytest                 # 70 tests, coverage gate at 90%
```

Built test-first. The coverage gate is in `pyproject.toml` and currently sits at 97%.

Tests are organized by module: transcript parsing, the analyses, rendering, the CLI, and the MCP
surface. The MCP tests exercise the server's own dispatch rather than a stdio transport, because
transport framing is the SDK's job and is tested there.

## License

MIT. See [LICENSE](LICENSE).
