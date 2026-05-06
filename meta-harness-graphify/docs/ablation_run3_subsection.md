# Ablation run 3 — sub-section ablation within "Reading graphify output"

**Setup:** Run-1 found "Reading graphify output" was the highest-effect
section (+31% task 1, +88% task 2). To find which sub-rule(s) carry
that weight, drop one bullet at a time. 5 candidates × 2 tasks ×
3 trials each = 30 rollouts.

The 5 sub-rules under ablation:
1. **`abl_read_no_metadata`** — drops "Per-row metadata" (`· g3d · 482ln`,
   `!stale` marker, `[depth≤N]`)
2. **`abl_read_no_hints`** — drops "Hint footers name structural cause /
   trust them"
3. **`abl_read_no_omission`** — drops "Omission counts are never silent"
   plus `--explain-cost` mention
4. **`abl_read_no_firstrow`** — drops "First row is usually the right one"
   plus path-qualified disambiguation
5. **`abl_read_no_edgeconf`** — drops "Edge confidence" (EXTRACTED /
   INFERRED / AMBIGUOUS)

## Reference (same conditions, run earlier this session)

| | task 1 | task 2 |
|---|---|---|
| baseline_navigator | 8,317 ± 2,477 | 8,010 ± 1,745 |
| baseline_minimal | 17,923 ± 286 (+115%) | 21,603 ± 3,148 (+170%) |

## Results — sub-section ablation

| sub-rule dropped | task 1 delta | task 2 delta | verdict |
|---|---|---|---|
| **abl_read_no_edgeconf** (drop EXTRACTED/INFERRED/AMBIGUOUS) | **+38%** | **+100%** | **KEEP** — biggest sub-rule, especially on task 2 |
| **abl_read_no_omission** (drop +N hidden / --explain-cost) | +13% | **+111%** | **KEEP** — task-2 critical |
| abl_read_no_metadata (drop g3d/!stale/[depth≤N] explainer) | +20% | -7% | mixed — task-1 effect only |
| abl_read_no_hints (drop "trust hint footers") | +12% | +18% | small but consistent |
| abl_read_no_firstrow (drop "first row + path-qualified") | +2% | -9% | **inside noise on these tasks** |

## What this reveals

**Edge confidence** is the surprise winner. Removing the EXTRACTED /
INFERRED / AMBIGUOUS explainer doubles TTC on task 2 (+100%). Without
that primer, the agent doesn't know which edges to trust on the
caller-trace task and over-explores. This sub-rule is now the highest-
leverage three lines in the entire SKILL.md.

**Omission counts** is also load-bearing on task 2 (+111%). Without the
"+N INFERRED hidden" / "--explain-cost" mention, the agent doesn't
know how to widen filters and burns calls trying to expand listings.

**First-row guidance** and **per-row metadata** look free on these
tasks — but both fire only under specific conditions:
- First-row guidance fires when graphify returns a disambig listing for
  cross-file same-name symbols. Both fixtures have unique symbol names,
  so this rule never has anything to act on. Cannot confidently call
  it droppable until tested on a task with same-name disambig.
- Per-row metadata's `!stale` marker fires when graph mtime < file
  mtime. Both fixtures get a fresh graph at sandbox build, so this
  rule's `!stale` clause never fires. Cannot confidently call it
  droppable.

**Honest takeaway: "Reading graphify output" earns its 88% task-2
effect mostly via edge_confidence and omission_counts.** The other
three sub-rules need tasks that trigger their fire conditions before
we can call them load-bearing or droppable.

## Implications for the SKILL.md footprint

- `abl_read_no_edgeconf` and `abl_read_no_omission` are confirmed
  load-bearing — not droppable. These two sub-rules carry the section's
  weight.
- `abl_read_no_hints` shows small but consistent effect across both
  tasks; safe to keep.
- `abl_read_no_firstrow` and `abl_read_no_metadata` are zero-effect on
  the current corpus, but the corpus doesn't exercise their trigger
  conditions. The right next experiment is **add tasks that fire these
  conditions** (a task with `compute_score` defined in 2+ files; a
  task on a stale graph) and re-run the ablation.

## A new experimental angle: corpus-conditioned ablation

This run revealed a methodological limitation: ablations only show
signal under conditions that trigger the rule. To certify a sub-rule
as droppable, we need:
1. The sub-rule's removal causing zero/negative TTC on tasks that DO
   trigger its conditions, OR
2. The sub-rule's conditions never appearing in the agent's tasks
   (which would have to be argued at the corpus level, not on n=2)

Neither bar is met for first-row or metadata at this point.

## Cost

- 30 rollouts × ~$0.30 = ~$9 of API time
- All sequential foreground; no race contamination
