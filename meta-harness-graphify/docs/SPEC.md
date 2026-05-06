# Domain Spec: Meta-Harness for graphify navigator

> **Status:** Drafted autonomously by Claude on 2026-05-05 while user was asleep. Decisions made in the absence of explicit answers are tagged `[AUTO]` with the rationale. User should review and override anything that doesn't match intent.

## Glossary

- **Eval agent** — Claude session that solves one task using the candidate harness. The thing whose tokens we're trying to minimize.
- **Proposer** — Claude session that *writes* candidate harnesses by reading the frontier + logs + skill prior. Runs once per iteration.
- **Candidate / candidate harness** — `(graphify overrides, SKILL.md)` pair. The unit of search.
- **Baseline** — current `navigator` branch state of graphify + existing skill text. Anchor for "did we improve."
- **Search set** — tasks the search loop optimizes against.
- **Held-out set** — tasks reserved for final evaluation, never seen by the proposer.
- **Frontier** — top-N candidates by score (not strictly Pareto unless we add a second metric).
- **Rollout** — one (candidate, task, trial) execution of the eval agent.

## Domain Summary

**Task.** Adapt the Meta-Harness framework (Stanford Iris Lab, 2026) to optimize the *navigator harness* used by Claude Code when it works inside a code repository. The navigator harness is the joint of:

- **Interface:** the `graphify` CLI's user-facing output — hint formatting, banner/warning triggers, ranking/truncation policies, "next direction" suggestions, ambiguity surfacing, dotted-suffix matching, freshness checks.
- **Instructions:** the `SKILL.md` / wrapper text that conditions Claude on *when* to reach for graphify versus Read/Grep, *how* to read its output, and *which* knobs to use first.

**Unit of evaluation.** One coding task = one (repo snapshot, problem statement, success oracle) tuple. The agent (Claude) is given the problem in a fresh sandboxed working copy with the candidate harness installed, and either resolves the task (oracle passes) or doesn't (oracle fails / budget exceeded).

**Fixed components.**
- Base model for the eval agent: `claude-opus-4-7` `[AUTO: latest available, matches user's current model]`
- Graphify's *graph extraction layer* (extractors, AST parsing, cache machinery) — mutating these breaks correctness, not friction, so they are out of scope.
- Tool surface available to the eval agent: `Read`, `Grep`, `Glob`, `Bash`, `Edit`, `Write`, `graphify` (via Bash). Same tools every candidate sees.
- The eval task corpus and oracles, once frozen for a search run.

**Allowed to change.**
- Anything under `graphify/cli/`, `graphify/navigate.py`, `graphify/resolve.py`, `graphify/reporters/` (i.e., user-facing output assembly).
- Configurable thresholds in graphify (truncation cutoffs, ranking weights, hint trigger conditions, dominant-match cutoff, freshness banner trigger, dotted-suffix policy).
- The `SKILL.md` prior given to the eval agent.

**Optimization budget.** `[AUTO: target ~$50 per *MVP* iteration (8 tasks × 1 trial × 4 candidates ≈ 32 rollouts ≈ 1.6M tokens at ~$30/M effective with caching). Full iterations (12 tasks × 2 trials × 6 candidates ≈ 144 rollouts ≈ $200) require explicit user approval]` — TB2 reference is ~$500/iter on Opus 4.6 × 89 tasks × 2 trials. Graphify tasks are smaller per-rollout but we have less convergence signal. Default: 8-12 task `hard` subset, 1-2 trials per (candidate, task), 4-6 candidates per iteration.

## Harness and Search Plan

**Candidate harness shape.** A candidate is a directory:

```
candidates/<candidate_id>/
  overrides/                  # files that override graphify's source tree (relative paths preserved)
    cli/navigate.py
    resolve.py
    ...
  SKILL.md                    # text the eval agent sees as a system-prompt addendum
  metadata.json               # parent_id, generation, proposer notes, search-set scores
```

At eval time, the harness layer:
1. Spawns a fresh git worktree of the target repo
2. Installs graphify with `overrides/` applied (rsync over a clean checkout into a per-candidate venv)
3. Launches the eval agent (Claude API) with `SKILL.md` as a system-prompt suffix
4. Runs the agent until the task is solved or the budget is hit
5. Captures: agent transcript, tool calls, token counts, time, oracle result

**Baseline.** `agents/baseline_navigator/` = the current `navigator` branch state of graphify + the existing skill text (or empty SKILL.md if there is none yet). This is the reference candidate every iteration's frontier compares against.

**Reusable helpers (the framework provides):**
- `harness/sandbox.py` — per-eval git-worktree + venv setup/teardown
- `harness/eval_runner.py` — drives one (candidate, task, trial) rollout via the Anthropic SDK with prompt caching
- `harness/oracles.py` — task success oracles (pytest pass, file-content match, regex match on agent output)
- `harness/metrics.py` — tokens-to-completion accounting (input, output, cache-read, cache-write split)
- `harness/proposer.py` — invokes Claude Code as the proposer agent with the meta-harness skill loaded

