# Executive summary — calibrated SKILL.md analysis (2026-05-06)

For the user who's coming back to this in the morning.

## Headline

The lap-21 calibrated `baseline_navigator/SKILL.md` is **well-tuned at
section granularity**. Three layers of ablation experiments confirm:

1. **vs. minimal baseline (sanity floor)**: -61% TTC on task 1, -63% on
   task 2. The lap-21 work paid off.
2. **Section-level ablation (5 sections × 2 tasks)**: every section
   earns its keep — none is droppable without measurable cost on at
   least one task. The maintainer's intuition matched empirical results
   5-for-5.
3. **Sub-section ablation within "Reading graphify output" (5 sub-rules ×
   2 tasks)**: the section's effect localizes to **edge confidence**
   and **omission counts** (each ~+100% TTC on task 2 when removed).
   Two other sub-rules (first-row + metadata) didn't trigger on this
   corpus — can't be certified as droppable yet.

## What can ship today

Nothing. The calibrated SKILL.md is the right thing to ship; ablation
confirmed that. The 70-line/1100-token footprint is pulling its weight.

## What needs more experiments

To certify the two dormant sub-rules (first-row, metadata) as droppable,
build tasks that trigger their fire conditions:

- **First-row + path-qualified disambig**: needs a fixture with the
  *same function name* defined in 2+ files. Agent has to lock onto
  the right one. The graphify codebase itself has 11 files with `main`
  — that's the natural test target if we can add a graphify-on-itself
  task.
- **Per-row metadata `!stale` marker**: needs a fixture where the
  graph is built, then a source file is touched to make graph mtime
  older. Agent must recognize and rebuild.

If both sub-rules turn out to be droppable on tasks that fire them,
trim from the SKILL.md and re-baseline. If either one matters on its
fire conditions, keep both (they cost ~30 tokens of cache_write each;
not worth the complexity).

## Where the bigger leverage lives

Section-granularity SKILL.md tuning has hit diminishing returns. The
remaining ~7-8k TTC per rollout is mostly **graphify CLI tool output**.
Each `graphify navigate` / `peek` / `update` call is producing the bulk
of the input tokens. To go further:

1. **Output density on `graphify navigate`**: how much info per row?
   The lap-21 work added per-row metadata; could we go denser without
   confusing agents?
2. **Auto-build / auto-update on stale**: the bimodal `+67% TTC`
   spike on `abl_no_first_call_hygiene` came from agents who didn't
   realize they needed `graphify update`. If the CLI auto-detected
   missing/stale graph and ran update for them, that failure mode
   disappears entirely. The SKILL.md hygiene paragraph could shrink
   to a single line.
3. **`graphify navigate <symbol> --read`**: collapse focus + body into
   one call. Agents do navigate-then-peek often; combining saves a
   round-trip.

These are all CLI-side changes — they belong in the graphify codebase
itself, with the meta-harness used as the empirical-validation harness.
The override mechanism in `harness/sandbox.py:build_candidate_graphify_source`
is built for exactly this: drop a modified `graphify/navigate.py` into
`agents/cli_X/overrides/graphify/navigate.py` and it gets used in the
candidate's sandbox.

## Where the data lives

| file | what's in it |
|---|---|
| `docs/SPEC.md` | Phase 1-4 vision (mostly stale now) |
| `docs/PLAN.md` | Phase 1 implementation plan |
| `docs/compare_run1_flake.md` | First baseline_minimal vs baseline_navigator (flake) |
| `docs/compare_run2_clean.md` | -61% TTC after install fix |
| `docs/ablation_run1_task1.md` | section ablation, task 1 |
| `docs/ablation_run2_task2.md` | section ablation, task 2 + cross-task |
| `docs/ablation_run3_subsection.md` | sub-section ablation in "Reading graphify output" |
| `runs/<candidate>__<task>__t<N>/metrics.json` | per-rollout structured data |
| `runs/<candidate>__<task>__t<N>/transcript.jsonl` | per-API-call transcript |
| `scripts/analyze_runs.py --all-tasks` | post-hoc analysis tool |

## Cost summary

- ~$25 of Anthropic API across all rollouts this session
- ~30 commits on `meta-harness-graphify` worktree branch
- 31 unit/integration tests (was 28; +3 for `file_contains_all` oracle)
- 12 candidates in `agents/` (2 baselines + 5 section ablations + 5 sub-section ablations)
- 2 tasks in `tasks/` (body-heavy + caller-trace, both Python src-layout)

## Recommended next step

Decide between:

- **A. Confirm the dormant sub-rules.** Build a same-name disambig task
  (graphify-on-itself, find the right `main`) and a stale-graph task.
  Re-run abl_read_no_firstrow + abl_read_no_metadata. If droppable on
  trigger-condition tasks, ship a smaller calibrated_v2. (~$3-5)

- **B. Pivot to CLI-side experiments.** The auto-build-on-missing-graph
  patch is the highest-leverage change suggested by the data
  (eliminates the +67% bimodal failure mode entirely). Requires
  modifying `graphify/navigate.py:load_graph` and overlaying via the
  candidate-overrides mechanism. (~$5-10 to validate)

- **C. Stop and ship.** Push the navigator branch upstream; mark the
  calibrated SKILL.md as the production target. Memory has been
  updated. The SUMMARY.md in this dir is the read-back-in artifact.

My recommendation is **B** — the data points specifically at the
auto-build-on-missing-graph case as the highest-impact unaddressed
issue, and the CLI-side override mechanism is exactly designed to
test it. But this is a real product decision and should be your call.
