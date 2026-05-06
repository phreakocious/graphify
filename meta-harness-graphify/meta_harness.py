"""Meta-harness CLI. Phase 1 supports `smoke` (one or more rollouts of a
single candidate × task) and `compare` (two-candidate head-to-head with
aggregated stats). Future phases add the search loop and frontier."""
import json
import os
import statistics
import sys
from pathlib import Path

import click
from dotenv import load_dotenv

from harness.eval_runner import RunnerConfig, run_rollout
from harness.types import Candidate

ROOT = Path(__file__).parent
DEFAULT_GRAPHIFY_SRC = Path("/Volumes/chonk/projects/graphify")
load_dotenv(ROOT / ".env")


def _load_candidate(name: str) -> Candidate:
    base = ROOT / "agents" / name
    if not (base / "SKILL.md").is_file():
        raise click.ClickException(f"candidate {name!r} has no SKILL.md at {base}")
    return Candidate(
        candidate_id=name,
        overrides_dir=base / "overrides",
        skill_md_path=base / "SKILL.md",
        parent_id=None,
        generation=0,
    )


def _load_task(task_id: str):
    sys.path.insert(0, str(ROOT))
    if task_id == "seed_task_001":
        from tasks.seed_task_001 import task
        return task()
    raise click.ClickException(f"unknown task: {task_id!r}")


def _summarize(results) -> dict:
    """Aggregate metrics across trials. Trials with passed=False contribute
    `budget_tokens × 3` (penalty multiplier matching harness/metrics.py)."""
    n = len(results)
    if n == 0:
        return {}
    pass_rate = sum(1 for r in results if r.passed) / n
    ttc = [r.tokens_to_completion for r in results]
    calls = [r.n_api_calls for r in results]
    secs = [r.wall_seconds for r in results]
    summary = {
        "n_trials": n,
        "pass_rate": pass_rate,
        "tokens_to_completion": {
            "mean": round(statistics.mean(ttc)),
            "stdev": round(statistics.stdev(ttc), 1) if n > 1 else 0,
            "min": min(ttc),
            "max": max(ttc),
        },
        "n_api_calls": {
            "mean": round(statistics.mean(calls), 1),
            "min": min(calls),
            "max": max(calls),
        },
        "wall_seconds": {
            "mean": round(statistics.mean(secs), 1),
            "min": round(min(secs), 1),
            "max": round(max(secs), 1),
        },
    }
    return summary


@click.group()
def cli():
    """Meta-harness for the graphify navigator."""


@cli.command()
@click.option("--candidate", default="baseline_navigator", show_default=True)
@click.option("--task", default="seed_task_001", show_default=True)
@click.option("--trials", default=1, type=int, show_default=True)
@click.option("--model", default="claude-opus-4-7", show_default=True)
@click.option("--max-iterations", default=50, type=int, show_default=True)
@click.option("--graphify-src", default=str(DEFAULT_GRAPHIFY_SRC), show_default=True)
def smoke(candidate, task, trials, model, max_iterations, graphify_src):
    """Run one or more rollouts of a candidate × task, print per-rollout +
    aggregate metrics."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        click.echo("error: ANTHROPIC_API_KEY not set", err=True)
        sys.exit(1)
    cand = _load_candidate(candidate)
    tk = _load_task(task)
    config = RunnerConfig(model=model, max_iterations=max_iterations, log_dir=ROOT / "runs")
    click.echo(f"smoke: candidate={candidate} task={task} trials={trials} model={model}")
    results = []
    for trial in range(trials):
        click.echo(f"  -> trial {trial}/{trials - 1}")
        r = run_rollout(candidate=cand, task=tk, trial=trial, graphify_src=Path(graphify_src), config=config)
        results.append(r)
        click.echo(json.dumps({
            "trial": trial,
            "passed": r.passed,
            "ttc": r.tokens_to_completion,
            "n_api_calls": r.n_api_calls,
            "wall_seconds": round(r.wall_seconds, 1),
        }, indent=2))
    if trials > 1:
        click.echo("aggregate:")
        click.echo(json.dumps(_summarize(results), indent=2))


@cli.command()
@click.option("--candidates", required=True, help="comma-separated candidate ids")
@click.option("--task", default="seed_task_001", show_default=True)
@click.option("--trials", default=3, type=int, show_default=True)
@click.option("--model", default="claude-opus-4-7", show_default=True)
@click.option("--max-iterations", default=50, type=int, show_default=True)
@click.option("--graphify-src", default=str(DEFAULT_GRAPHIFY_SRC), show_default=True)
def compare(candidates, task, trials, model, max_iterations, graphify_src):
    """Run trials × N candidates, report side-by-side aggregates and pairwise
    deltas relative to the first candidate (the reference baseline)."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        click.echo("error: ANTHROPIC_API_KEY not set", err=True)
        sys.exit(1)
    names = [n.strip() for n in candidates.split(",") if n.strip()]
    if len(names) < 2:
        raise click.ClickException("need at least 2 candidates")
    tk = _load_task(task)
    config = RunnerConfig(model=model, max_iterations=max_iterations, log_dir=ROOT / "runs")

    aggregates: dict[str, dict] = {}
    for name in names:
        cand = _load_candidate(name)
        click.echo(f"\n[{name}] running {trials} trials...")
        results = []
        for trial in range(trials):
            click.echo(f"  -> trial {trial}/{trials - 1}")
            r = run_rollout(candidate=cand, task=tk, trial=trial, graphify_src=Path(graphify_src), config=config)
            results.append(r)
            click.echo(f"     passed={r.passed} ttc={r.tokens_to_completion} calls={r.n_api_calls} sec={r.wall_seconds:.1f}")
        aggregates[name] = _summarize(results)

    click.echo("\n=== summary ===")
    click.echo(json.dumps(aggregates, indent=2))

    ref = names[0]
    ref_ttc = aggregates[ref]["tokens_to_completion"]["mean"]
    ref_calls = aggregates[ref]["n_api_calls"]["mean"]
    click.echo(f"\n=== deltas vs {ref} ===")
    for name in names[1:]:
        d_ttc = aggregates[name]["tokens_to_completion"]["mean"] - ref_ttc
        d_calls = aggregates[name]["n_api_calls"]["mean"] - ref_calls
        pct_ttc = 100 * d_ttc / ref_ttc if ref_ttc else 0
        pct_calls = 100 * d_calls / ref_calls if ref_calls else 0
        click.echo(f"  {name}: ttc {d_ttc:+d} ({pct_ttc:+.1f}%)  calls {d_calls:+.1f} ({pct_calls:+.1f}%)")


if __name__ == "__main__":
    cli()
