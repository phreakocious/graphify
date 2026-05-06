# Lap-24: Session-benchmark A/B + harness fix

**Date:** 2026-05-06.
**Branch:** `meta-harness-graphify`.
**Test:** `seed_task_006_egf_session_chain` (3 sub-tasks anchored on
`tools/dynamical_fingerprint.py`: file orientation, blast radius of
`_compute_signal_metrics`, GeometryAnalyzer summary). All 3 writes
required, oracle = `all_of` of `file_contains_all`.
**Hypothesis:** structural data left in cache by early graphify calls
makes later sub-tasks cheaper — a "graphify-first" prime instruction
should reduce calls/wall on a chain of related questions even if
single-task ttc shows it doesn't.

## Candidates

| Candidate | Diff vs base |
|-----------|--------------|
| `task_recipe_navigator` (V1) | Lap-23 task-recipe SKILL.md, no priming instruction. |
| `graphify_first_navigator` (V2) | V1 + a single paragraph at top of SKILL.md: "Session priming — read this first. When you start work on a new file or symbol, your FIRST call should be `graphify shape <file>` (for files), `graphify summarize @<Class>` (for classes), or `graphify navigate @<symbol>` (for free symbols)…" |

## Results (n=3 each)

|                | V1 (task_recipe) | V2 (graphify_first) | Δ |
|----------------|------------------|----------------------|---|
| pass rate      | 2/3 (67%)        | 3/3 (100%)           | +33pp |
| calls (mean)   | 21.3             | 20.7                 | **−2.8%** |
| wall (mean)    | 114.8s           | 119.2s               | +3.8% |
| ttc (mean)     | 53.9k            | 57.8k                | +7.2% |

Per-trial:

```
V1: t0 PASS 26 calls 144.6s 51.6k ttc
    t1 PASS 19 calls 115.2s 81.1k ttc
    t2 FAIL 19 calls  84.6s 28.9k ttc

V2: t0 PASS 20 calls 122.2s 85.8k ttc
    t1 PASS 19 calls 111.5s 40.3k ttc
    t2 PASS 23 calls 124.0s 47.2k ttc
```

## Honest read

The priming-effect hypothesis as instrumented **did not pan out**. V2's
prime instruction did not reduce calls or wall in any meaningful way
(−2.8% calls is well within n=3 noise; wall and ttc went slightly up,
not down). A 3-step chain may be too short for the priming benefit
to compound, OR the V1 task-recipe alone already steers the agent
toward graphify-first behavior so the explicit prime is redundant.

V2's pass-rate win is real but **confounded by a harness bug**. V1
trial 2 failed because the model's final response had stop_reason
`end_turn` while content carried 3 valid `write_file` tool_use blocks.
The harness's old `if end_turn: break` short-circuit dropped the writes
silently, oracle then reported missing files. Fixed in
`03e374b`: execute any tool_use blocks the model emits regardless of
stop_reason. Under the fixed harness, V1 trial 2 would have passed —
making V1's pass rate 3/3 and erasing V2's reliability win.

## What this means for shipping

- **V2's prime instruction does NOT clearly improve graphify-first
  behavior** at this task scale. Don't ship it to user-facing skill.md
  on the basis of this experiment.
- **The harness bug is fixed forward** — future A/Bs measure cleaner.
- **The session-benchmark task design works** — 3-step chain executes
  reliably under both V1 and V2 (post-fix), so task#006 can be reused
  as a baseline for session-shape experiments.

## Implications for the broader theory

The reframe from earlier ("calls + wall compound across a session;
ttc is a cold-start metric") still holds. But this experiment shows
that **calling out priming explicitly in skill.md doesn't lower call
count on a 3-step chain**. The task-recipe framing alone may already
be enough to elicit graphify-first behavior; the explicit prime is
restating what the recipe table already implies.

Two open questions for lap-24+:

1. **Does priming compound on LONGER session chains?** A 5-7 step
   benchmark (e.g. file orientation → callers → callees → trace path →
   refactor plan → write changes → verify tests) might show the
   compounding effect. Worth building.
2. **Does priming come from VERB FUSION, not from prose instruction?**
   Lap-22 `blast` shipped a one-shot verb that won −47% calls. Lap-23b
   `summarize @<Class>` shipped a one-shot verb that won −31% calls.
   These are MEASURED priming-equivalent wins from collapsing
   multi-call workflows into single-call verbs. **The prose prime
   moved nothing; verb fusion moved everything**. Strong signal that
   the lap-24 next move is more verb fusion, not more SKILL.md prose.

## Recommendation

- **Don't ship** the V2 graphify_first_navigator prime to user-facing
  skill.md. The experiment doesn't justify the +7% ttc tax.
- **Ship the harness fix** (already committed `03e374b`).
- **Move to verb fusion** for lap-24:
  - Task #24 (no-arg `summarize` redesign as primer): the case for
    this is now WEAKER than it looked pre-experiment, because the
    explicit prime instruction didn't help. But the no-arg form is
    still admittedly weak; redesigning it is a small clean-up that
    should at least make it more useful for humans.
  - Task #25 (auto-prime hook): pause. If explicit prose-priming
    doesn't move calls, auto-running a primer query on session start
    is unlikely to either. Re-evaluate if a longer-session benchmark
    shows compounding.

## Cost

Combined A/B: 6 rollouts × ~75s × ~$1.40 ≈ $8.50.
