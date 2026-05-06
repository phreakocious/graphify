"""Seed task 003: cross-file blast-radius on a real Python repo
(exotic-geometry-framework). Tests `graphify navigate ... in/out --kind=calls`
under same-name disambiguation: there are TWO functions named
`_compute_signal_metrics` in the repo, one in tools/dynamical_class.py and
one in tools/dynamical_fingerprint.py. The agent must produce blast radius
for the fingerprint variant ONLY — disambiguation is the test."""
from pathlib import Path

from harness.types import Task

# Real repo. snapshot_repo() will git-archive HEAD -> only tracked files
# (~40MB / 257 files). graphify-out is gitignored, so the sandbox starts
# without a graph — cli_auto_build candidate will build it on first focus,
# baseline candidates rely on first-call hygiene to run `graphify update`.
EGF_REPO = Path("/Volumes/chonk/projects/exotic-geometry-framework")


def task() -> Task:
    return Task(
        task_id="seed_task_003_egf_blast_radius",
        repo_dir=EGF_REPO,
        prompt=(
            "There are TWO functions in this repo named `_compute_signal_metrics` — "
            "one in `tools/dynamical_class.py` and one in `tools/dynamical_fingerprint.py`. "
            "For the variant in `tools/dynamical_fingerprint.py` ONLY, produce a refactor "
            "blast-radius report. Write to a new file `BLAST_RADIUS.md` in the repo root "
            "with two markdown sections:\n\n"
            "## Callers\n"
            "List every function/method that directly calls this `_compute_signal_metrics` "
            "(the dynamical_fingerprint.py one). Format each on its own line as "
            "`<file>:<line>:<symbol>` — for example `tools/foo.py:42:bar`. Direct callers only.\n\n"
            "## Callees\n"
            "List every user-defined function/class/method called from inside this "
            "`_compute_signal_metrics`'s body. Same `<file>:<line>:<symbol>` format. Skip "
            "stdlib/third-party calls (numpy, json, etc.) — only callees defined in this repo.\n\n"
            "Disambiguation matters: callers/callees of the dynamical_class.py variant must NOT "
            "appear in the report."
        ),
        oracle_kind="file_contains_all",
        oracle_args={
            "path": "BLAST_RADIUS.md",
            "substrings": [
                "## Callers",
                "## Callees",
                # The single direct caller of the fingerprint variant.
                "classify",
                # The fingerprint variant constructs GeometryAnalyzer and calls
                # add_all_geometries() on it.
                "GeometryAnalyzer",
            ],
        },
        budget_tokens=200_000,
        budget_seconds=900,
    )
