"""Seed task 004: class-summary on a real big file. Tests graphify-as-primer
on a 15K-line file where blind read_file would burn the whole budget.
`peek <Class>` on KleinBottleGeometry surfaces method shapes + algorithm
names without reading the file body."""
from pathlib import Path

from harness.types import Task

EGF_REPO = Path("/Volumes/chonk/projects/exotic-geometry-framework")


def task() -> Task:
    return Task(
        task_id="seed_task_004_egf_klein_summary",
        repo_dir=EGF_REPO,
        prompt=(
            "Write a 150-word summary of the `KleinBottleGeometry` class (in "
            "`exotic_geometry_framework.py`) to a new file `KLEIN_SUMMARY.md` in the "
            "repo root. The summary must specifically cover:\n"
            "  (a) the math foundation the class is built on,\n"
            "  (b) what `compute_metrics` emits (name the actual metric keys),\n"
            "  (c) the central algorithmic primitive used to compute the metrics.\n\n"
            "Be concrete — name actual algorithms, named metrics, and the math objects "
            "involved. Do not write a generic 'this class implements X' summary."
        ),
        oracle_kind="file_contains_all",
        oracle_args={
            "path": "KLEIN_SUMMARY.md",
            "substrings": [
                "KleinBottleGeometry",
                "compute_metrics",
                # `compute_metrics` emits three named metrics; linear_complexity
                # is the most prominent and any accurate summary mentions it.
                "linear_complexity",
                # Berlekamp-Massey is the named algorithmic primitive. Substring
                # tolerates "Berlekamp-Massey", "Berlekamp Massey", "Berlekamp".
                "Berlekamp",
            ],
        },
        budget_tokens=200_000,
        budget_seconds=900,
    )
