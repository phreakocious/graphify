"""Seed task: agent must change compute_score from mean to median."""
from pathlib import Path

from harness.types import Task

FIXTURE_REPO = Path(__file__).parent / "fixture_repo_001"


def task() -> Task:
    return Task(
        task_id="seed_task_001_compute_score_median",
        repo_dir=FIXTURE_REPO,
        prompt=(
            "There's a Python package in this repo (mylib) and a failing test "
            "in `tests/test_utils.py`. Run the test to see what's failing, "
            "find the function that needs to change, edit it so the test passes, "
            "then verify by running the test again. The package source is under "
            "`src/`."
        ),
        oracle_kind="pytest_passes",
        oracle_args={"node_id": "tests/test_utils.py"},
        budget_tokens=200_000,
        budget_seconds=900,
    )
