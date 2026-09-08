# Deep-dive resolved

Closed findings, newest first. This file is an audit trail: it is **not** loaded at the start of a
run. Grep it by symbol before re-opening anything, since a later pass can otherwise re-find and
re-file something already closed with evidence.

## Resolved

### 2026-09-08 - debt pass

All six open entries closed. The pass was unblocked by something the previous one had assumed away:
the README's "real corpus" is the local `~/.claude/projects` archive, and it is still there. Both
items that had been deferred as "re-bases a published figure" were therefore measurable rather than
theoretical, and the figure was re-based on evidence.

**Method, because it is the load-bearing part.** The corpus is *live* - sessions are written to it
while a sweep runs, and transcripts rotate away over time - so two aggregate sweeps cannot be
compared: the corpus moves underneath them. Instead the 135,618 distinct shell commands were frozen
to a file once, then classified by the old implementation and the new one. Same input, two
implementations, so every difference is attributable to the change.

#### 1. `suite_events` decided green/red from the transport flag alone - FIXED

Deferred at run 1 as a design decision. Ruled and implemented, and the corpus made the ruling
obvious: **85.2% of the test-run commands in it pipe through `2>&1 | head`** or similar. A shell
pipeline exits with the status of its *last* command, so `pytest ... | head -30` exits 0 however the
tests went. The transport flag was not merely unreliable here; it was wrong for the large majority
of real invocations, which is why the published table showed **zero** red top-level endings.

`looks_failed` now reads the run's own output for failure markers, and is used by `suite_events` for
both the failure count and the ending. It can only turn a green **red**, never the reverse: a run
the transport already called failed stays failed, so an unrecognised runner cannot launder a real
failure into a pass. Nine parametrised failing shapes and six passing ones are pinned, plus the
end-to-end case through a transcript.

This also retires the entanglement with `ToolCall.result_text`. The field was retained for exactly
this parsing and read by nothing; the README's privacy section stated that as a debt. It now states
it as a feature, truthfully.

Effect on the table: top-level green/red **143 / 0 → 137 / 6**; with subagents, **656 / 7 → 646 /
17**.

#### 2. `is_test_run` missed common real invocations - FIXED

`uvx` moved to `_ONE_WORD_WRAPPERS`: as `("uvx", "")` it required an empty second token, which no
tokeniser produces, so it never matched anything. `("yarn", "run")` dropped from
`_TWO_WORD_WRAPPERS`, where it peeled to `["test"]` - not a runner - so yarn was the one package
manager whose `run test` did not classify; npm and pnpm worked only because they lacked a `run`
entry and fell through to the old scan-everything match.

#### 3. `is_test_run` matched a subcommand anywhere in the argument list - FIXED

The one deferred as "genuinely breaking-change scope". Matching is now positional, in the subcommand
*slot*, with three refinements the naive version needed:

- **Multi-target tools** (`mvn`, `gradle`, `make`, `rake`) take a list of goals, all of which run,
  so any positional may be the test one. `mvn clean install` is a real test run; `go run ./cmd/seed
  test` is not.
- **Skip flags** (`-DskipTests`, `-Dmaven.test.skip`, `-x test`, `--collect-only`, `--help`) mean
  the suite is not run. Deliberately *not* including `-v` or `-n`, which look like query flags but
  are the two most common pytest invocations there are.
- **Value-taking flags**, per program and case-sensitive, because a shared set collides: `make -s`
  is silent and takes no value while `mvn -s` names a settings file. Without this, `make -C test
  all` read the directory named "test" as a target.

Windows paths now survive tokenising. POSIX `shlex` treats backslash as an escape, so
`C:\repo\.venv\Scripts\pytest.exe` arrived as `C:repo.venvScriptspytest.exe` and the
backslash-normalising code in `_program` could never fire - on a project with a Windows CI matrix.
The escape-free reading is *preferred* rather than forced: disabling the escape makes `\"` read as
an unbalanced quote, and the naive `split()` fallback loses `;` and `&&`, merging every segment into
one. Three real test runs regressed that way before the fallback chain was added. Escape-free
first, POSIX second, `split()` last.

**A separate bug fell out of this.** shlex's `punctuation_chars` mode glues adjacent operators, so
`...2>&1); npm test` arrives with `);` as a single token, which the separator set missed - merging
two commands into one segment and hiding the second program behind the first. While any argument
counted as a match, this produced the right answer for the wrong reason. `_is_break` now recognises
a token of nothing but operators.

**Measured effect** on the frozen 135,618 commands: 3,303 classified as test runs before, 3,300
after. Three corrections (`dotnet test --help` twice, one `pytest --co`), one command newly found
(the `);`-hidden `npm test`), and two known misclassifications in opposite directions from heredoc
and multi-line-quote blindness - re-filed as open entry 1 rather than papered over.

#### 4. `.gitignore` negation carved a hole in the privacy control - FIXED

`!tests/**/*.jsonl` narrowed to `!tests/fixtures/*.jsonl`, and `*.jsonl.gz`, `.env` and `.env.*`
added. Verified with `git check-ignore`: `tests/x.jsonl` and `tests/sub/deep/x.jsonl` are ignored,
`tests/fixtures/x.jsonl` is committable. Dropping a real transcript anywhere but the fixtures
directory now needs an explicit `git add -f`, which is a decision rather than an accident.

#### 5. CI installed outside the lock - FIXED

`uv pip install -e ".[dev]"` never read `uv.lock` - uv's pip interface is lock-unaware - so the lock
claimed a reproducibility CI did not have and `ruff>=0.16.6` floated. Both jobs now run
`uv sync --all-extras --frozen`, which fails rather than silently re-resolving when the lock and
`pyproject.toml` disagree. `uv sync` creates the environment itself, so the explicit `uv venv` step
is gone. Verified locally: the sync resolves, and the suite and ruff both run green from the
resulting environment. (The run-1 note that `uv sync` would *uninstall* the dev tools no longer
holds - Dependabot has since moved the lock.)

#### 6. Two user-facing findings never exercised end to end - FIXED

Both now driven through `evaluate`: twelve Bash calls with five errors for "High tool failure
rate: 42% of 12 calls", and a session with a `hook_blocked` attachment for "Hooks blocked 1 tool
call(s)". A clean session asserts neither string appears.

#### Verification

163 tests pass (50 new). Coverage **98.4%**, up from 97.5%, against a 95% floor; `metrics.py` alone
went 97% → 99%. `ruff check` and `ruff format --check` clean. The corpus sweeps and the frozen
command-set comparison were run locally, on Python 3.11 and 3.13.

Not run locally: the GitHub Actions workflow itself, so the `uv sync --frozen` change is verified by
running the same command by hand rather than by observing a CI run.

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
