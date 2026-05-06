# Compare run 1 (2026-05-06): baseline_minimal vs baseline_navigator (calibrated)

**Status:** flaky pass rate (33% on both candidates) due to a fixture
install bug — the mylib src-layout package wasn't pip-installed into the
sandbox venv, so the oracle's `pytest tests/test_utils.py` failed to
import unless the agent happened to run `pip install -e .` themselves
during their rollout. The bug affects BOTH candidates equally, so the
TTC comparison is still meaningful.

Fix shipped in same series: `Sandbox.create()` now editable-installs
the target repo's pyproject.toml into the venv before the rollout.

**Headline result (still valid as a TTC comparison despite oracle flake):**

`baseline_navigator` — encoding lap-21 R3 lessons (peek/shape/locate/wu
verbs, primer framing, omission counts, hint trust) — saves **-39.0%
tokens-to-completion** vs `baseline_minimal` (the prior 14-line baseline).

| candidate | trials | TTC mean | TTC stdev | TTC min | TTC max | calls mean | sec mean |
|---|---|---|---|---|---|---|---|
| baseline_minimal | 3 | 20,506 | 2,419 | 17,782 | 22,404 | 10.0 | 29.9 |
| baseline_navigator | 3 | 12,502 | 2,847 |  9,994 | 15,596 |  9.3 | 28.0 |
| Δ | | **-8,004 (-39.0%)** | | | | -0.7 (-7.0%) | -1.9s (-6.4%) |

## Why calibrated won

Trial 1 of `baseline_navigator` (the 9,994-token run) used **`graphify peek
"compute_score"`** — the verb that `baseline_minimal` doesn't mention.
Calibrated SKILL.md ships a verb cheat-sheet including peek/shape/search/
locate/path/explain/query/summarize/changed; minimal only mentioned
navigate. The agent reaches for the right verb when the SKILL primes it.

## Notes

- Variance is real: stdev ~15% of mean on a small task. n=3 catches gross
  effects but borderline calls need n≥5.
- Pass-rate flake is independent of candidate quality — both candidates
  hit it at the same rate.
- The install-flake fix should land before the next compare run.
