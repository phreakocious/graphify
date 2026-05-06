# CLI auto-build experiment + agent friction corpus

**Setup:** Test the hypothesis that moving graph-existence handling from
the SKILL.md (the first-call-hygiene paragraph) into the CLI itself
absorbs the SKILL's role. Three candidates, n=3 trials × 2 tasks:

- **baseline_navigator** (full SKILL with hygiene paragraph + stock CLI)
- **abl_no_first_call_hygiene** (no hygiene paragraph + stock CLI)
- **cli_auto_build** (no hygiene paragraph + CLI patched: load_graph
  auto-runs `graphify update <repo>` when graph.json is missing; the
  6 pre-load_graph existence-check exits in `__main__.py` removed)

This run also activated the new `agent_feedback` collection — one
extra API call per rollout asking the agent for friction reports.

## Quantitative result

| candidate | task 1 ttc | task 2 ttc | task 2 calls |
|---|---|---|---|
| baseline_navigator | 8,317 | 8,010 | 8.0 |
| cli_auto_build | 7,170 | 13,826 | 8.7 |
| abl_no_first_call_hygiene | 6,644 | **18,337** | **11.3** |

**Task 2: cli_auto_build saved 25% TTC and 23% wall-time vs the
no-hygiene baseline by absorbing the auto-update step into the CLI.**
The bimodal failure mode (agent doesn't realize they need to update;
floppers through read_file/grep) is partially neutralized.

**But not fully.** cli_auto_build is still +73% over full
baseline_navigator on task 2 — meaning the SKILL's hygiene paragraph
contributes value beyond just "run update first." The other guidance
in that paragraph (trust the `!stale` banner, "graph might be stale"
framing) likely also matters.

**Recommendation: ship CLI auto-build AND keep the SKILL hygiene
paragraph.** They're complementary, not duplicative.

## Agent feedback corpus (the bigger find)

The auto-feedback turn produced six recurring complaints across
cli_auto_build's 6 trials. These are real, specific UX issues an agent
would want fixed — most of them have nothing to do with auto-build:

### 1. Cursor doesn't persist across CLI invocations (3 trials cited)
The SKILL says "cursor persists for chains" but it persists only
within a single Python process. Agents who try
`graphify navigate "@X" && graphify navigate in` as separate bash
calls get "no cursor. focus with @<label> first."

> "The cheat-sheet implies 'cursor persists for chains' — it persists
> *within* a single process, not across shell invocations. Either
> document this clearly or persist cursor state to disk."

### 2. No one-shot `callers` / `wu` verb (3 trials)
Agents trying to answer "who calls X" expected a single verb. They got
the cursor issue (above), then fell back to `search`, then had to
filter manually.

> "Wanted a one-shot caller verb. For 'list direct callers of X' I
> expected something like `graphify callers "compute_score"` or
> `graphify navigate "@compute_score" --in` (single command, dump
> frontier)."

### 3. `search` conflates call-sites with imports/docstrings/tests (3 trials)
Without an AST-aware filter, every `search` hit needs eyeballing.

> "Hits [2][3][5][6][12] are bare `import` lines; [7][14] are
> docstring mentions (one of which literally says 'Does NOT call
> compute_score'); [9][17-24] are tests. A `--kind=callsite` or
> `--exclude-imports` filter would have collapsed 24 hits to 6."

### 4. `graphify update <subdir>` writes to `<subdir>/graphify-out/` (2 trials)
And then `graphify <verb>` from repo root looks at `./graphify-out/`,
fails, and the error doesn't say where the graph actually lives.

> "`graphify update src/` printed an absolute sandbox path for the
> output dir, but no hint that future invocations need to be run
> from `src/`. A one-line 'run subsequent commands from <dir> or pass
> --graph <path>' would have saved the failed `locate` call."

### 5. `graphify locate` returns absolute sandbox path, not workspace-relative (2 trials)
Agents see `/Volumes/.../runs/cli_auto_build_*/sandbox/repo/src/...` and
think it's a different file.

> "Forced me to fall back to `find` to confirm the real source
> location. A flag like `--workspace-only` (or excluding sandbox/run
> dirs by default) would've saved a call."

### 6. No way to check graph status without triggering a build (cli_auto_build-specific, 1 trial)
Now that auto-build is in place, agents can't peek at staleness without
paying the build cost.

> "A `graphify status` to check freshness without triggering a build
> would help me decide whether to defer graphify until after a quick
> read_file."

### Bonus: agents self-report habitual reach for the wrong verb
Multiple agents noted "for a single-symbol edit, `peek` would've been
strictly better than `locate` + `read_file`. I defaulted to `locate`
out of habit." Suggests the SKILL could have a stronger steer toward
peek.

## What this proves about the feedback-collection feature

The user has been doing this manually with two test repos. Automating
it produces:
- **Specific, actionable complaints** (quoted commands, named flags)
- **Repeated themes across trials** (cursor, search filters, paths)
- **Behavioral self-reports** (agent caught themselves using the wrong
  verb out of habit — that's the signal you can't get from the metrics
  alone)

The feature costs ~$0.01 per rollout. Across 50+ rollouts in this
session it's produced a corpus that's a backlog of well-scoped
graphify improvements. Worth the spend.

## Cost summary for this experiment

- 12 rollouts × ~$0.30 = ~$3.60
- 12 feedback turns × ~$0.01 = ~$0.12
- **Total ~$4 produced both the auto-build measurement AND the friction
  corpus.**

## Open question for the user

The auto-build patch is real and ready to ship to graphify (would need
to be ported to the navigator branch as a regular code change, not a
candidate override). Independently of that, the feedback corpus
suggests:

1. **Highest priority**: cursor persistence across CLI invocations
   (or at least clearer docs)
2. **Verb gap**: a one-shot `callers` / `wu --no-cursor` for "who
   calls X"
3. **`search` precision**: `--kind=call` to filter imports/docstrings
4. **Path display**: `graphify locate` should return CWD-relative paths
5. **`graphify status`**: read-only freshness check
6. **Auto-update from repo root**: when `graphify update` is invoked
   from a subdir, it should still build to repo-root `graphify-out/`
   (or at least warn about the path mismatch)

These are real graphify features to consider, with empirical justification
from agent feedback. Pick the ones that match your appetite.
