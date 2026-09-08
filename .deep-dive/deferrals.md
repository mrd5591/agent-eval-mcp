# Deep-dive deferrals

Open findings this repo has chosen not to fix yet. Each entry carries a **class** (what leaving it
costs: `money` > `step` > `hours`) and an **effort** (`small` = one sitting and one gate, or
`own-pr`). Closed items move to `resolved.md`.

Ceiling on `## Open`: 20.

## Open

### 1. `suite_events` decides green/red from the transport flag alone
- **Severity:** High · **Class:** `step` · **Effort:** `own-pr` · **Surfaced:** 2026-09-08 · **Run count:** 1
- `src/agent_eval/metrics.py:271-276`. `ended_green` is `not last.failed`, and `failed` comes only
  from the transcript's `is_error` flag (`transcript.py:263`). A suite that fails while the command
  exits 0 — `pytest -q || true`, `pytest 2>&1 | tee out`, a runner whose wrapper swallows the code —
  is recorded as **green**. Verified by the metrics reviewer against a synthetic transcript.
- Entangled with `ToolCall.result_text`, which is retained (400 chars) and read by nothing. The
  README previously justified that retention by this parsing; the justification is now stated
  honestly as a debt (README "Tool results are truncated…"), not as an existing feature.
- **Defer reason:** design decision. Parsing pass/fail out of tool output is a real feature with a
  per-runner surface, and the README's real-corpus table (137/0 green/red) was measured under
  current behaviour, so changing it re-bases a published figure.
- **Default if nobody rules:** read `result_text` for a failure signal (the field already exists for
  this, and the tool's central claim is that it measures what the agent actually did). **Apply at
  run 2.**
- **Next action:** decide, then either implement result-text parsing + re-measure the corpus table,
  or delete `result_text` and drop the claim.

### 2. `is_test_run` misses common real invocations
- **Severity:** Medium · **Class:** `step` · **Effort:** `small` · **Surfaced:** 2026-09-08 · **Run count:** 1
- `src/agent_eval/metrics.py:63,66` — two table entries are unreachable as written. `("uvx", "")`
  requires an empty second token, so `uvx pytest` is **not** a test run; it belongs in the one-word
  wrapper set. `("yarn", "run")` peels to `["test"]`, which is not a `_RUNNERS` key, so
  `yarn run test` is **not** a test run either (npm/pnpm work only because they lack a `run` entry).
- **Defer reason:** none of the three defer criteria strictly applies; it is deferred only because
  this pass had already changed seven files and each further edit to the classifier raises the
  regression surface of a published corpus measurement. **This is the first item a debt pass should
  take.**
- **Next action:** move `uvx` to `_ONE_WORD_WRAPPERS`, add `test` to the yarn peel target or drop
  the `("yarn","run")` entry; regression-test both shapes.

### 3. `is_test_run` matches a subcommand anywhere in the argument list
- **Severity:** Medium · **Class:** `step` · **Effort:** `own-pr` · **Surfaced:** 2026-09-08 · **Run count:** 1
- `src/agent_eval/metrics.py:254` — `any(arg in subcommands for arg in tokens[1:])` scans every
  argument, not the subcommand slot. False positives: `go run ./cmd/seed test`, `cargo run -- test`,
  `make -C test all`, `npm run build -- --env test`. Skip flags are ignored, so
  `mvn clean install -DskipTests` and `gradle build -x test` count as runs, and `pytest --version`
  / `--collect-only` count as runs. Separately, `_program`'s backslash normalisation
  (`metrics.py:239-241`) is dead for Windows paths because `shlex(posix=True)` has already eaten the
  backslashes — `C:\repo\.venv\Scripts\pytest.exe` does not classify, despite a Windows CI matrix.
- **Defer reason:** genuinely breaking-change scope. Fixing the slot semantics and the skip flags
  changes which sessions count as having run tests, which re-bases the README corpus table.
- **Next action:** positional subcommand matching + a skip-flag deny list + POSIX/Windows tokenising,
  landed together with a re-measured corpus table.

### 4. `.gitignore`'s `!tests/**/*.jsonl` negation carves a hole in the privacy control
- **Severity:** Medium · **Class:** `money` · **Effort:** `small` · **Surfaced:** 2026-09-08 · **Run count:** 1
- `.gitignore:15-16`. `*.jsonl` exists so a real transcript can never be committed; the negation
  re-enables commits under `tests/` — precisely where a contributor would drop a real transcript to
  debug against. Verified with `git check-ignore -v`: `tests/x.jsonl` and `tests/fixtures/x.jsonl`
  are **not** ignored. The negation currently buys nothing: there are zero `.jsonl` files in the
  tree, because every fixture is built in Python (`tests/fixtures.py`). Also unignored:
  `a.jsonl.gz`, `*.json`, and `.env`.
- **Defer reason:** design decision — the negation exists so a future synthetic fixture *can* be
  committed, and narrowing it is a call about contributor workflow, not a defect fix.
- **Default if nobody rules:** narrow to `!tests/fixtures/*.jsonl` and add `*.jsonl.gz` and `.env`,
  so committing a transcript anywhere else needs an explicit `git add -f`. **Apply at run 2.**
- **Next action:** apply the default, or delete the negation outright.

### 5. CI installs outside the lock, so the pinned versions are not what is tested
- **Severity:** Medium · **Class:** `hours` · **Effort:** `small` · **Surfaced:** 2026-09-08 · **Run count:** 1
- `.github/workflows/build.yml:28`. `uv pip install -e ".[dev]"` never reads `uv.lock` — uv's pip
  interface is lock-unaware. Verified: `uv sync --dry-run` reports it would *uninstall* pytest,
  pytest-cov, ruff and coverage, i.e. the dev tools CI installs are absent from the lock's
  resolution. So `ruff>=0.6` floats and a ruff release can turn CI red on an unchanged tree, while
  the lock file claims a reproducibility CI does not have.
- **Defer reason:** design decision. Floating dev tools catch upstream breakage early, which is
  arguably right for a tool that tracks the MCP ecosystem; pinning trades that for reproducibility.
  This pass already changed the workflow once (the shell fix) and a second change wants its own PR.
- **Default if nobody rules:** switch the install step to `uv sync --all-extras` and let Dependabot
  move the lock. **Apply at run 2.**
- **Next action:** decide pinned vs floating, then make the lock and CI agree either way.

### 6. Two user-facing findings are never exercised end to end
- **Severity:** Medium · **Class:** `hours` · **Effort:** `small` · **Surfaced:** 2026-09-08 · **Run count:** 1
- `src/agent_eval/metrics.py:351,357` — the "High tool failure rate" and "Hooks blocked N tool
  call(s)" strings are the tool's own output and no test reaches them through `evaluate`;
  `tests/test_metrics.py:149` calls `gate_events` directly. They are reachable in principle, so this
  is a coverage gap in the emission path, not dead code.
- **Defer reason:** none of the three criteria strictly applies; deferred for the same
  edits-per-pass reason as item 2. Cheap for a debt pass.
- **Next action:** two tests driving `evaluate` — 12 Bash calls with 5 errors, and a session with
  `hook_blocked` attachments — asserting the finding strings appear.

## Accepted / won't-action

_(none yet)_
