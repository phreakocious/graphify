"""Seed task 005: TypeScript class-summary on zero-tvm.

`buildDecodeEngine` in src/zero-tvm/engine-core.ts is a 1799-line "class-shaped"
function (graphify reports class-shape via `methods` pivot — 36 internal methods).
This task tests:
  - graphify's TypeScript extractor on a real WebGPU/transformer codebase
  - graphify-as-primer on a 1.8k-line file
  - the class-shape hint (`methods` then `in` reveals callers via the wider graph)
"""
from pathlib import Path

from harness.types import Task

# Real TS repo. snapshot_repo() git-archives ~62MB tracked source.
# graphify-out is gitignored. Sandbox skips `pip install -e .` because
# zero-tvm has no pyproject.toml/setup.py — but graphify itself only
# reads source files, so this is fine (no npm install needed).
ZERO_TVM_REPO = Path("/Volumes/chonk/projects/zero-tvm")


def task() -> Task:
    return Task(
        task_id="seed_task_005_zerotvm_decode_engine_summary",
        repo_dir=ZERO_TVM_REPO,
        prompt=(
            "Write a 150-word summary of the `buildDecodeEngine` function (in "
            "`src/zero-tvm/engine-core.ts`) to a new file `DECODE_SUMMARY.md` in "
            "the repo root. Cover specifically:\n"
            "  (a) what the function builds and what it returns,\n"
            "  (b) the runtime / hardware target (e.g. what kind of device),\n"
            "  (c) the model architecture and the central decode-time operations "
            "the wired-up pipelines perform.\n\n"
            "Be concrete — name actual types, the hardware API, the model family, "
            "and the named transformer operations. Do not write a generic "
            "'this function builds an engine' summary."
        ),
        oracle_kind="file_contains_all",
        oracle_args={
            "path": "DECODE_SUMMARY.md",
            "substrings": [
                "buildDecodeEngine",
                # Hardware target: first parameter type, prominent in any
                # technically-accurate summary.
                "GPUDevice",
                # Model family: the codebase is Phi-3 (PHI3 constants, phi3v2.ts).
                # Most accurate summaries write "Phi-3" or "Phi3"; substring "Phi"
                # is the loosest reliable form.
                "Phi",
                # Core transformer operation; any summary of decode pipelines
                # mentions attention.
                "attention",
            ],
        },
        budget_tokens=200_000,
        budget_seconds=900,
    )
