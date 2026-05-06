# Lap-23b: `graphify summarize @<Class>` A/B + the metrics reframe

**Date:** 2026-05-06
**graphify branch:** `navigator` at `f9eafce`
**Question:** Does fusing class-level context into one verb (`summarize @<Class>`) reduce agent cost on body-heavy class-comprehension tasks where agents fall back to `read_file`?

## Context

Lap-23 R1 found that organizing the SKILL.md by task → recipe beat verb cheat-sheet by −10 to −12% TTC on cluster-A and TS comprehension tasks, but tied on Python body-heavy comprehension (task_004 Klein). Transcript inspection located the cluster-B failure mode: agents fall to `read_file` on huge files because no graphify verb fuses class-level info — sig + methods + callers + inheritance — into one call.

`summarize @<Class>` is the blast-pattern fix: collapse the multi-call class-context walk into one structured-markdown call. Same pattern that delivered −28% on lap-22's blast experiment.

## The metrics reframe (mid-experiment)

The user pointed out during the run: **TTC is the wrong primary metric for multi-turn agent sessions.**

- `ttc = input + output + cache_write` measures cold-start cost.
- Once SKILL.md is in cache (one cache_write at session start), subsequent turns pay only ~10% of that for cache_read.
- Sessions span 10-50 turns; primer cost amortizes to nearly zero.
- What compounds across a session: **calls** (each is a round-trip), **wall** (latency, cache TTL pressure), **output tokens** (uncached, regenerated each turn).

We pivoted to leading the analysis with calls + wall, treating ttc as a sanity-check footnote. The user also surfaced the bigger reframe: graphify-first vs read-first isn't measured on a single task — it's measured by what's in cache to prime the *next* tasks. That informed the lap-24 plan (session benchmark + summarize redesign + auto-prime hook).

## Setup

V1 = `task_recipe_navigator` (lap-23 winner — task-recipe SKILL.md, no summarize-class line).
V2 = `task_recipe_summarize` (V1 + one cheat-sheet row pointing at `summarize @<Class>`).

n=3 trials × 2 candidates × 2 tasks (task_004 EGF Klein body-heavy, task_005 zero-tvm TS body-heavy).

## Results

### task_004 (class summary — direct target)

| metric | V1 | V2 | delta |
|---|---|---|---|
| **calls** | 12.0 | **8.3** | **−31%** |
| **wall** | 74.4s | **59.3s** | **−20%** |
| ttc | 65,429 | 107,227 | +64% (variance) |
| pass | 3/3 | 3/3 | — |

**summarize @KleinBottleGeometry was used in 3/3 V2 trials.** The verb did its job.

The +64% ttc came from V2 having 2/3 expensive trials vs V1's 1/3. The expensive trials all ran `read_file(path)` without bounds AFTER summarize gave them the right info. **The verb gave context but didn't change the read_file discipline.** That's an agent-side discipline issue, not a verb fusion issue.

### task_005 (function summary — non-target)

| metric | V1 | V2 | delta |
|---|---|---|---|
| **calls** | 13.0 | **10.3** | **−21% (incidental)** |
| wall | 80.4s | 70.7s | −12% |
| ttc | 38,045 | 67,159 | +77% (variance) |

`buildDecodeEngine` is a function. summarize-class correctly *not* used (the redirect would say "try peek or blast"). The −21% calls came from V2 happening to do fewer output-tweak edit iterations than V1 — incidental.

## Findings

1. **summarize-class works as designed on class tasks.** −31% calls / −20% wall on task_004. Verb is real, agents pick it up cleanly, redirect for non-class targets prevents misuse.

2. **The cluster-B failure mode persists.** 3/6 V2 trials still hit the body-heavy expensive mode. Root cause: agent uses `read_file(path)` without bounds even when graphify has given them line ranges and method bodies. **The verb doesn't fix discipline.**

3. **Single-task harness is too noisy for 20-30% effects.** The bimodal failure rate (~30-50%) means the inter-trial stdev exceeds the effect size. Need either n=10+ per arm or session-level benchmarks.

4. **The metrics reframe (calls/wall over ttc) lands.** It also weakens the lap-23 R1 framing-only result: V1's win on task_003 was −12% ttc but +14% calls — partly cache_write fluctuation that wouldn't show in real use. The lap-22 blast (−47% calls) and lap-23b summarize (−31% calls) are the more load-bearing wins.

## Next experiments (lap-24)

The metrics reframe + the priming-effect insight (graphify calls leave structural data in cache for free use by next tasks) point at three connected pieces:

1. **Session benchmark task type** — sequence of related questions on the same repo, oracle each step, measure cumulative calls/wall across the chain. Surfaces the priming effect that single-task TTC undermeasures.

2. **Redesign no-arg `summarize`** — current form (top communities, edge mix, language counts) is admittedly not very useful. New version: top hubs by external in-degree, top entry points, recent-activity files (changed last week), archived paths to skip, suggested orientation. ~500-1500 tokens, dense, agent-targeted.

3. **Auto-prime hook** — run `graphify summarize` on session start before the first user prompt. Output goes into cache as turn-0 context. Measure via session benchmark: same chain with vs without prime turn.

The user pitch is nice: "open the project, agent already knows your structure."

## Cost

- 12 rollouts (n=3 × 2 candidates × 2 tasks): ~$5.
- Combined with lap-23 R1 (~$15): ~$20 total for the framing + verb fusion experiments.

## Open follow-ups

- **Test "bound read_file" SKILL.md hint** — cheaper than building peek windowing and may capture most of the cluster-B value (the discipline is the bottleneck, not the verb).
- **Add output-tokens line to compare summary** — explicit steady-state cost number alongside ttc.
