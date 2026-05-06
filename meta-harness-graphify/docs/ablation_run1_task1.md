# Ablation run 1 — task 1 (compute_score median bug)

**Setup:** baseline_navigator (lap-21-calibrated SKILL.md) plus 5 ablations
(one section dropped each), n=3 trials each, claude-opus-4-7. Compared
against baseline_minimal as a sanity floor. Task: mylib `compute_score`
mean → median bug-fix on fixture_repo_001 (body-heavy, single function,
~5-line target).

## Results

| candidate | TTC mean | stdev | calls | delta vs ref | tool flavor |
|---|---|---|---|---|---|
| baseline_navigator (ref) | 8,317 | ±2,477 | 7.3 | (ref) | graphify-heavy: update=6, navigate=3, peek=1 |
| abl_only_navigate | 8,671 | ±2,127 | 7.3 | **+4.3%** | shifts to read_file=3, only navigate=2 |
| abl_no_decision_rule | 8,967 | ±1,821 | 7.7 | **+7.8%** | mixed: locate=2, peek=1, read_file=4 |
| abl_no_primer | 9,370 | ±2,134 | 8.0 | **+12.7%** | navigate=3, peek=2, read_file=2 |
| abl_no_output_conventions | 10,910 | ±1,868 | 8.7 | **+31.2%** | read_file=5, locate=2, update=5 |
| abl_no_first_call_hygiene | 11,267 | ±2,438 | 8.7 | **+35.5%** | read_file=5, update=5 (still!), locate=2 |
| baseline_minimal | 17,923 | ±286 | 7.3 | **+115.5%** | search=3, read_file=4 |

All ablations passed 3/3. baseline_minimal stdev is 286 (1.6%) because
the agent always uses read_file with that prior — deterministic flow.
Calibrated/ablated stdevs are ~2k (~25%) because the agent sometimes
uses peek/navigate (cheap) and sometimes read_file (more expensive),
producing bimodal TTC.

## Statistical caveat

Standard error on the mean = stdev/√3 ≈ 1,150 tokens. **Deltas under ~28%
are inside the noise band on n=3.** The two ablations that clear the
noise threshold confidently:

- **abl_no_first_call_hygiene: +35% TTC** (delta 2,950, std error ~1,400)
- **abl_no_output_conventions: +31% TTC** (delta 2,593, std error ~1,300)

The other three ablations (only_navigate +4%, no_decision_rule +8%,
no_primer +13%) are inside the noise band on this task. They could be
contributing nothing — or contributing real but smaller-than-noise
amounts that we'd see with n=10+ trials. Don't trim them based on this
data alone.

## What the tool-call distribution reveals

Removing the **first-call-hygiene** paragraph or the **output-reading
conventions** doesn't stop the agent from running `graphify update`
(both ablations still show update=5). But it does cause the agent to
**fall back to `read_file` more often** (5× vs 0× on baseline). That's
the mechanism: the agent runs graphify, then doesn't trust the output
enough to act on it without also reading the file directly. Hedging
costs ~3,000 tokens.

baseline_navigator with the full SKILL.md never did a `read_file` in
this run (across 3 trials). The graphify output was sufficient. That's
the calibrated-SKILL win in concrete form.

## Per-task limitation

This is a body-heavy single-file task. It under-tests verbs that exist
for other purposes: `wu` (where-used) for caller tracing, `path` for
reachability, `summarize` for cross-file aggregation. The verb table
ablation (`abl_only_navigate`, dropping all the non-navigate rows)
looks free here — but probably isn't on a caller-trace or
architecture-traversal task. Task 2 (caller trace, fixture_repo_002)
will tell us.

## Trim decision (preliminary)

**Don't trim anything yet** based on task-1-only data. The two
high-signal sections (first_call_hygiene, output_conventions) are
clearly earning their keep — keep them. The three ablations in the
noise band (only_navigate, no_decision_rule, no_primer) need
cross-validation against task 2 before we can confidently call them
"unused."

Consideration for the no-effect-on-this-task ablations: even if they
contribute zero on body-heavy tasks, they may still be load-bearing
on the call-graph traversal and architecture tasks that graphify
exists to support. Cross-task validation is the gating step.

## Costs

- 5 ablation candidates × 3 trials = 15 rollouts
- Plus 3 fresh baseline_navigator trials = 3 rollouts
- Plus 3 baseline_minimal trials (compare run 2, reused) = 0 new
- **Total: ~$5 of API time**

## Methodology notes

- All ablations run **sequentially in foreground** to avoid the race
  conditions caused by parallel bg compare runs writing to the same
  trial dirs (lesson from compare runs 3/4).
- transcript.jsonl now unlinked at start of each rollout (was appending
  across re-runs, inflating tool-call counts in `analyze_runs.py`).
- Anthropic SDK `max_retries=6` plus an outer `_create_with_overload_retry`
  layer (4× 60s) handle the API capacity events from earlier runs.
