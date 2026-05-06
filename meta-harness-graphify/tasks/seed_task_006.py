"""Seed task 006: SESSION BENCHMARK — chain of three related sub-tasks
all anchored on `tools/dynamical_fingerprint.py` in the
exotic-geometry-framework repo. Tests the priming-effect hypothesis:
*structural data left in cache by early graphify calls makes later
sub-tasks cheaper*. An agent who runs `graphify shape <file>` in step 1
has line ranges, method signatures, and call structure cached for free
when steps 2 and 3 ask follow-up questions about the same file.

The chain is intentionally tightly scoped: same file, overlapping
symbols (`_compute_signal_metrics` body calls `GeometryAnalyzer` whose
internals step 3 inspects). An agent who reads the whole file in step 1
spends ~150k tokens up-front and pays per-turn output for the rest of
the session. An agent who uses graphify primitives accumulates indexed
pivots that step 2 and step 3 can reuse via the cursor or by direct
re-resolution.

Measure cumulative calls / wall / output across the rollout. ttc is
reported but de-emphasized (cache_write fires once at session start;
the metric that compounds across this kind of chain is calls + wall
+ steady-state output)."""
from pathlib import Path

from harness.types import Task

EGF_REPO = Path("/Volumes/chonk/projects/exotic-geometry-framework")


def task() -> Task:
    return Task(
        task_id="seed_task_006_egf_session_chain",
        repo_dir=EGF_REPO,
        prompt=(
            "You will perform THREE related sub-tasks on this repo, in "
            "ONE session. Each sub-task is grounded in the same file and "
            "overlapping symbols, so structural data you put in cache "
            "early helps later sub-tasks.\n\n"

            "## Step 1 — file orientation\n"
            "Summarize what `tools/dynamical_fingerprint.py` is for. "
            "Cover: the file's main classes/functions, what's exported "
            "(used from outside the file), and the longest function. "
            "100-150 words. Write to `SESS1.md`.\n\n"

            "## Step 2 — blast radius\n"
            "For the function `_compute_signal_metrics` defined in "
            "`tools/dynamical_fingerprint.py` (NOT the same-named one "
            "in `tools/dynamical_class.py`), list:\n"
            "  (a) every direct caller (functions/methods that call it)\n"
            "  (b) every direct callee defined in this repo (skip stdlib)\n"
            "Format each row as `<file>:<line>:<symbol>`. Write to "
            "`SESS2.md` with two markdown sections `## Callers` and "
            "`## Callees`.\n\n"

            "## Step 3 — connected class\n"
            "The function in step 2 instantiates `GeometryAnalyzer` and "
            "calls methods on it. Summarize the `GeometryAnalyzer` class: "
            "its purpose, its main methods, and what `add_all_geometries` "
            "does. 100-150 words. Write to `SESS3.md`.\n\n"

            "All three files must be written. Steps share repo context — "
            "use it. Stop when all three are done."
        ),
        oracle_kind="all_of",
        oracle_args={
            "oracles": [
                {
                    "kind": "file_contains_all",
                    "args": {
                        "path": "SESS1.md",
                        "substrings": [
                            "_compute_signal_metrics",
                            "classify",
                        ],
                    },
                },
                {
                    "kind": "file_contains_all",
                    "args": {
                        "path": "SESS2.md",
                        "substrings": [
                            "## Callers",
                            "## Callees",
                            "classify",
                            "GeometryAnalyzer",
                        ],
                    },
                },
                {
                    "kind": "file_contains_all",
                    "args": {
                        "path": "SESS3.md",
                        "substrings": [
                            "GeometryAnalyzer",
                            "add_all_geometries",
                        ],
                    },
                },
            ],
        },
        budget_tokens=500_000,
        budget_seconds=1500,
    )
