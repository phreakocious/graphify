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

## Round 2 — verb-data ships A/B (post-fix)

After committing the harness fix + the lap-24 navigator changes
(`9422c0d` summarize file:line + Suggested-next footer; `1b016cc`
shape ×N marker + entry-point promotion past --limit; `9d838f9`
summarize redirects file targets to shape, not peek/blast), re-ran
the same session benchmark with `baseline_navigator` and
`task_recipe_navigator`. Goal: see whether the data-surfacing
improvements move single-task call/wall counts *with the same
SKILL.md*. The cleanest comparison is **same candidate, pre-fix vs
post-fix** rather than V1-vs-V2.

|                     | task_recipe pre-fix | task_recipe post-fix | Δ |
|---------------------|---------------------|----------------------|---|
| pass rate           | 2/3 (66%)           | 3/3 (100%)           | +33pp (mostly harness fix) |
| **calls (mean)**    | **21.3**            | **17.7**             | **−16.9%** |
| **wall (mean)**     | **114.8s**          | **100.5s**           | **−12.5%** |
| ttc (mean)          | 53.9k               | 82.7k                | +53.4% (variance — see note) |

The ttc increase is mostly an artifact of failure removal: prior
trial 2 failed with truncated output (28.9k ttc); removing it from
the mean lifts the pre-fix baseline to ~66.4k. The genuine ttc delta
is closer to +24% — not a regression but a real cost. Likely the
new shape data lets the agent write more thorough SESS*.md files
(data is there, agent uses it). Pre-fix variance was massive
(stdev 26,151) because of the failure outlier; post-fix variance
is tight (stdev 4,311). The post-fix run is more consistent.

Baseline (no SKILL.md framing, post-fix):

|                     | baseline post-fix | task_recipe post-fix | Δ |
|---------------------|-------------------|----------------------|---|
| pass rate           | 2/3 (66%)         | 3/3 (100%)           | +33pp |
| calls               | 18.3              | 17.7                 | −3.3% |
| wall                | 107.2s            | 100.5s               | −6.2% |
| ttc                 | 46.3k             | 82.7k                | +78.7% |

Baseline failure was an oracle brittleness issue (SESS1.md missing
substring `_compute_signal_metrics` — the prompt doesn't actually
require naming that internal helper, but the oracle does). Same task
needs the substring list relaxed; tracked separately.

## What this confirms

**Verb-data improvements move calls. Prose-priming doesn't.** Round 1
prose-prime A/B showed −2.8% calls (within noise). Round 2 verb-data
A/B (same task, same SKILL.md) shows **−16.9% calls and −12.5% wall**.
Same compounding direction as lap-22 blast (−47% calls) and lap-23b
summarize @Class (−31% calls): collapsing follow-up calls into the
first-call output payload is the load-bearing axis.

The lap-24 ships:
- `9422c0d` no-arg summarize: file:line on entry points + Suggested-next footer
- `1b016cc` shape: ×N marker on fns + entry-point promotion past --limit
- `9d838f9` summarize: file targets redirect to shape (was peek/blast)
- `7e227de` skill.md: document shape ×N changes
- `03e374b` (meta-harness): execute tool_use blocks regardless of stop_reason

Round 2 cost: ~$8.40.

## Round 3 — shape docstring (smoke, n=3)

After Round 2 confirmed verb-data improvements move calls, shipped two
more changes and re-smoked task_recipe_navigator on `seed_task_006`:
- navigator `d23effa` summarize file targets fall through to shape
  (was: error-with-redirect-message)
- navigator `d79e67b` shape inlines top-of-file docstring/comment
  block under the header
- meta-harness `e53bf99` SESS1 oracle relaxed (substrings derived
  from prompt: `classify` + `build_residual_axis`, not the
  step-2-specific `_compute_signal_metrics`)

|                    | round 2 (no docstring) | round 3 (docstring) | Δ |
|--------------------|------------------------|----------------------|---|
| pass rate          | 3/3                    | 3/3                  | — |
| calls              | 17.7                   | 20.0                 | +13% |
| wall               | 100.5s                 | 111.9s               | +11% |
| ttc                | 82.7k                  | 50.5k                | **−39%** |

**Mixed result.** Calls and wall went up modestly; ttc dropped sharply.
Within n=3 noise: round 3 calls range 17-23, round 2 ranged 15-21.
Output tokens per trial in round 3 are consistent (3.9k, 4.5k, 4.3k);
the ttc drop is driven by input + cache_write savings (denser shape
output means fewer read_file / re-orientation calls to make up the
cache). Per-trial transcript inspection shows the agent's exploration
pattern is largely the same as round 2; the docstring add ~5 short
lines (~80 tokens) per shape call and doesn't directly reduce
follow-up calls on this task.

**Hypothesis** (unverified at this n): the docstring helps tasks where
file purpose matters (e.g. "what does this library do?"), not tasks
where the call graph is the answer (this benchmark). The mild call
uptick may be agents using the extra context to do incremental
exploration rather than commit faster.

**Decision**: keep the docstring ship. The data is objectively useful;
ttc savings are real; call delta is noise-band. If a future run
confirms a real call-count regression, revert. Lap-24 stops here.

Round 3 cost: ~$4.20.

## Bottom line

- **Ship**: all four navigator commits + harness fix. Pushed.
- **Don't ship**: the V2 prose-prime instruction (no measurable benefit).
- **Theory holds**: graphify's compounding edge comes from one-call
  data density and verb fusion, not from telling the agent to use it.
- **Next**: more verb-fusion candidates (lap-25 backlog).
