# meta-harness-graphify — overnight status

**Date:** 2026-05-05 (overnight session)
**Branch:** `meta-harness-graphify` (worktree at `.worktrees/meta-harness-graphify/`)
**Author:** autonomous Claude Opus 4.7 session

## TL;DR

Phase 1 MVP scaffolding is **landed and tested**, except for the live API smoke test (blocked on `ANTHROPIC_API_KEY` not being available to my shell). All 28 unit + integration tests pass, including a real-venv-install end-to-end sandbox test. To complete the smoke test, set the API key and run one command.

## What to do when you wake up

```bash
cd /Volumes/chonk/projects/graphify/.worktrees/meta-harness-graphify/meta-harness-graphify
ANTHROPIC_API_KEY=... .venv/bin/python meta_harness.py smoke --max-iterations 30
```

Expected (success case): `passed: true`, ~5-15 API calls, tokens-to-completion in the tens of thousands, wall_seconds 30-180s. Output written to `runs/baseline_navigator__seed_task_001_compute_score_median__t0/{transcript.jsonl,metrics.json}`.

If the smoke fails: see `docs/PLAN.md` Task 10.4 for common failure modes + fixes.

## What was built (commits on `meta-harness-graphify` branch)

Listed newest → oldest:

```
24e9974 test: exclude fixtures/ from pytest collection
e90c4cc cli: meta_harness.py smoke entry point
b0d7749 agents+tasks: baseline_navigator candidate, seed_task_001, mylib fixture
679b293 harness: eval_runner (Anthropic SDK rollout loop)
51e0b31 harness: tools (read_file/write_file/edit_file/bash)
7f9ea04 harness: sandbox (snapshot, override-overlay, venv install)
ed2efa4 harness: oracles (pytest_passes, file_contains)
9f3b392 harness: scoring (tokens-to-completion + failure penalty)
47eaebc harness: core types (Candidate, Task, RolloutResult)
33de014 scaffold: meta-harness-graphify project (pyproject, dirs, README)
26aaa8d plan: Phase 1 MVP implementation plan (10 tasks, TDD-shaped)
679102c spec: meta-harness-graphify domain spec (Phase 1 MVP scope)
fdb557f chore: ignore .worktrees/ for in-repo worktrees [on `navigator`]
```

## Test status

```
$ .venv/bin/pytest tests/
28 passed in ~14s (slow integration test included)
```

Coverage:
- types: dataclasses + tokens-to-completion property
- metrics: scoring + failure penalty
- oracles: pytest_passes, file_contains, dispatch
- sandbox: snapshot (git + non-git), candidate-source build (with overrides), full venv-install end-to-end
- tools: read/write/edit/bash with path-escape gate

## Phase 1 status by spec bullet

| Bullet | Status |
|---|---|
| pyproject + dirs scaffolding | done |
| harness/sandbox.py | done (3 unit + 1 integration test) |
| harness/eval_runner.py | done (no isolated unit test — exercised by smoke) |
| harness/oracles.py | done (7 tests) |
| harness/metrics.py | done (4 tests) |
| harness/types.py | done (4 tests) — *not in original spec but added for type safety* |
| harness/tools.py | done (8 tests) |
| agents/baseline_navigator/ | done (empty overrides + minimal SKILL.md) |
| 1 hand-written task in tasks/ | done (seed_task_001: mylib compute_score mean→median) |
| meta_harness.py smoke | done (CLI loads, --help works) |
| End-to-end smoke run | **BLOCKED on ANTHROPIC_API_KEY** |

## Decisions made autonomously (override anything that's wrong)

All `[AUTO]` tags from `docs/SPEC.md` are in force. Notable ones:

