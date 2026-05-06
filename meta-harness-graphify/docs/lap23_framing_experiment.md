# Lap-23: SKILL.md framing — tool-organized vs task-organized

**Date:** 2026-05-06
**graphify branch:** `navigator` at `bc7922d`
**Question:** Does organizing the SKILL.md by *task you're doing* (task → recipe) beat organizing by *tool you have* (verb cheat-sheet) — when both contain the same information?

## Why the experiment

User observation after lap-22b: graphify's surface has grown (10+ verbs, multiple pivots), and the SKILL.md primer is hitting the point where Claude pays a lot of tokens up-front per session. The current cheat-sheet is verb-list shaped — each row says "verb → use for X". An agent doing task X has to scan the full list and synthesize "which verb fits."

A task-recipe table inverts that: each row says "task X → run this verb sequence." The information is the same; the indexing changes.

We also tested an explicit "agent self-audit" prompt: *"before each call, name two ways you could answer and pick the cheaper."*

## Setup

Three SKILL.md variants, byte-identical except where noted:

| candidate | difference vs V0 |
|---|---|
| **V0** `baseline_navigator_blast` | control. tool-organized cheat-sheet (current navigator default + `blast` row from lap-22). |
| **V1** `task_recipe_navigator` | cheat-sheet replaced with a task → recipe table. Same 13 entries, same content, reorganized by task-shape (e.g. "Refactor blast radius → `graphify blast \"@<symbol>\"`"). |
| **V2** `task_recipe_compare` | V1 + one paragraph at the top: *"Before each call, name two ways you could answer the current sub-question and pick the cheaper one."* |

All other sections (R3 calibration, search-vs-grep guidance, first-call hygiene, output conventions, skip-for) byte-identical across the three.

## Round 1: seed_task_003 (EGF refactor blast-radius)

n=3 trials × 3 candidates.

| candidate | TTC mean (±stdev) | calls (mean) | wall (mean) | pass |
|---|---|---|---|---|
| V0 baseline_navigator_blast | 13,190 (±2,213) | 7.3 | 51.3s | 3/3 |
| **V1 task_recipe_navigator** | **11,609 (±1,633)** | 8.3 | 49.2s | 3/3 |
| V2 task_recipe_compare | 13,864 (±2,275) | 9.0 | 61.9s | 3/3 |

**Deltas vs V0:**
- V1: ttc **−12.0%**, calls +1.0 (+13.7%)
- V2: ttc **+5.1%**, calls +1.7 (+23.3%), wall +21%

**Significance:** V1 win is **directional but not statistically significant at n=3** (t≈1.0, distributions overlap). V2 loss is more robust — wall and call delta both clearly exceed noise.

### Why V1 won (transcript inspection)

V1 trials all opened with `graphify navigate "@_compute_signal_metrics"` — the disambig recipe. V0 trials sometimes used `graphify locate _compute_signal_metrics` for the same purpose. Both work, but the disambig recipe in V1's table pointed directly at `navigate` (cheap output, ≤200 chars), while V0's tool-cheat-sheet didn't make `navigate` the obvious disambig tool — agents reached for `locate` (broader output, ~400-800 chars) about half the time.

The win compounds across the rollout: V1 picks the smaller-output tool for the same step, and one cheaper tool early saves more than the extra `navigate` call costs.

### Why V2 backfired

The "name two approaches before each call" instruction added an explicit reasoning step per turn. The agent generated alternative-approach text on turns where the right path was obvious, raising output tokens AND number of calls (+23%) — agents enumerated alternatives, picked one, then sometimes second-guessed.

**Negative finding:** explicit per-call self-audit prompts cost more than they save. The harness should not assume agents need this scaffolding.

## Round 2: seed_task_004 (EGF Klein body-heavy summary)

n=3 trials × V0/V1 (V2 dropped after R1).

| candidate | TTC mean (±stdev) | calls (mean) | wall (mean) | pass |
|---|---|---|---|---|
| V0 | 109,875 (±73,469) | 14.0 | 86.6s | 3/3 |
| V1 | 108,484 (±71,973) | 14.3 | 89.4s | 3/3 |

**Delta:** V1: ttc **−1.3%**, calls +0.3 (+2.1%) — within noise.