**Search loop.** Per iteration:
1. Load current frontier (best candidates by search-set score).
2. Proposer reads frontier candidates + recent evaluation logs + skill prior, and writes a new candidate to `candidates/<new_id>/`.
3. Evaluator runs new candidate on the search-set tasks (with `--trials N`).
4. Score is aggregated; frontier is updated; logs are appended.

**First search loop.** *MVP smoke test:* single proposer, single evaluator, sequential, 1 task × 1 candidate × 1 trial — to prove plumbing. *First real iteration:* sequential proposer, evaluator runs at concurrency 2-3 (because 144 sequential rollouts at ≤30 min each would take days). `[AUTO: parallelism is required for real iterations but limited to a low number until rate-limit behavior is characterized.]`

**Out of scope for first pass.**
- Mutating the graph extraction layer.
- Mutating tool definitions Claude sees beyond `SKILL.md` (e.g., MCP server modifications).
- Mutating the base model.
- Optimizing for non-coding tasks.

## Evaluation Plan

**Search set (used by the search loop).** `[AUTO: 8-12 tasks, drawn from these repos:]`
- `exotic-geometry-framework` — same target as the EGF Round-2 head-to-head; Python, math/ML domain.
- `graphify-on-itself` — the agent navigates and edits graphify's own code.
- One TypeScript repo TBD `[AUTO: deferred to task-authoring step; navigator's TS support is recent and important to cover]`.

**Held-out test set.** `[AUTO: 4-6 tasks held out, drawn from a *different* repo than the search set.]` Held-out evaluation runs only after the search loop terminates. Tasks are not seen by the proposer until eval. Proposer never reads held-out task files.

**Headline metric.** `tokens_to_completion`, conditional on task pass. Lower wins. Failed tasks get a 3× per-task budget cap penalty so candidates can't game the metric by giving up early.

`tokens_to_completion = sum(input + output + cache_write tokens)` — cache-read tokens are reported separately because they are nearly free and don't reflect harness friction.

**Secondary diagnostics (logged, not optimized):**
- `productive_tokens` (Edit/Write/code-reasoning) vs `overhead_tokens` (Read/Grep/graphify-output) split — proxy for "deterministic work vs. needless context"
- `n_graphify_calls`, `n_read_calls`, `n_grep_calls` per task
- Whether the chosen "next direction" hint matched the agent's actual next call (proxy for hint value)
- Wall-clock seconds, API call count
- Agent confusion signals: re-running the same query with different args, abandoned exploration paths

**Per-candidate evaluation.** `min(2, n_trials)` rollouts per task per candidate `[AUTO: 1 trial in MVP smoke test, 2 in real iterations]`. Average tokens, take task-pass median.

**Per-task budget cap.** `[AUTO: 100k tokens or 30 min wall-clock, whichever first; failed tasks penalized at 300k tokens.]` Adjustable in config.

**Noise.** Single-shot Claude task runs are noisy. The 2-trial average mitigates but doesn't eliminate. Trust differences only above a threshold `[AUTO: 5% relative]`.

**Leakage risk.**
- Held-out tasks must come from a repo not used in the search set, *and* must not share code/symbols with search-set tasks.
- Proposer must not read held-out task definitions or their target files.
- The eval agent reads its target repo as part of doing the task — that's expected; only the proposer is gated.

## Experience and Logging

**Per-rollout artifacts (`logs/<run>/<candidate>/<task>/<trial>/`):**
- `transcript.jsonl` — every API request/response, with full message content
- `tool_calls.jsonl` — extracted tool-call events with timing
- `metrics.json` — tokens-to-completion + diagnostic split, oracle result, runtime
- `agent_diff.patch` — `git diff` of agent's edits in the sandbox
- `graphify_calls.jsonl` — every `graphify` invocation + its stdout/stderr (high-signal debugging artifact)
- `final_state.txt` — what the sandbox looked like at end-of-run

**Per-candidate artifacts (`candidates/<id>/`):**
- `metadata.json` — parent, generation, proposer notes, search-set aggregate score, frontier rank
- `overrides/` — the patch applied vs. baseline graphify
- `SKILL.md` — the candidate's skill text
- `proposer_notes.md` — what the proposer said it was trying to do (free-form, for human review)

**Per-iteration aggregates (`logs/iteration_<n>/`):**
- `summary.json` — frontier before/after, candidates evaluated, tokens spent, regressions
- `frontier.json` — top-N candidates by aggregate score (single-metric "frontier"; if we add a second optimized metric later, this becomes a real Pareto set)
- `regressions.md` — auto-flagged tasks where the new frontier is worse than baseline

**CLI for querying run history.** `[AUTO: yes, build a tiny one — entry point `mhg` (= meta-harness-graphify)]`
```
mhg ls candidates                        # list candidates with score, parent
mhg show candidate <id>                  # tree of overrides, SKILL.md preview
mhg show task <task-id>                  # all rollouts ever, scored
mhg compare <id-a> <id-b>                # side-by-side metrics + diff of overrides
mhg frontier                             # current best-N candidates by score
mhg regression                           # tasks where frontier regressed
```

This is small (Python click app). Pays for itself by iteration 2.