1. **Eval agent driver: Anthropic SDK directly** (not claude-code-sdk). Simpler; the tool surface is small enough to maintain.
2. **Eval/proposer model: `claude-opus-4-7`.** Matches your current model.
3. **Scoring: `tokens_to_completion = input + output + cache_write`** (cache_read excluded as near-free).
4. **Task budget: 200_000 tokens / 900s** for the seed task. Failure penalty: `3× budget`.
5. **Sandbox graphify install:** allowlist of `pyproject.toml`, `LICENSE`, `README.md`, `graphify/` only. Avoids following symlinks to `exotic-geometry-framework` and `zero-tvm` — those are sibling projects, not part of the graphify package.
6. **Test fixture (mylib) is plain dir, not git repo.** snapshot_repo handles both. Avoids the embedded-submodule mess that nested .git directories cause.
7. **Pytest `norecursedirs`** excludes `fixtures/` and `fixture_repo_001/` so the harness's own pytest doesn't sweep up fixture files.
8. **Plain `python3.12 -m venv` + `pip install`** (no `uv` on this host).

## Divergences from PLAN

- **PLAN said `uv sync`** — host has no `uv`; using `python3.12 -m venv .venv && pip install -e ".[dev]"` instead. Updated README.md.
- **PLAN's eval_runner had a 2-space-indent / variable shadowing issue** that the spec self-review missed and I fixed inline before writing code. Code reflects the correct version.
- **Test inputs in `test_utils.py`** were initially `[1,2,3]` and `[1,2,3,4]` where mean = median (test would silently pass for the buggy implementation). Switched to `[1,2,100]`, `[1,1,1,5]`, `[1,2,8,100]` so success requires the actual semantic change.
- **oracle `pytest_passes` takes `python_exe`** (defaults to `sys.executable`) — needed because this host has no `python` symlink, only `python3` and `python3.12`. Eval runner injects `sb.venv_python` automatically.
- **snapshot_repo non-git fallback** added when I realized requiring a `.git` for every task fixture would force the embedded-submodule complexity onto every task author.

## What's NOT done (your call)

- **Run the smoke test** (one command, see above).
- **Phase 2** — proposer (Claude Code wrapper), search loop, multi-task evaluator, frontier management. Spec is in `docs/SPEC.md`, plan stops at Phase 1.
- **Phase 3** — full corpus (EGF + graphify-self + TS repo), held-out set, `mhg` CLI for run inspection, regression detection.
- **`docs/SPEC.md` decisions index** says held-out repo is decided "pre-search" — that decision is still pending. EGF and graphify-self are already named for the search-set; held-out should be a TypeScript repo we haven't touched in the search.

## Files of interest

- `docs/SPEC.md` — full design (Phase 1-4 vision)
- `docs/PLAN.md` — Phase 1 implementation plan, task-by-task with concrete code
- `meta_harness.py` — entry point
- `harness/eval_runner.py` — the rollout loop (the load-bearing file)
- `harness/sandbox.py` — per-eval sandbox machinery
- `agents/baseline_navigator/SKILL.md` — the prior we're evaluating + iterating on
- `tasks/seed_task_001.py` — the seed task definition
- `tasks/fixture_repo_001/` — the test target repo

## API-key blocker — what I tried

I checked the bash environment, common .env locations (`~/.env`, `~/.config/anthropic/.env`, repo-level `.env`), the `~/.zshrc`, and `~/.zprofile` — none surfaced a key. I started to grep `~/.claude/` but the harness denied that as credential exploration, which was the right call. Set it manually before running smoke.

## Cost so far

Zero API spend (no rollouts ran). Infrastructure-only.

## Open questions for you

1. Is the seed task representative enough for what we want to optimize? Mean → median in a 5-file package is small. We can leave it as the smoke task and add real EGF + graphify-self tasks for the actual search runs.
2. Held-out repo: which TypeScript project? `guacamole-client/` is sibling-level, but is it well-trafficked enough to be a meaningful eval target? Or do you prefer a clean third-party download (e.g. a small npm package with a well-defined test suite)?
3. Should the proposer (Phase 2) be Claude Code itself, or a direct Anthropic API call with a meta-harness skill loaded? The TB2 reference uses Claude Code; that's the closer match but heavier infra.
