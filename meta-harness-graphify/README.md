# meta-harness-graphify

Search-driven optimization of the **graphify navigator** SKILL.md and CLI
behavior. Adapts Stanford Meta-Harness (arXiv:2603.28052) to measure
which parts of graphify's surface earn their tokens for a coding agent.

The unit of measurement is **tokens-to-completion** — `input + output +
cache_write`, summed over an Anthropic SDK rollout that drives Claude
through a coding task using `graphify` as a tool. Pass-gated; failure
penalty 3× budget.

## Quick start

```bash
cd meta-harness-graphify
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
.venv/bin/mhg smoke --candidate baseline_navigator --task seed_task_001 --trials 3
```

## CLI

```
mhg smoke --candidate <id> [--task <id>] [--trials N]
   single-candidate, multi-trial. prints per-rollout metrics + aggregate.

mhg compare --candidates a,b,c [--task <id>] [--trials N]
   N×K head-to-head. prints per-candidate aggregates and deltas vs the
   first candidate (the reference baseline).
```

Per-rollout transcripts live in `runs/<candidate>__<task>__t<N>/`.

## What's been measured

| comparison | TTC mean (delta) | pass | notes |
|---|---|---|---|
| baseline_minimal vs baseline_navigator | -61.3% (17,923 → 6,940) | 1.0 / 1.0 | calibrated SKILL.md savings on seed_task_001 (compare run 2). Same call count and wall time — the agent does the same plan with cheaper content. |

Headline: **the lap-21 calibration of `baseline_navigator/SKILL.md` (R3
decision rule, primer framing, full verb table, output conventions)
saves 61% TTC over the prior 14-line baseline at identical call count
and pass rate.** See `docs/compare_run2_clean.md` for the writeup.

## Candidates currently in `agents/`

- **baseline_minimal** — pre-lap-21 14-line SKILL.md. The floor.
- **baseline_navigator** — lap-21 calibrated SKILL.md (~70 lines). The
  reference baseline for ablation.
- **abl_no_decision_rule** — calibrated minus the R3 decision-rule section
- **abl_no_primer** — calibrated minus the "graphify-first as a primer"
  paragraph
- **abl_only_navigate** — calibrated with verb table reduced to navigate
  rows only (drop peek/shape/search/locate/path/explain/query/summarize/
  changed)
- **abl_no_output_conventions** — calibrated minus "Reading graphify
  output" section (per-row metadata, hint footers, omission counts,
  edge confidence)
- **abl_no_first_call_hygiene** — calibrated minus the `graphify update`
  / stale-graph paragraph

## Tasks currently in `tasks/`

- **seed_task_001** — mylib `compute_score` mean → median bug-fix.
  Body-heavy, single function. Favors `peek`-style verbs.
- **seed_task_002** — caller trace: list every direct caller of
  `compute_score` across `mylib/`. Multi-file lookup. Favors
  `navigate calls` / `wu` / `search`.

## Project layout

```
meta-harness-graphify/
├── agents/                  # candidates (one SKILL.md per dir)
├── docs/                    # SPEC, PLAN, run writeups
├── harness/
│   ├── eval_runner.py       # Anthropic SDK rollout loop
│   ├── metrics.py           # tokens-to-completion + failure penalty
│   ├── oracles.py           # pytest_passes / file_contains / file_contains_all
│   ├── sandbox.py           # per-rollout sandbox (snapshot + venv install)
│   ├── tools.py             # read_file/write_file/edit_file/bash agent tools
│   └── types.py             # Candidate / Task / RolloutResult / OracleResult
├── tasks/                   # task definitions + fixture repos
├── meta_harness.py          # CLI entry point (mhg)
└── runs/                    # per-rollout output (gitignored)
```

## See also

- `docs/SPEC.md` — full design
- `docs/PLAN.md` — Phase 1 implementation plan
- `STATUS.md` — overnight Phase 1 build status
- `docs/compare_run2_clean.md` — calibrated vs minimal head-to-head
- The graphify project's `CLAUDE.md` — design philosophy that informs
  what's "calibrated" SKILL.md content
