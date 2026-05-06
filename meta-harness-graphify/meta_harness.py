"""Meta-harness CLI. Phase 1 supports `smoke` only: one rollout (baseline ×
seed task × trial 0). Future phases add the search loop, multi-task evaluator,
and frontier management."""
import json
import os
import sys
from pathlib import Path

import click
from dotenv import load_dotenv

from harness.eval_runner import RunnerConfig, run_rollout
from harness.types import Candidate

ROOT = Path(__file__).parent
DEFAULT_GRAPHIFY_SRC = Path("/Volumes/chonk/projects/graphify")
load_dotenv(ROOT / ".env")


def _load_baseline() -> Candidate:
    base = ROOT / "agents" / "baseline_navigator"
    return Candidate(
        candidate_id="baseline_navigator",
        overrides_dir=base / "overrides",
        skill_md_path=base / "SKILL.md",
        parent_id=None,
        generation=0,
    )


def _load_seed_task():
    sys.path.insert(0, str(ROOT))
    from tasks.seed_task_001 import task
    return task()


@click.group()
def cli():
    """Meta-harness for the graphify navigator."""


@cli.command()
@click.option("--model", default="claude-opus-4-7", show_default=True)
@click.option("--max-iterations", default=50, show_default=True, type=int)
@click.option("--graphify-src", default=str(DEFAULT_GRAPHIFY_SRC), show_default=True)
def smoke(model: str, max_iterations: int, graphify_src: str):
    """Run one rollout: baseline candidate × seed task × trial 0."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        click.echo("error: ANTHROPIC_API_KEY not set", err=True)
        sys.exit(1)
    candidate = _load_baseline()
    task = _load_seed_task()
    config = RunnerConfig(model=model, max_iterations=max_iterations, log_dir=ROOT / "runs")
    click.echo(f"smoke run: {candidate.candidate_id} × {task.task_id}")
    click.echo(f"  model={model}  max_iterations={max_iterations}")
    result = run_rollout(
        candidate=candidate,
        task=task,
        trial=0,
        graphify_src=Path(graphify_src),
        config=config,
    )
    summary = {
        "candidate_id": result.candidate_id,
        "task_id": result.task_id,
        "passed": result.passed,
        "oracle_detail": result.oracle.detail[:300],
        "tokens_to_completion": result.tokens_to_completion,
        "tokens_input": result.tokens_input,
        "tokens_output": result.tokens_output,
        "tokens_cache_read": result.tokens_cache_read,
        "tokens_cache_write": result.tokens_cache_write,
        "wall_seconds": round(result.wall_seconds, 1),
        "n_api_calls": result.n_api_calls,
        "n_tool_calls": len(result.tool_calls),
    }
    click.echo(json.dumps(summary, indent=2))


if __name__ == "__main__":
    cli()
