"""Seed task 002: caller-trace task — list every function that calls
compute_score. Tests cross-file lookup, where graphify navigate calls /
where-used / search should beat read+grep."""
from pathlib import Path

from harness.types import Task

FIXTURE_REPO = Path(__file__).parent / "fixture_repo_002"


def task() -> Task:
    return Task(
        task_id="seed_task_002_compute_score_callers",
        repo_dir=FIXTURE_REPO,
        prompt=(
            "Find every function in this repository's `src/mylib/` package that "
            "calls `compute_score` (the function defined in `src/mylib/utils.py`). "
            "Write the list to a new file `CALLERS.md` in the repo root. Format "
            "each caller on its own line as `<filename>:<function_name>` — for "
            "example `helpers.py:rank_scores`. List each calling function exactly "
            "once. Do NOT list `compute_score` itself, and do NOT list functions "
            "that only transitively reach compute_score through another function "
            "in this repo — only direct callers."
        ),
        oracle_kind="file_contains_all",
        oracle_args={
            "path": "CALLERS.md",
            "substrings": [
                "helpers.py:rank_scores",
                "formatters.py:format_summary",
                "pipelines.py:scoring_pipeline",
                "pipelines.py:analytics_pipeline",
                "runner.py:process",
            ],
        },
        budget_tokens=200_000,
        budget_seconds=900,
    )
