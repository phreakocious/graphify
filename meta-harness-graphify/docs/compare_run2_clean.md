# Compare run 2 (2026-05-06): clean run after install fix

**Setup:** baseline_minimal vs baseline_navigator (calibrated), seed_task_001
(mylib compute_score median bug-fix), n=3 trials, claude-opus-4-7. Sandbox
now editable-installs the target repo's pyproject.toml so the oracle's
pytest invocation no longer depends on whether the agent ran
`pip install -e .` themselves.

## Headline

**baseline_navigator (calibrated) saves -61.3% tokens-to-completion vs
baseline_minimal**, at identical call count and identical pass rate.

| candidate | trials | pass | TTC mean | TTC stdev | TTC min | TTC max | calls | sec |
|---|---|---|---|---|---|---|---|---|
| baseline_minimal | 3 | 3/3 | 17,923 | 286 | 17,644 | 18,215 | 7.3 | 23.7 |
| baseline_navigator | 3 | 3/3 | **6,940** | 1,505 | 5,979 | 8,674 | 7.3 | 23.6 |
| Δ | | | **-10,983 (-61.3%)** | | | | +0.0 (+0.0%) | -0.1s (-0.4%) |

## Why this is the right comparison

- **Same call count** (7.3 vs 7.3). The agent isn't taking more steps with
  the calibrated SKILL — it's using the same plan with cheaper steps.
- **Same wall-clock time** (23.6 vs 23.7s). Confirms the win is content
  per call, not concurrency or fewer steps.
- **Same pass rate** (3/3 both). Calibrated isn't trading correctness for
  brevity.
- **Tight variance on minimal** (stdev 286, ~1.6% of mean) — the install
  fix removed the env-hack noise that compare run 1 was hitting.
- **Calibrated still has ~22% stdev** (1505 / 6940), meaning the agent
  sometimes uses peek (5,979 ttc) and sometimes reads the file (8,674
  ttc) — there's still room to push them toward the more efficient path.

## What drove the win

Trial 0 of baseline_navigator (5,979 ttc) used **`graphify peek
"compute_score"`** to locate AND read the function body in one bash
call — the verb `baseline_minimal` doesn't mention. This is the lap-21
"primer" framing paying off: the SKILL.md surfaces peek as a verb, the
agent reaches for it, and gets the body without a `read_file` on
`utils.py` (which would have read the entire file including unrelated
`normalize` / `summarize` defs).

The 8,674-ttc trial used a slightly less efficient path — read_file on
utils.py — but still under half the minimal's ttc, because the
calibrated SKILL also gives better tool-selection priors elsewhere.

## Limitations

- Single task. With one task, ablations of unused features look free.
  Need ≥3 diverse tasks before drawing conclusions about feature value.
- n=3 catches gross effects (61% delta is unambiguous) but not subtle
  ones; ablation runs with smaller deltas need n≥5.
- The fixture's compute_score function is ~5 lines. On a multi-100-line
  function, peek and read_file converge — this task amplifies peek's
  edge.

## Next: ablation matrix

5 ablations of baseline_navigator (one section dropped each), n=3 trials
each, same task. Tells us which sections of the calibrated SKILL.md
contribute most to the -61% win.

- abl_no_decision_rule
- abl_no_primer
- abl_only_navigate
- abl_no_output_conventions
- abl_no_first_call_hygiene
