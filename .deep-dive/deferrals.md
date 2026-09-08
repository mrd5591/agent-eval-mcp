# Deep-dive deferrals

Open findings this repo has chosen not to fix yet. Each entry carries a **class** (what leaving it
costs: `money` > `step` > `hours`) and an **effort** (`small` = one sitting and one gate, or
`own-pr`). Closed items move to `resolved.md`.

Ceiling on `## Open`: 20.

## Open

### 1. Heredoc bodies and multi-line quoted strings are parsed as commands
- **Severity:** Low · **Class:** `hours` · **Effort:** `own-pr` · **Surfaced:** 2026-09-08 · **Run count:** 1
- `src/agent_eval/metrics.py`, `_segments`. The parser splits a command on newlines and tokenises
  each line independently, so it has no notion of a body that is data rather than shell. Two
  consequences, both **measured** on the frozen 135,618-command corpus during the 2026-09-08 debt
  pass, one in each direction:
  - Prose inside a `<<'EOF'` heredoc can look like a command. A commit message containing the line
    `npm test 126/126.` is classified as a test run.
  - A `python -c "` string spanning several lines leaves the remaining lines unbalanced, so a real
    `npx vitest run` later on the same line is not seen.
- Two commands in 135,618, in opposite directions, so the aggregate effect is nil and the README
  states it rather than hiding it.
- **Defer reason:** genuinely `own-pr`. Doing this properly means tracking heredoc delimiters and
  quote state *across* lines, which is a real change to how a command is read and wants its own
  fixture matrix; the payoff is two commands in a corpus this size.
- **Next action:** a line-spanning tokeniser that knows `<<`/`<<-` delimiters and carries quote
  state, with fixtures for both shapes above.

### 2. Failure markers are English- and runner-specific
- **Severity:** Low · **Class:** `hours` · **Effort:** `small` · **Surfaced:** 2026-09-08 · **Run count:** 1
- `_FAILURE_MARKERS` recognises pytest, go, maven, jest/vitest and mocha output. A runner outside
  that set whose command exits 0 through a pipe still reads as green - the failure is simply not
  seen, exactly as before the 2026-09-08 pass, so this is a narrowed gap rather than a new one.
  Localised output would miss too.
- **Defer reason:** none of the defer criteria strictly applies; it is a scope boundary rather than
  a defect, and the markers are only ever used to turn a green red, so an unrecognised runner
  cannot be made *worse* by this. Cheap for a later pass to widen.
- **Next action:** add markers as runners are encountered; consider `rspec`, `phpunit`, `ctest`,
  `swift test` and `mix test`, each with a fixture.

## Accepted / won't-action

- **`-v` and `-n` are not treated as skip flags.** They look like query flags but `pytest -v` is a
  verbose run and `pytest -n 4` is a parallel one - two of the most common invocations there are.
  Suppressing them would trade a rare false positive for a frequent false negative. Do not re-file.
- **The corpus figures in the README cannot be reproduced exactly.** Transcripts rotate away over
  time, so the archive shrinks; the 2026-09-08 re-measurement is a dated snapshot and says so. The
  classifier's own effect is pinned separately against a frozen command set, which is the part that
  is actually reproducible.
