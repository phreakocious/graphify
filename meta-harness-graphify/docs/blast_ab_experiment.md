# `graphify blast` A/B — meta-harness validation of a feature ship

**Date:** 2026-05-06
**graphify branch:** `navigator` at `cfa0f7e`
**Task:** `seed_task_003_egf_blast_radius` — list every callsite + every direct
callee of `_compute_signal_metrics` (which has TWO same-name variants in the
EGF repo, so disambiguation is the test) on the real
exotic-geometry-framework repo.

## Setup

The lap-22 friction corpus surfaced two agents independently asking for a
one-shot blast-radius verb:

> "this task is literally callers + callees in one shot, but I had to do
> `navigate in` then re-focus and `navigate out`. A `graphify blast \"@symbol\"`
> ... would map 1:1 onto this common refactor question."

We shipped it: `graphify blast <symbol>` (commits `a470ac4` impl + `cfa0f7e`
skill.md cheat-sheet line). Internally piggybacks on the existing
`callers`/`callees` cursor pivots (which already filter to `kind=calls`),
so disambig and rank logic stay in one place. Renders `## Callers` /
`## Callees` markdown sections side-by-side.

To measure impact, we cloned `baseline_navigator` into `baseline_navigator_blast`
with one extra cheat-sheet row mentioning the verb. Empty overrides — both
candidates use the same navigator graphify; the only difference is whether
the SKILL primer mentions `blast` or not.

## Result

3-trial × 2-candidate compare on `seed_task_003`:

| candidate | TTC mean (±stdev) | calls (mean) | wall (mean) | pass rate |
|---|---|---|---|---|
| `baseline_navigator` | 17,365 (±1,259) | 13.7 | 76.8s | 3/3 |
| `baseline_navigator_blast` | **12,452 (±1,243)** | **7.3** | **54.6s** | 3/3 |
| **delta** | **−28.3% TTC** | **−46.7% calls** | **−29% wall** | — |

3.9 stdevs of separation — well outside the historic ~30% inter-trial variance
on real-repo tasks. Within the lap-22 backlog's predicted 20-40% drop band.

The agent in the blast condition replaces a 4-call sequence:
1. `graphify navigate "@_compute_signal_metrics"` (disambig)
2. `graphify navigate "@tools/dynamical_fingerprint.py/_compute_signal_metrics"` (focus)
3. `graphify navigate in --kind=calls`
4. (re-focus, then) `graphify navigate out --kind=calls`

with a 1-call sequence:
1. `graphify blast "@tools/dynamical_fingerprint.py/_compute_signal_metrics"`

That's the **calls 14 → 7** drop in the table — literally halved.

## Why this matters

Two agents asked for this independently, so the prediction had a strong prior.
But two equally-strong-prior items fell out at 0% in earlier ablations
(["Reading graphify output" sub-sections lap-21 R3]). Strong prior + n=3
empirical confirmation is the right bar for shipping new surface area.

This is the cleanest demonstration so far of the meta-harness's value
proposition: **friction corpus → backlog item with predicted delta → ship
fix → A/B-validate within an hour.** The whole loop ran inside this session.

## Open follow-ups

- **Mirror `blast` cheat-sheet line into platform-specific skill-*.md
  variants.** The user has a sync workflow we should respect — flagged
  in the lap-22 commit message and the backlog entry.
- **Cluster-A backlog items still open:** instance-method inferred-edge
  mis-resolution (the `+1 INFERRED hidden` we still see on the EGF
  blast output), peek --head/--tail/--full for huge bodies (cluster B),
  shape --filter, disambig --session hint. See navigate_v3_backlog.md
  lap-22 section for full structure.
- **Run blast A/B on a TS task** (seed_task_005 zero-tvm). The current
  TS task is comprehension-heavy (cluster B, where blast doesn't help)
  but a TS refactor task would let us check whether the −28% generalizes
  across language extractors.

## Cost

- 3 trials × baseline_navigator + 3 trials × baseline_navigator_blast on EGF
- ~$1 total — same order as a single Klein-summary rollout.
- Established a measurable A/B baseline that future cluster-A fixes can
  benchmark against.
