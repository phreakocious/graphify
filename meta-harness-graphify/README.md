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

**Two head-to-head comparisons + a 5-section ablation matrix on two tasks.**

### Calibrated vs minimal SKILL.md (head-to-head)

| task | baseline_minimal | baseline_navigator | delta | pass |
|---|---|---|---|---|
| 1 (compute_score median) | 17,923 ttc | 6,940 ttc | **-61%** | 1.0 / 1.0 |
| 2 (caller trace) | 21,603 ttc | 8,010 ttc | **-63%** | 1.0 / 1.0 |

Same call count and wall time on both tasks — the agent does the same
plan with cheaper content per step. See `docs/compare_run2_clean.md`.

### Ablation matrix — does every section earn its keep?

Each row drops one section of the calibrated baseline_navigator
SKILL.md; lower number = section is more droppable. **No section
showed zero effect on either task.**

| section dropped | task 1 cost | task 2 cost | verdict |
|---|---|---|---|
| Reading graphify output | +31% | **+88%** | KEEP — biggest effect |
| First-call hygiene (`graphify update`, `!stale` banner) | +35% | **+67%** (bimodal) | KEEP — insurance |
| graphify-as-primer paragraph | +13% | **+49%** | KEEP — task-2 critical |
| R3 decision rule | +8% | **+39%** | KEEP — task-2 critical |
| Verb table beyond navigate (peek/shape/etc.) | +4% | **+22%** | KEEP — smallest but real |

**Headline: the lap-21 maintainer's calibration matches empirical
results 5-for-5.** No section in the calibrated SKILL.md is unearned
token cost. See `docs/ablation_run1_task1.md` and
`docs/ablation_run2_task2.md` for full writeups.

### Footprint optimization next steps

Section-granularity ablation has hit diminishing returns. To trim
further would need:
1. Sub-section ablations within "Reading graphify output" (5 sub-rules)
2. Per-verb ablations within the verb table
3. CLI-side optimization (output density of `graphify navigate` etc.)

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