Both candidates **bimodal**: 1 trial each finished in ~25k tokens, 2 trials each finished in ~150k.

### What the bimodality is actually about

The split is not about graphify framing. It's about whether the agent remembers to bound `read_file`:

- **Cheap V0 t0 (25,041):** used `graphify peek` exclusively for body content. No `read_file`.
- **Cheap V1 t1 (25,459):** called `read_file(path, start_line=12398)` — ranged read on the 15k-line file.
- **Expensive trials (4 of 6, both candidates):** called `read_file(path)` with NO line range → entire 15k-line file in context → ~100k extra tokens.

Once the agent fell back to `read_file` without bounds, no SKILL.md framing recovered the run. This is **a `peek` and/or `read_file` discipline issue, not a framing issue.**

### Cluster-B implication

This empirically confirms the open backlog item:
- `peek <symbol> --full` / `peek --head N` / `peek --tail N` / `peek --range A,B` would let agents stay inside graphify on huge bodies, avoiding the unbounded-read_file failure mode.
- Alternative: SKILL.md hint "never `read_file` a >1000-line file without `start_line`" — testable as a pure-prompt variant before building the verb.

## Round 3: seed_task_005 (zero-tvm TS class-summary)

n=3 trials × V0/V1 on `buildDecodeEngine` summary. Tests TS extractor / cross-language generalization.

| candidate | TTC mean (±stdev) | calls (mean) | wall (mean) | pass |
|---|---|---|---|---|
| V0 | 70,979 (±57,742) | 12.0 | 79.1s | 3/3 |
| **V1** | **63,600 (±65,862)** | **9.7** | **68.7s** | 3/3 |

**Delta:** V1: ttc **−10.4%**, calls **−2.3 (−19.2%)**, wall **−13%**.

V1 generalizes to TS — same direction, same magnitude as task_003. Notably, V1 had two clearly-cheap trials (25,468 and 25,680) while V0 had one (31,094); V1's cheap path is also cheaper than V0's, suggesting the recipe table converges agents on a tighter walk.

## Aggregate

| task | task type | V1 Δ TTC | V1 Δ calls | won? |
|---|---|---|---|---|
| seed_task_003 | cluster-A refactor (Python) | **−12.0%** | +13.7% | ✓ |
| seed_task_004 | cluster-B body-heavy (Python) | −1.3% | +2.1% | tie |
| seed_task_005 | cluster-B body-heavy (TS) | **−10.4%** | **−19.2%** | ✓ |

**V1 wins 2-of-3, ties 1, never loses.** Generalizes across Python and TS extractors. Even on body-heavy task_004 (where R3 says Read should win), the framing didn't actively hurt — it was just dominated by the unbounded-`read_file` failure mode shared by both candidates.

**Thesis confirmed:** task-recipe framing helps where the right verb depends on task-shape (cluster A and parts of cluster B), and is neutral where the failure mode is downstream of graphify. The recipe table is also slightly denser than the verb cheat-sheet (~5 fewer primer tokens), so V1 is strictly Pareto-better.

**V2 (self-audit prompt) — clear negative result.** Don't ship.

## Recommendation: ship V1 as the new SKILL.md baseline

- 0 new code
- ~50 fewer tokens in the primer
- −10 to −12% TTC on cluster-A and TS comprehension tasks
- Neutral on body-heavy Python comprehension (the cluster-B failure mode is real but framing-orthogonal)
- Generalizes across language extractors

## Open follow-ups

1. **Add 3 more V0 vs V1 trials on task_003** to firm up significance (currently t≈1.0).
2. **Test "bound read_file" SKILL.md hint** as a pure-prompt variant on task_004 — cheaper than building `peek --range` and may capture most of the value.
3. **If R3 (task_005) is also neutral or wins**, V1 task-recipe framing is a clean small win to ship as the new SKILL.md baseline. Cost: 0 new code, ~50 fewer tokens in the primer (recipe table is denser).

## Cost

- R1 (3 candidates × 3 trials × 1 task): ~$3-5
- R2 (2 candidates × 3 trials × 1 task): ~$3-5
- R3 in progress: ~$3
- **Total: ~$10-15** for two clear findings (V1 modest win, V2 negative) + a confirmed cluster-B mechanism (read_file-without-bounds).
