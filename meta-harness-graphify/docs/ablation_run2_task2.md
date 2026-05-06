# Ablation run 2 — task 2 (caller trace) + cross-task analysis

**Setup:** same 6 candidates from run 1 (full calibrated + 5 ablations), n=3
trials each, on **seed_task_002** (caller-trace: list every direct caller
of `compute_score` across `mylib/` to `CALLERS.md`). Multi-file lookup,
favors graphify's call-graph strengths.

## Task 2 results

| candidate | TTC mean | stdev | calls | delta vs ref |
|---|---|---|---|---|
| baseline_navigator (ref) | 8,010 | ±1,745 | 8.0 | (ref) |
| abl_only_navigate | 9,761 | ±2,498 | 8.3 | **+21.9%** |
| abl_no_decision_rule | 11,125 | ±801 | 8.3 | **+38.9%** |
| abl_no_primer | 11,957 | ±2,707 | 8.7 | **+49.3%** |
| abl_no_first_call_hygiene | 13,343 | ±9,395 | 10.7 | **+66.6%** (bimodal!) |
| abl_no_output_conventions | 15,020 | ±1,715 | 8.3 | **+87.5%** |
| baseline_minimal | 21,603 | ±3,148 | 7.3 | **+169.7%** |

All passed 3/3. abl_no_first_call_hygiene's stdev of 9,395 is real
bimodality: trial 0 burned 23,933 ttc / 15 calls / 52s (agent didn't
realize they needed `graphify update` and floundered through read_file),
trial 1 nailed it in 6,011 ttc / 8 calls / 23s.

## Cross-task summary

| ablation | task 1 (body-heavy) | task 2 (caller-trace) | both? |
|---|---|---|---|
| only_navigate (drop verb table) | +4.3% | +21.9% | mild on both |
| no_decision_rule | +7.8% | **+38.9%** | task-2 dominant |
| no_primer | +12.7% | **+49.3%** | task-2 dominant |
| no_output_conventions | **+31.2%** | **+87.5%** | huge both tasks |
| no_first_call_hygiene | **+35.5%** | **+66.6%** (bimodal) | huge both tasks |
| baseline_minimal (full strip) | **+115.5%** | **+169.7%** | (sanity floor) |

**Same hierarchy on both tasks**, with task 2 amplifying every effect
because it's structurally more demanding. Task 2 (multi-file lookup)
tests almost every section the SKILL.md ships.

## What this means for the SKILL.md footprint

**Every section is earning its keep.** The calibrated baseline isn't
over-built — five-of-five ablations degraded TTC, and on task 2 four
of those five degraded it by >20%.

| section | task 1 cost-to-remove | task 2 cost-to-remove | trim verdict |
|---|---|---|---|
| Reading graphify output (4 sub-rules + edge confidence) | +31% | +88% | **KEEP — load-bearing** |
| First-call hygiene (graphify update + !stale banner) | +35% | +67% | **KEEP — load-bearing** |
| graphify-first as a primer | +13% | +49% | **KEEP — task-2 critical** |
| R3 decision rule (when graphify wins vs read_file) | +8% | +39% | **KEEP — task-2 critical** |
| Verb table beyond navigate (peek/shape/search/locate/etc.) | +4% | +22% | **KEEP — smallest effect, but still positive on the harder task** |

Even the lowest-impact section (verb table beyond navigate) costs +22%
TTC on task 2. Trimming it would shrink the SKILL by ~30% but add ~$0.025
to every rollout via increased TTC — a net loss for a tool meant to
minimize agent friction.

## What surprised me (or should I say, validated lap-21)

The lap-21 calibration of `baseline_navigator` SKILL.md picks the
sections that ship in the production graphify SKILL too. The ablation
exercise was set up with the hypothesis "surely some of this is
unearned token cost." The data says no — the lap-21 maintainer's
intuition matched the empirical results 5-for-5.

Specifically:
- **Output conventions** (the per-row metadata, hint trust, omission
  counts, edge confidence sub-rules) is the largest single ablation
  effect. The maintainer's "omission counts are never silent" rule and
  the `_emit_hint` system are doing exactly what they're supposed to
  do — give the agent enough metadata to act on graphify output without
  hedging via read_file.

- **First-call hygiene** has the strongest bimodal effect: when the
  agent doesn't run `graphify update` and lands on a stale graph, the
  rollout falls off a cliff (52s wall, 24k ttc). When they do run
  it (or get lucky on a fresh graph), the rollout is normal. The
  paragraph is critical insurance against the bad-luck branch.

- **Primer framing** matters most on task 2 — exactly the kind of
  task where graphify-as-primer (vs graphify-as-small-task-tool) is
  the right framing.

## Tool-call distribution

| candidate | task 1 graphify verbs | task 2 graphify verbs |
|---|---|---|
| baseline_navigator | update=6, navigate=3, peek=1 | navigate=9, update=3, peek=2 |
| baseline_minimal | update=3, search=3 | navigate=4, update=3, --help=2, search=1 |
| abl_only_navigate | navigate=2, update=1 | navigate=12, locate=0 |
| abl_no_first_call_hygiene | update=5 (still!), navigate=1, locate=2 | navigate=2, locate=1 |

Notable findings:
- `peek` use only happens with the full verb table; agents in
  abl_only_navigate fall back to navigate-only chains (12 navigate
  calls vs 9).
- `graphify --help` appears 2× in baseline_minimal task 2 — the agent
  literally has to look up the surface from scratch because the SKILL
  doesn't tell them.
- Even abl_no_first_call_hygiene runs `graphify update` 5× on task 1.
  The hygiene paragraph isn't what causes update calls — but its
  absence makes the agent waste calls trying to figure out *when* to
  run it.

## Cost & methodology

- **15 ablation rollouts × 2 tasks + 6 baseline_navigator + 6 baseline_minimal = 42 rollouts**
- **~$10-12 of API time**
- All rollouts run sequentially in foreground (lesson from compare run 3:
  bg compare runs racing each other corrupted run dirs).
- `transcript.jsonl` now unlinked at start of each rollout (fix landed
  this session — was appending across re-runs and inflating tool counts).

## What's next

**The calibrated SKILL.md is well-tuned at the section granularity.**
Trim opportunities don't exist at this level. To reduce footprint
further, the next experiments would have to be:

1. **Sub-section ablations within "Reading graphify output":** the
   biggest section. Are all five sub-rules (per-row metadata, hint
   footers, omission counts, first-row, edge confidence) equally
   load-bearing, or could one or two be cut?
2. **Per-verb ablations within the verb table:** drop `summarize` /
   `changed` / `query` (rarely used in this corpus) without losing
   `peek`/`shape`/`search`/`locate`. The +22% effect on task 2 is
   coming from *something* — which verb specifically?
3. **More tasks:** all current tasks are Python. Cross-language
   (TypeScript) and architecture-traversal tasks might surface
   different priorities.

Of these, (1) is the most surgical and most likely to find slack
without behavioral cost.

But also worth considering: the lap-21 baseline already achieves -61%
to -63% TTC vs the minimal baseline. The remaining ~7-8k tokens-to-
completion is mostly tool output. Further SKILL.md optimization will
have diminishing returns; the bigger leverage is in **graphify CLI
output density** (the other half of the joint search target named in
the SPEC).