**Offline experience to seed the proposer:**
- The `navigate_v3_backlog.md` memory contents — captures all the user's hard-won design lessons.
- The EGF Round-2 head-to-head benchmark transcript (if recoverable) — concrete signal on what graphify currently does well and badly.
- Recent commits on the `navigator` branch — encode reasoning behind each lap-20a/b/c/d change.

These get loaded into the proposer's `SKILL.md` (the meta-harness-graphify skill, not the eval agent's skill).

## Open Questions and Unknowns

- **Eval agent driver.** Use the Anthropic SDK directly with prompt caching, or use the `claude-code-sdk` (if available) to mimic Claude Code's exact tool-loop behavior? Latter is closer to the deployment surface; former is simpler. `[AUTO: start with claude-code-sdk if present, fall back to direct Anthropic SDK; verify in scaffold step]`
- **Task corpus authoring.** The seed tasks need oracles that don't reward hacking. For "refactor X" tasks, oracle = test suite passes + diff doesn't touch unrelated files. For "implement Y" tasks, oracle = specific test passes. Authoring takes care.
- **Search budget vs. quality.** A 12-task × 2-trial × 6-candidate iteration is ~144 rollouts. At ~50k tokens/rollout average, that's ~7M tokens/iteration. Manageable but not free.
- **Concurrency.** TB2 runs at concurrency 50 against Anthropic's API; we'll start at concurrency 1-3 to avoid rate limits and keep early debugging tractable.
- **Caching strategy.** The candidate's `SKILL.md` is in the system prompt suffix — should be a cache breakpoint. Per-task content varies, so cache the harness prelude and reset per task.
- **`.venv` editable-install pitfall.** Per existing memory: graphify's editable install resolves to the original path even from a worktree. The eval-time installation path needs to be a *real* install of the candidate's overlaid graphify into a per-candidate venv, not editable.
- **Held-out repo selection.** Need to pick before the search starts to avoid post-hoc cherry-picking. `[AUTO: deferred to task-authoring step; document the choice when made.]`
- **Multi-language coverage.** Navigator just shipped TS-Claude-driven changes; held-out should include at least one TS/JS task to detect language regressions.
- **Stop conditions.** Iteration count, wall-clock budget, dollar budget, frontier convergence — pick at least one, default to candidate count `[AUTO: default `--iterations 1` for MVP smoke; user sets real budget later]`.

## Phasing

The SPEC describes the full system. The implementation PLAN scopes only **Phase 1**.

**Phase 1 — MVP "loop works end-to-end" (this PR's scope).**
- Project scaffolding (`pyproject.toml`, dirs, dependencies)
- `harness/sandbox.py` — per-eval git-worktree + venv setup/teardown for one repo
- `harness/eval_runner.py` — drive one rollout via Anthropic SDK with prompt caching
- `harness/oracles.py` — at least pytest-pass + file-content-match
- `harness/metrics.py` — tokens-to-completion accounting
- `agents/baseline_navigator/` — one baseline candidate
- 1 hand-written task in `tasks/` (graphify-on-self) with a passing oracle
- A `meta_harness.py` entry point that runs only `--smoke` (one rollout, one candidate, one task) and reports metrics
- No proposer, no search loop, no multi-task yet
- Goal: prove the plumbing. End state = baseline harness solves the seed task and we have metrics.

**Phase 2 — Search loop.**
- `harness/proposer.py` — Claude Code as proposer, with meta-harness-graphify SKILL.md prior
- Multi-task evaluator with concurrency
- Candidate registry, frontier, iteration logging
- 4-6 candidates per iteration

**Phase 3 — Corpus + CLI + held-out.**
- Full search-set (8-12 tasks across EGF + graphify-self + TS repo)
- Held-out set + leakage hygiene
- `mhg` CLI for querying run history
- Regression detection

**Phase 4 — Cost/quality optimization.**
- Concurrency tuning
- Cache strategy refinement
- Trial count / noise calibration
- Stop-condition heuristics

## Decisions Index (Autonomous)

| Decision | Choice | Rationale |
|---|---|---|
| Search scope | Joint (interface + instructions) | User said "instructions and interface" |
| Headline metric | Absolute tokens-to-completion, success-gated, failure-penalized | Robust; ratio falls out as diagnostic |
| Eval agent model | `claude-opus-4-7` | Latest; matches deployment |
| Proposer model | `claude-opus-4-7` | Same; consistent reasoning behind candidates |
| Baseline | Current `navigator` branch | Defensible reference; user's most recent ship |
| Search-set repos | EGF + graphify-self + 1 TS repo | Mix of domains, both languages graphify supports |
| Held-out | Different repo, decided pre-search | Standard leakage hygiene |
| Task budget | 100k tokens or 30 min, fail = 300k penalty | Catches flailing without strangling hard tasks |
| Trials | 2 per (candidate, task) in iter; 1 in smoke | Halves noise without doubling cost |
| Iteration size | 6 candidates × 12 tasks × 2 trials | ~144 rollouts ≈ tractable per iter |
| Parallelism | 1 in MVP, scale later | Debug the loop before optimizing it |
| CLI for run history | Yes, small click app | Pays back by iter 2 |
