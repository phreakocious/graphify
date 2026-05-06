# Meta-Harness for Graphify Navigator — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phase 1 MVP — prove the loop works end-to-end. One baseline candidate solves one seed task in a sandboxed venv; we capture tokens-to-completion + an oracle pass/fail. No proposer, no search, no concurrency.

**Architecture:** Python project at `meta-harness-graphify/`. A `harness/` library handles sandbox + eval-loop + metrics + oracles. A `meta_harness.py --smoke` entry point runs one rollout. The eval agent uses the Anthropic SDK directly with a minimal Read/Edit/Write/Bash tool surface that mimics Claude Code's. The candidate harness is `(graphify overrides, SKILL.md)`; baseline candidate has empty overrides + a small default SKILL.md.

**Tech Stack:** Python 3.12, `anthropic` SDK, `pytest`, `click`, `pydantic` for typed config, standard library `subprocess`/`shutil`/`tempfile` for sandbox.

---

## File Structure

```
meta-harness-graphify/
  pyproject.toml                          # Task 1
  README.md                               # Task 1
  meta_harness.py                         # Task 9 — entry point
  agents/
    __init__.py
    baseline_navigator/                   # Task 7
      SKILL.md
      overrides/                          # empty for baseline
      metadata.json
  harness/
    __init__.py
    types.py                              # Task 2 — Candidate, Task, RolloutResult
    metrics.py                            # Task 3 — token accounting
    oracles.py                            # Task 4 — pytest_passes, file_contains
    sandbox.py                            # Task 5 — worktree + venv + install
    tools.py                              # Task 6 — Read/Edit/Write/Bash impls
    eval_runner.py                        # Task 6 — drive one rollout
  tasks/
    __init__.py
    fixture_repo_001/                     # Task 8 — tiny test target repo
      pyproject.toml
      src/mylib/__init__.py
      src/mylib/utils.py
      src/mylib/helpers.py
      ...
      tests/test_utils.py
    seed_task_001.py                      # Task 8 — task definition
  tests/
    test_metrics.py                       # Task 3
    test_oracles.py                       # Task 4
    test_sandbox.py                       # Task 5
    test_tools.py                         # Task 6
```

---

## Task 1: Project Scaffolding

**Files:**
- Create: `meta-harness-graphify/pyproject.toml`
- Create: `meta-harness-graphify/README.md`
- Create: `meta-harness-graphify/.gitignore`
- Create: `meta-harness-graphify/agents/__init__.py`
- Create: `meta-harness-graphify/harness/__init__.py`
- Create: `meta-harness-graphify/tasks/__init__.py`
- Create: `meta-harness-graphify/tests/__init__.py`

- [ ] **Step 1.1: Create pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "meta-harness-graphify"
version = "0.0.1"
description = "Meta-Harness adaptation for the graphify navigator. Searches over (graphify CLI behavior, SKILL.md) candidates to minimize Claude's tokens-to-completion on coding tasks."
requires-python = ">=3.12"
dependencies = [
    "anthropic>=0.40.0",
    "click>=8.1.0",
    "pydantic>=2.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]

[project.scripts]
mhg = "meta_harness:cli"

[tool.setuptools]
packages = ["harness", "agents", "tasks"]
```

- [ ] **Step 1.2: Create README.md**

```markdown
# meta-harness-graphify

Phase 1 MVP. See `docs/SPEC.md` for the design and `docs/PLAN.md` for the implementation plan.

## Quick start

```bash
uv sync
uv run python meta_harness.py --smoke
```

`--smoke` runs one rollout (baseline candidate × seed task × 1 trial) and prints metrics. Requires `ANTHROPIC_API_KEY`.
```

- [ ] **Step 1.3: Create .gitignore**

```
__pycache__/
*.pyc
*.egg-info/
build/
dist/
.venv/
.pytest_cache/
.mypy_cache/
runs/                                     # per-rollout artifacts (large, ephemeral)
candidates/*/overrides/                   # candidate snapshots (large)
!agents/baseline_navigator/overrides/     # baseline overrides ARE committed
```

- [ ] **Step 1.4: Create empty package `__init__.py` files for `harness/`, `agents/`, `tasks/`, `tests/`**

Each is just `""` as content (empty string, but the file exists).

- [ ] **Step 1.5: Verify the project installs cleanly**

```bash
cd meta-harness-graphify
uv sync
```

Expected: completes without error; `.venv/` exists.

- [ ] **Step 1.6: Commit**

```bash
git add meta-harness-graphify/
git commit -m "scaffold: meta-harness-graphify project (pyproject, dirs)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: Core Types

**Files:**
- Create: `meta-harness-graphify/harness/types.py`
- Create: `meta-harness-graphify/tests/test_types.py`

- [ ] **Step 2.1: Write the failing test**

```python
# tests/test_types.py
from pathlib import Path
from harness.types import Candidate, Task, RolloutResult, OracleResult, ToolCall


def test_candidate_has_required_fields():
    c = Candidate(
        candidate_id="baseline",
        overrides_dir=Path("/tmp/x/overrides"),
        skill_md_path=Path("/tmp/x/SKILL.md"),
        parent_id=None,
        generation=0,
    )
    assert c.candidate_id == "baseline"
    assert c.parent_id is None
    assert c.generation == 0


def test_task_has_required_fields():
    t = Task(
        task_id="seed_001",
        repo_dir=Path("/tmp/repo"),
        prompt="do the thing",
        oracle_kind="pytest_passes",
        oracle_args={"node_id": "tests/test_x.py::test_y"},
        budget_tokens=100_000,
        budget_seconds=1800,
    )
    assert t.task_id == "seed_001"
    assert t.budget_tokens == 100_000


def test_rollout_result_has_required_fields():
    r = RolloutResult(
        candidate_id="baseline",
        task_id="seed_001",
        trial=0,
        oracle=OracleResult(passed=True, detail="all green"),
        tokens_input=1000,
        tokens_output=500,
        tokens_cache_read=2000,
        tokens_cache_write=300,
        wall_seconds=42.0,
        n_api_calls=4,
        tool_calls=[],
        transcript_path=Path("/tmp/transcript.jsonl"),
    )
    assert r.passed
    assert r.tokens_to_completion == 1800  # input + output + cache_write (cache_read free)


def test_tokens_to_completion_with_failure_penalty():
    r = RolloutResult(
        candidate_id="baseline",
        task_id="seed_001",
        trial=0,
        oracle=OracleResult(passed=False, detail="test failed"),
        tokens_input=1000,
        tokens_output=500,
        tokens_cache_read=0,
        tokens_cache_write=0,
        wall_seconds=10.0,
        n_api_calls=2,
        tool_calls=[],
        transcript_path=Path("/tmp/t.jsonl"),
    )
    # Failure penalty is applied at scoring time, not stored — see metrics.py
    assert r.tokens_to_completion == 1500
    assert not r.passed
```

- [ ] **Step 2.2: Run test, confirm it fails**

```bash
cd meta-harness-graphify && uv run pytest tests/test_types.py -v
```

Expected: `ModuleNotFoundError: No module named 'harness.types'`

- [ ] **Step 2.3: Implement `harness/types.py`**

```python
"""Core dataclasses for candidates, tasks, and rollout results."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    overrides_dir: Path
    skill_md_path: Path
    parent_id: str | None
    generation: int


@dataclass(frozen=True)
class Task:
    task_id: str
    repo_dir: Path
    prompt: str
    oracle_kind: str               # "pytest_passes" | "file_contains" | ...
    oracle_args: dict[str, Any]
    budget_tokens: int
    budget_seconds: int


@dataclass(frozen=True)
class OracleResult:
    passed: bool
    detail: str


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    result_summary: str            # truncated for the log
    duration_ms: int


@dataclass(frozen=True)
class RolloutResult:
    candidate_id: str
    task_id: str
    trial: int
    oracle: OracleResult
    tokens_input: int
    tokens_output: int
    tokens_cache_read: int
    tokens_cache_write: int
    wall_seconds: float
    n_api_calls: int
    tool_calls: list[ToolCall]
    transcript_path: Path

    @property
    def passed(self) -> bool:
        return self.oracle.passed

    @property
    def tokens_to_completion(self) -> int:
        # Cache-read tokens are excluded — they are nearly free and don't reflect harness friction.
        return self.tokens_input + self.tokens_output + self.tokens_cache_write
```

- [ ] **Step 2.4: Run tests, confirm they pass**

```bash
cd meta-harness-graphify && uv run pytest tests/test_types.py -v
```

Expected: 4 passed.

- [ ] **Step 2.5: Commit**

```bash
git add meta-harness-graphify/harness/types.py meta-harness-graphify/tests/test_types.py
git commit -m "harness: core types (Candidate, Task, RolloutResult)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: Metrics — Score Aggregation

**Files:**
- Create: `meta-harness-graphify/harness/metrics.py`
- Create: `meta-harness-graphify/tests/test_metrics.py`

- [ ] **Step 3.1: Write the failing test**

```python
# tests/test_metrics.py
from pathlib import Path
from harness.types import RolloutResult, OracleResult
from harness.metrics import score_rollout, aggregate_candidate_score, FAILURE_PENALTY_MULTIPLIER


def _r(passed: bool, total_tokens: int) -> RolloutResult:
    return RolloutResult(
        candidate_id="x",
        task_id="t",
        trial=0,
        oracle=OracleResult(passed=passed, detail=""),
        tokens_input=total_tokens,
        tokens_output=0,
        tokens_cache_read=0,
        tokens_cache_write=0,
        wall_seconds=1.0,
        n_api_calls=1,
        tool_calls=[],
        transcript_path=Path("/tmp/t.jsonl"),
    )


def test_score_passes_returns_tokens_to_completion():
    r = _r(passed=True, total_tokens=10_000)
    assert score_rollout(r, budget_tokens=100_000) == 10_000


def test_score_fail_returns_penalized_budget():
    r = _r(passed=False, total_tokens=5_000)
    assert score_rollout(r, budget_tokens=100_000) == 100_000 * FAILURE_PENALTY_MULTIPLIER


def test_aggregate_takes_mean_of_per_task_scores():
    rollouts = [
        _r(True, 10_000),
        _r(True, 20_000),
    ]
    score = aggregate_candidate_score(rollouts, budget_tokens=100_000)
    assert score == 15_000


def test_aggregate_one_failure_dominates():
    rollouts = [
        _r(True, 10_000),
        _r(False, 5_000),
    ]
    score = aggregate_candidate_score(rollouts, budget_tokens=100_000)
    # mean of (10_000, 300_000) = 155_000
    assert score == (10_000 + 100_000 * FAILURE_PENALTY_MULTIPLIER) / 2
```

- [ ] **Step 3.2: Run test, confirm it fails**

```bash
cd meta-harness-graphify && uv run pytest tests/test_metrics.py -v
```

Expected: `ModuleNotFoundError: No module named 'harness.metrics'`

- [ ] **Step 3.3: Implement `harness/metrics.py`**

```python
"""Scoring: tokens-to-completion with failure penalty."""
from harness.types import RolloutResult

FAILURE_PENALTY_MULTIPLIER = 3


def score_rollout(rollout: RolloutResult, *, budget_tokens: int) -> float:
    """Score one rollout. Lower is better. Failed rollouts are penalized at
    FAILURE_PENALTY_MULTIPLIER × budget_tokens, regardless of how many tokens
    they actually consumed (so candidates can't game the metric by giving up
    early)."""
    if rollout.passed:
        return float(rollout.tokens_to_completion)
    return float(budget_tokens * FAILURE_PENALTY_MULTIPLIER)


def aggregate_candidate_score(
    rollouts: list[RolloutResult],
    *,
    budget_tokens: int,
) -> float:
    """Mean of per-rollout scores. Caller is responsible for grouping by task
    and trial as appropriate."""
    if not rollouts:
        raise ValueError("aggregate_candidate_score: empty rollouts")
    return sum(score_rollout(r, budget_tokens=budget_tokens) for r in rollouts) / len(rollouts)
```

- [ ] **Step 3.4: Run tests, confirm they pass**

```bash
cd meta-harness-graphify && uv run pytest tests/test_metrics.py -v
```

Expected: 4 passed.

- [ ] **Step 3.5: Commit**

```bash
git add meta-harness-graphify/harness/metrics.py meta-harness-graphify/tests/test_metrics.py
git commit -m "harness: scoring (tokens-to-completion + failure penalty)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 4: Oracles — `pytest_passes` and `file_contains`

**Files:**
- Create: `meta-harness-graphify/harness/oracles.py`
- Create: `meta-harness-graphify/tests/test_oracles.py`
- Create: `meta-harness-graphify/tests/fixtures/oracle/passing_test.py` (test fixture)
- Create: `meta-harness-graphify/tests/fixtures/oracle/failing_test.py` (test fixture)
- Create: `meta-harness-graphify/tests/fixtures/oracle/sample_file.txt` (test fixture)

- [ ] **Step 4.1: Create the test fixtures**

`tests/fixtures/oracle/passing_test.py`:
```python
def test_one():
    assert 1 == 1
```

`tests/fixtures/oracle/failing_test.py`:
```python
def test_two():
    assert 1 == 2
```

`tests/fixtures/oracle/sample_file.txt`:
```
hello world
the quick brown fox
```

- [ ] **Step 4.2: Write the failing test**

```python
# tests/test_oracles.py
from pathlib import Path
from harness.oracles import pytest_passes, file_contains, run_oracle

FIXTURES = Path(__file__).parent / "fixtures" / "oracle"


def test_pytest_passes_on_passing_test():
    result = pytest_passes(repo_dir=FIXTURES, node_id="passing_test.py::test_one")
    assert result.passed, result.detail


def test_pytest_passes_on_failing_test():
    result = pytest_passes(repo_dir=FIXTURES, node_id="failing_test.py::test_two")
    assert not result.passed
    assert "assert" in result.detail.lower() or "fail" in result.detail.lower()


def test_pytest_passes_on_missing_test():
    result = pytest_passes(repo_dir=FIXTURES, node_id="passing_test.py::test_does_not_exist")
    assert not result.passed


def test_file_contains_match():
    result = file_contains(
        repo_dir=FIXTURES,
        path="sample_file.txt",
        substring="quick brown",
    )
    assert result.passed


def test_file_contains_no_match():
    result = file_contains(
        repo_dir=FIXTURES,
        path="sample_file.txt",
        substring="not present",
    )
    assert not result.passed


def test_run_oracle_dispatches_by_kind():
    result = run_oracle(
        kind="file_contains",
        repo_dir=FIXTURES,
        args={"path": "sample_file.txt", "substring": "hello"},
    )
    assert result.passed


def test_run_oracle_unknown_kind_raises():
    import pytest
    with pytest.raises(ValueError, match="unknown oracle"):
        run_oracle(kind="banana", repo_dir=FIXTURES, args={})
```

- [ ] **Step 4.3: Run test, confirm it fails**

```bash
cd meta-harness-graphify && uv run pytest tests/test_oracles.py -v
```

Expected: `ModuleNotFoundError: No module named 'harness.oracles'`

- [ ] **Step 4.4: Implement `harness/oracles.py`**

```python
"""Task success oracles. Each oracle returns OracleResult(passed, detail)."""
import subprocess
from pathlib import Path
from typing import Any

from harness.types import OracleResult


def pytest_passes(*, repo_dir: Path, node_id: str, timeout: int = 120) -> OracleResult:
    """Pass iff `pytest <node_id>` returns 0 from inside repo_dir."""
    try:
        proc = subprocess.run(
            ["python", "-m", "pytest", node_id, "-x", "--tb=short", "-q"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        return OracleResult(passed=False, detail=f"pytest timeout after {timeout}s")
    detail = (proc.stdout or "") + (proc.stderr or "")
    return OracleResult(passed=proc.returncode == 0, detail=detail.strip()[-2000:])


def file_contains(*, repo_dir: Path, path: str, substring: str) -> OracleResult:
    """Pass iff the file at repo_dir/path exists and contains substring."""
    target = repo_dir / path
    if not target.is_file():
        return OracleResult(passed=False, detail=f"file not found: {target}")
    content = target.read_text(errors="replace")
    if substring in content:
        return OracleResult(passed=True, detail=f"matched at offset {content.index(substring)}")
    return OracleResult(passed=False, detail=f"substring not found in {target}")


_DISPATCH = {
    "pytest_passes": pytest_passes,
    "file_contains": file_contains,
}


def run_oracle(*, kind: str, repo_dir: Path, args: dict[str, Any]) -> OracleResult:
    if kind not in _DISPATCH:
        raise ValueError(f"unknown oracle kind: {kind}")
    return _DISPATCH[kind](repo_dir=repo_dir, **args)
```

- [ ] **Step 4.5: Run tests, confirm they pass**

```bash
cd meta-harness-graphify && uv run pytest tests/test_oracles.py -v
```

Expected: 7 passed.

- [ ] **Step 4.6: Commit**

```bash
git add meta-harness-graphify/harness/oracles.py meta-harness-graphify/tests/test_oracles.py meta-harness-graphify/tests/fixtures/
git commit -m "harness: pytest_passes and file_contains oracles

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 5: Sandbox — Per-Eval Worktree + Venv + Graphify Install

**Files:**
- Create: `meta-harness-graphify/harness/sandbox.py`
- Create: `meta-harness-graphify/tests/test_sandbox.py`

**Background.** Each rollout needs an isolated copy of the target repo and a per-eval venv with the *candidate's* graphify installed (overrides applied, no editable install). We use `git archive HEAD` to snapshot the target repo, and we build a candidate-graphify source tree by copying graphify's source then rsyncing the candidate's overrides on top, then `pip install` (non-editable).

- [ ] **Step 5.1: Write the failing test**

```python
# tests/test_sandbox.py
import subprocess
from pathlib import Path
import pytest
from harness.sandbox import (
    snapshot_repo,
    build_candidate_graphify_source,
    create_sandbox_venv,
    Sandbox,
)

GRAPHIFY_REPO = Path("/Volumes/chonk/projects/graphify")


def test_snapshot_repo_produces_clean_dir(tmp_path):
    out = tmp_path / "snap"
    snapshot_repo(GRAPHIFY_REPO, out)
    assert (out / "graphify" / "__init__.py").exists()
    assert not (out / ".git").exists()  # archive doesn't include .git


def test_build_candidate_graphify_source_with_no_overrides(tmp_path):
    overrides = tmp_path / "empty_overrides"
    overrides.mkdir()
    out = tmp_path / "candidate_src"
    build_candidate_graphify_source(
        graphify_src=GRAPHIFY_REPO,
        overrides_dir=overrides,
        dest=out,
    )
    assert (out / "graphify" / "__init__.py").exists()
    assert (out / "pyproject.toml").exists()


def test_build_candidate_graphify_source_applies_overrides(tmp_path):
    # Create an override that adds a marker line to graphify/__init__.py
    overrides = tmp_path / "ov"
    (overrides / "graphify").mkdir(parents=True)
    (overrides / "graphify" / "__init__.py").write_text("# OVERRIDDEN\n")
    out = tmp_path / "candidate_src"
    build_candidate_graphify_source(
        graphify_src=GRAPHIFY_REPO,
        overrides_dir=overrides,
        dest=out,
    )
    assert (out / "graphify" / "__init__.py").read_text() == "# OVERRIDDEN\n"


@pytest.mark.slow
def test_sandbox_end_to_end(tmp_path):
    """Integration test: build a sandbox with empty overrides, install graphify,
    verify `python -m graphify --help` works."""
    overrides = tmp_path / "ov"
    overrides.mkdir()
    target_repo_snap = tmp_path / "target_repo_snapshot"
    snapshot_repo(GRAPHIFY_REPO, target_repo_snap)

    sb = Sandbox.create(
        sandbox_root=tmp_path / "sb",
        target_repo_snapshot=target_repo_snap,
        graphify_src=GRAPHIFY_REPO,
        candidate_overrides=overrides,
    )
    try:
        # Verify graphify is importable from the sandbox venv
        proc = subprocess.run(
            [str(sb.venv_python), "-c", "import graphify; print(graphify.__file__)"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        # Should NOT resolve to the original graphify path (non-editable install)
        assert str(GRAPHIFY_REPO) not in proc.stdout
        assert str(sb.root) in proc.stdout
    finally:
        sb.cleanup()
```

- [ ] **Step 5.2: Run tests, confirm failure**

```bash
cd meta-harness-graphify && uv run pytest tests/test_sandbox.py -v
```

Expected: `ModuleNotFoundError: No module named 'harness.sandbox'`

- [ ] **Step 5.3: Implement `harness/sandbox.py`**

```python
"""Per-eval sandbox: target-repo snapshot + isolated venv with candidate's graphify."""
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path


def snapshot_repo(repo: Path, dest: Path) -> None:
    """Snapshot a git repo's HEAD into `dest` using `git archive`. No .git, no
    untracked files, no submodules. dest must not exist."""
    if dest.exists():
        raise FileExistsError(dest)
    dest.mkdir(parents=True)
    with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as tf:
        tar_path = Path(tf.name)
    try:
        subprocess.run(
            ["git", "archive", "--format=tar", "-o", str(tar_path), "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        with tarfile.open(tar_path) as tar:
            tar.extractall(dest)
    finally:
        tar_path.unlink(missing_ok=True)


def build_candidate_graphify_source(
    *,
    graphify_src: Path,
    overrides_dir: Path,
    dest: Path,
) -> None:
    """Copy graphify_src into dest, then overlay overrides_dir on top.
    `dest` must not exist."""
    if dest.exists():
        raise FileExistsError(dest)
    # Copy graphify source minus generated dirs.
    shutil.copytree(
        graphify_src,
        dest,
        ignore=shutil.ignore_patterns(
            ".git", ".venv", "venv", "__pycache__", "*.egg-info",
            "build", "dist", ".pytest_cache", ".mypy_cache",
            ".worktrees", "graphify-out", ".graphify",
            "meta-harness", "meta-harness-graphify", "worked",
        ),
    )
    # Overlay overrides.
    if overrides_dir.exists():
        for src in overrides_dir.rglob("*"):
            if src.is_file():
                rel = src.relative_to(overrides_dir)
                dst = dest / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)


def create_sandbox_venv(
    *,
    venv_dir: Path,
    candidate_graphify_src: Path,
) -> Path:
    """Create a venv at venv_dir and pip-install the candidate graphify (non-editable).
    Returns the path to the venv's python."""
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    venv_python = venv_dir / "bin" / "python"
    if not venv_python.exists():  # Windows fallback (we're on macOS, but defensive)
        venv_python = venv_dir / "Scripts" / "python.exe"
    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--quiet", "--upgrade", "pip"],
        check=True,
    )
    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--quiet", str(candidate_graphify_src)],
        check=True,
    )
    return venv_python


@dataclass
class Sandbox:
    root: Path
    target_repo: Path        # the snapshotted, mutable target repo (where the agent works)
    venv_dir: Path
    venv_python: Path
    candidate_graphify_src: Path

    @classmethod
    def create(
        cls,
        *,
        sandbox_root: Path,
        target_repo_snapshot: Path,
        graphify_src: Path,
        candidate_overrides: Path,
    ) -> "Sandbox":
        sandbox_root.mkdir(parents=True, exist_ok=False)
        target_repo = sandbox_root / "repo"
        shutil.copytree(target_repo_snapshot, target_repo)
        candidate_src = sandbox_root / "candidate_graphify"
        build_candidate_graphify_source(
            graphify_src=graphify_src,
            overrides_dir=candidate_overrides,
            dest=candidate_src,
        )
        venv_dir = sandbox_root / "venv"
        venv_python = create_sandbox_venv(
            venv_dir=venv_dir,
            candidate_graphify_src=candidate_src,
        )
        return cls(
            root=sandbox_root,
            target_repo=target_repo,
            venv_dir=venv_dir,
            venv_python=venv_python,
            candidate_graphify_src=candidate_src,
        )

    def cleanup(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)
```

- [ ] **Step 5.4: Configure pytest to recognize the slow marker**

Add to `meta-harness-graphify/pyproject.toml` after the `[project.scripts]` block:

```toml
[tool.pytest.ini_options]
markers = [
    "slow: marks tests as slow (run with -m slow)",
]
testpaths = ["tests"]
```

- [ ] **Step 5.5: Run fast tests, confirm pass**

```bash
cd meta-harness-graphify && uv run pytest tests/test_sandbox.py -v -m "not slow"
```

Expected: 3 passed (snapshot_repo, build_candidate with empty, build_candidate with overrides).

- [ ] **Step 5.6: Run slow integration test**

```bash
cd meta-harness-graphify && uv run pytest tests/test_sandbox.py::test_sandbox_end_to_end -v -m slow
```

Expected: PASS in ~30-60s. Verifies end-to-end venv creation and graphify install.

If this fails because graphify install times out: acceptable to extend timeout=120 in `create_sandbox_venv`'s subprocess calls. Do not bypass the install — the candidate must be the one running.

- [ ] **Step 5.7: Commit**

```bash
git add meta-harness-graphify/harness/sandbox.py meta-harness-graphify/tests/test_sandbox.py meta-harness-graphify/pyproject.toml
git commit -m "harness: sandbox (snapshot, override-overlay, venv install)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 6: Tools + Eval Runner

**Files:**
- Create: `meta-harness-graphify/harness/tools.py`
- Create: `meta-harness-graphify/harness/eval_runner.py`
- Create: `meta-harness-graphify/tests/test_tools.py`

**Background.** The eval agent (Claude) drives the task by emitting tool calls. We define minimal Read/Edit/Write/Bash tools that mimic Claude Code's contract. The eval runner sets up the SDK call, the system prompt (with `SKILL.md` as a cache breakpoint), the message loop, and metric capture.

The bash tool runs commands inside the sandbox's `target_repo` with the sandbox venv's Python on PATH so `python -m graphify` resolves to the candidate.

- [ ] **Step 6.1: Write tests for tools**

```python
# tests/test_tools.py
from pathlib import Path
from harness.tools import (
    ToolContext, tool_read, tool_write, tool_edit, tool_bash, TOOL_DEFINITIONS,
)


def _ctx(tmp_path: Path) -> ToolContext:
    venv_python = tmp_path / "venv_python"
    venv_python.write_text("# fake")
    return ToolContext(repo_dir=tmp_path / "repo", venv_python=venv_python)


def test_tool_read_returns_content(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.repo_dir.mkdir()
    (ctx.repo_dir / "f.txt").write_text("hello\nworld\n")
    result = tool_read(ctx, path="f.txt")
    assert "hello" in result
    assert "world" in result


def test_tool_read_missing_file_returns_error(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.repo_dir.mkdir()
    result = tool_read(ctx, path="missing.txt")
    assert "no such file" in result.lower() or "not found" in result.lower()


def test_tool_write_creates_file(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.repo_dir.mkdir()
    tool_write(ctx, path="new.txt", content="hi")
    assert (ctx.repo_dir / "new.txt").read_text() == "hi"


def test_tool_edit_str_replace(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.repo_dir.mkdir()
    (ctx.repo_dir / "f.txt").write_text("foo bar baz")
    tool_edit(ctx, path="f.txt", old_string="bar", new_string="QUX")
    assert (ctx.repo_dir / "f.txt").read_text() == "foo QUX baz"


def test_tool_edit_old_string_not_found_errors(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.repo_dir.mkdir()
    (ctx.repo_dir / "f.txt").write_text("foo")
    result = tool_edit(ctx, path="f.txt", old_string="xyz", new_string="abc")
    assert "not found" in result.lower() or "no match" in result.lower()


def test_tool_bash_runs_in_repo(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.repo_dir.mkdir()
    (ctx.repo_dir / "marker").write_text("here")
    result = tool_bash(ctx, command="ls")
    assert "marker" in result


def test_tool_definitions_include_all_four():
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert names == {"read_file", "write_file", "edit_file", "bash"}
```

- [ ] **Step 6.2: Run tests, confirm fail**

```bash
cd meta-harness-graphify && uv run pytest tests/test_tools.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 6.3: Implement `harness/tools.py`**

```python
"""Minimal tool surface mimicking Claude Code's Read/Edit/Write/Bash contracts."""
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ToolContext:
    repo_dir: Path
    venv_python: Path        # path to sandbox venv's python (so `graphify` resolves correctly)


TOOL_DEFINITIONS = [
    {
        "name": "read_file",
        "description": "Read a UTF-8 text file from the working repo. Returns up to 2000 lines from the start. Use to inspect specific files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Repo-relative path"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write a file. Overwrites if it exists.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace exactly one occurrence of `old_string` with `new_string` in the file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "bash",
        "description": "Run a shell command in the working repo. Use this to invoke `graphify`, run tests, grep, etc. The sandbox's python is on PATH first; `graphify` resolves to the candidate harness.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
            },
            "required": ["command"],
        },
    },
]


def _resolve(ctx: ToolContext, path: str) -> Path:
    p = (ctx.repo_dir / path).resolve()
    if ctx.repo_dir.resolve() not in p.parents and p != ctx.repo_dir.resolve():
        raise ValueError(f"path escapes repo_dir: {path}")
    return p


def tool_read(ctx: ToolContext, *, path: str, max_lines: int = 2000) -> str:
    try:
        p = _resolve(ctx, path)
    except ValueError as e:
        return f"error: {e}"
    if not p.is_file():
        return f"error: file not found: {path}"
    lines = p.read_text(errors="replace").splitlines()
    truncated = len(lines) > max_lines
    body = "\n".join(f"{i+1:6d}\t{ln}" for i, ln in enumerate(lines[:max_lines]))
    if truncated:
        body += f"\n... ({len(lines) - max_lines} more lines truncated)"
    return body


def tool_write(ctx: ToolContext, *, path: str, content: str) -> str:
    p = _resolve(ctx, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"wrote {len(content)} bytes to {path}"


def tool_edit(ctx: ToolContext, *, path: str, old_string: str, new_string: str) -> str:
    p = _resolve(ctx, path)
    if not p.is_file():
        return f"error: file not found: {path}"
    text = p.read_text(errors="replace")
    n = text.count(old_string)
    if n == 0:
        return f"error: old_string not found in {path}"
    if n > 1:
        return f"error: old_string matches {n} times in {path}; need a unique match"
    p.write_text(text.replace(old_string, new_string, 1))
    return f"edited {path}"


def tool_bash(ctx: ToolContext, *, command: str, timeout: int = 120) -> str:
    env = os.environ.copy()
    venv_bin = str(ctx.venv_python.parent)
    env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")
    env["VIRTUAL_ENV"] = str(ctx.venv_python.parent.parent)
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=ctx.repo_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return f"error: command timed out after {timeout}s"
    out = (proc.stdout or "")[-8000:]
    err = (proc.stderr or "")[-2000:]
    parts = [f"exit={proc.returncode}"]
    if out:
        parts.append(f"stdout:\n{out}")
    if err:
        parts.append(f"stderr:\n{err}")
    return "\n".join(parts)


TOOL_DISPATCH = {
    "read_file": tool_read,
    "write_file": tool_write,
    "edit_file": tool_edit,
    "bash": tool_bash,
}
```

**Note on the `_resolve` test:** the existing `test_tool_read_missing_file_returns_error` will pass because `_resolve` doesn't reject missing files, only path-escapes. Path-escape rejection is implicit; we don't write a unit test for it in MVP.

- [ ] **Step 6.4: Run tools tests, confirm pass**

```bash
cd meta-harness-graphify && uv run pytest tests/test_tools.py -v
```

Expected: 7 passed.

- [ ] **Step 6.5: Implement `harness/eval_runner.py`**

```python
"""Drive one rollout: load candidate, set up sandbox, loop with Anthropic SDK,
capture metrics, run oracle."""
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from anthropic import Anthropic

from harness.oracles import run_oracle
from harness.sandbox import Sandbox, snapshot_repo
from harness.tools import TOOL_DEFINITIONS, TOOL_DISPATCH, ToolContext
from harness.types import Candidate, OracleResult, RolloutResult, Task, ToolCall


@dataclass
class RunnerConfig:
    model: str = "claude-opus-4-7"
    max_iterations: int = 50         # max API calls per rollout
    log_dir: Path = Path("runs")


SYSTEM_PROMPT_PREFIX = """You are an automated coding agent. You will be given a coding task to solve in a working repository. Use the available tools to read code, run commands, edit files, and verify your work. When you believe the task is complete, say so explicitly in your final message.

You have these tools:
- read_file(path): inspect a file
- write_file(path, content): write/overwrite a file
- edit_file(path, old_string, new_string): replace exactly one occurrence
- bash(command): run a shell command (use this for `graphify`, tests, grep, ls, etc.)

Be efficient with context. Prefer targeted reads to broad exploration.
"""


def _truncate(s: str, n: int = 500) -> str:
    return s if len(s) <= n else s[:n] + f"... [truncated {len(s) - n} chars]"


def run_rollout(
    *,
    candidate: Candidate,
    task: Task,
    trial: int,
    graphify_src: Path,
    config: RunnerConfig | None = None,
) -> RolloutResult:
    config = config or RunnerConfig()
    rollout_id = f"{candidate.candidate_id}__{task.task_id}__t{trial}"
    log_root = config.log_dir / rollout_id
    log_root.mkdir(parents=True, exist_ok=True)
    transcript_path = log_root / "transcript.jsonl"
    sandbox_root = log_root / "sandbox"

    target_snap = log_root / "target_repo_snapshot"
    snapshot_repo(task.repo_dir, target_snap)

    sb = Sandbox.create(
        sandbox_root=sandbox_root,
        target_repo_snapshot=target_snap,
        graphify_src=graphify_src,
        candidate_overrides=candidate.overrides_dir,
    )

    skill_text = candidate.skill_md_path.read_text() if candidate.skill_md_path.is_file() else ""
    system_blocks = [
        {"type": "text", "text": SYSTEM_PROMPT_PREFIX},
        {"type": "text", "text": skill_text, "cache_control": {"type": "ephemeral"}},
    ]

    client = Anthropic()  # picks up ANTHROPIC_API_KEY
    ctx = ToolContext(repo_dir=sb.target_repo, venv_python=sb.venv_python)

    messages: list[dict] = [{"role": "user", "content": task.prompt}]
    tokens_input = tokens_output = tokens_cache_read = tokens_cache_write = 0
    n_api_calls = 0
    tool_calls: list[ToolCall] = []
    start = time.monotonic()
    final_text = ""
    oracle_result: OracleResult = OracleResult(passed=False, detail="rollout did not complete")
    wall = 0.0

    try:
        try:
            for iteration in range(config.max_iterations):
                elapsed = time.monotonic() - start
                if elapsed > task.budget_seconds:
                    final_text = f"[budget_seconds exceeded at iter {iteration}]"
                    break
                total_tokens = tokens_input + tokens_output + tokens_cache_write
                if total_tokens > task.budget_tokens:
                    final_text = f"[budget_tokens exceeded at iter {iteration}]"
                    break

                resp = client.messages.create(
                    model=config.model,
                    max_tokens=4096,
                    system=system_blocks,
                    tools=TOOL_DEFINITIONS,
                    messages=messages,
                )
                n_api_calls += 1
                usage = resp.usage
                tokens_input += getattr(usage, "input_tokens", 0)
                tokens_output += getattr(usage, "output_tokens", 0)
                tokens_cache_read += getattr(usage, "cache_read_input_tokens", 0) or 0
                tokens_cache_write += getattr(usage, "cache_creation_input_tokens", 0) or 0

                with transcript_path.open("a") as f:
                    f.write(json.dumps({
                        "iter": iteration,
                        "stop_reason": resp.stop_reason,
                        "usage": {
                            "input": getattr(usage, "input_tokens", 0),
                            "output": getattr(usage, "output_tokens", 0),
                            "cache_read": tokens_cache_read,
                            "cache_write": tokens_cache_write,
                        },
                        "content": [c.model_dump() for c in resp.content],
                    }) + "\n")

                messages.append({"role": "assistant", "content": [c.model_dump() for c in resp.content]})

                if resp.stop_reason == "end_turn":
                    final_text = "".join(c.text for c in resp.content if getattr(c, "type", None) == "text")
                    break

                if resp.stop_reason != "tool_use":
                    final_text = f"[unexpected stop_reason: {resp.stop_reason}]"
                    break

                tool_results = []
                for block in resp.content:
                    if getattr(block, "type", None) != "tool_use":
                        continue
                    tool_name = block.name
                    args = block.input
                    t0 = time.monotonic()
                    try:
                        result_str = TOOL_DISPATCH[tool_name](ctx, **args)
                    except Exception as e:
                        result_str = f"error: {type(e).__name__}: {e}"
                    duration_ms = int((time.monotonic() - t0) * 1000)
                    tool_calls.append(ToolCall(
                        name=tool_name,
                        arguments=dict(args),
                        result_summary=_truncate(str(result_str)),
                        duration_ms=duration_ms,
                    ))
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_str,
                    })
                messages.append({"role": "user", "content": tool_results})
            else:
                final_text = f"[max_iterations={config.max_iterations} reached]"

            # Run oracle on the post-rollout repo state.
            oracle_result = run_oracle(
                kind=task.oracle_kind,
                repo_dir=sb.target_repo,
                args=task.oracle_args,
            )
        except Exception as e:
            oracle_result = OracleResult(passed=False, detail=f"rollout error: {type(e).__name__}: {e}")
        wall = time.monotonic() - start

        result = RolloutResult(
            candidate_id=candidate.candidate_id,
            task_id=task.task_id,
            trial=trial,
            oracle=oracle_result,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            tokens_cache_read=tokens_cache_read,
            tokens_cache_write=tokens_cache_write,
            wall_seconds=wall,
            n_api_calls=n_api_calls,
            tool_calls=tool_calls,
            transcript_path=transcript_path,
        )
        # Write a summary alongside the transcript.
        (log_root / "metrics.json").write_text(json.dumps({
            "candidate_id": result.candidate_id,
            "task_id": result.task_id,
            "trial": result.trial,
            "passed": result.passed,
            "tokens_to_completion": result.tokens_to_completion,
            "tokens_input": result.tokens_input,
            "tokens_output": result.tokens_output,
            "tokens_cache_read": result.tokens_cache_read,
            "tokens_cache_write": result.tokens_cache_write,
            "wall_seconds": result.wall_seconds,
            "n_api_calls": result.n_api_calls,
            "n_tool_calls": len(result.tool_calls),
            "oracle_detail": result.oracle.detail,
            "final_assistant_text": _truncate(final_text, 2000),
        }, indent=2))
        return result
    finally:
        sb.cleanup()
```

- [ ] **Step 6.6: Commit (no separate test for eval_runner — exercised by smoke test in Task 10)**

```bash
git add meta-harness-graphify/harness/tools.py meta-harness-graphify/harness/eval_runner.py meta-harness-graphify/tests/test_tools.py
git commit -m "harness: tools (Read/Edit/Write/Bash) and eval_runner (Anthropic loop)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 7: Baseline Candidate

**Files:**
- Create: `meta-harness-graphify/agents/baseline_navigator/SKILL.md`
- Create: `meta-harness-graphify/agents/baseline_navigator/metadata.json`
- Create: `meta-harness-graphify/agents/baseline_navigator/overrides/.gitkeep` (empty file so dir is tracked)

- [ ] **Step 7.1: Author the baseline SKILL.md**

```markdown
# Graphify Navigator (Baseline)

You have access to `graphify`, a CLI that exposes a queryable knowledge graph of the working repository's code. Use it as your primary entry point for code navigation — prefer it over Read/Grep when you need to:

- Find a function/class/method by name → `graphify navigate <name>`
- Walk callers/callees of a symbol → `graphify navigate <name> --edges callers` / `--edges calls`
- See where a file's symbols are imported → `graphify navigate <path> --imports`
- Search the graph for a pattern → `graphify search <query>`

**First-call hygiene:** before navigating, run `graphify update` once if the graph might be stale. The command is fast.

**When to use Read/Grep instead:** when graphify returns no match, when you need to see exact line content of a file you've already located, or when navigating non-code (markdown, configs, data).

**Reading graphify output:** entries are ranked by relevance. The first entry is usually the right one. Hints at the bottom (`→ try this next`) point to follow-up calls that are often higher value than re-running with different args.
```

- [ ] **Step 7.2: Author metadata.json**

```json
{
  "candidate_id": "baseline_navigator",
  "parent_id": null,
  "generation": 0,
  "proposer_notes": "Phase 1 baseline. Empty overrides — uses graphify navigator's current state on the navigator branch as-is. SKILL.md is a minimal hand-written prior.",
  "created_at": "2026-05-05"
}
```

- [ ] **Step 7.3: Create empty overrides directory marker**

```bash
mkdir -p meta-harness-graphify/agents/baseline_navigator/overrides
touch meta-harness-graphify/agents/baseline_navigator/overrides/.gitkeep
```

- [ ] **Step 7.4: Commit**

```bash
git add meta-harness-graphify/agents/baseline_navigator/
git commit -m "agents: baseline_navigator candidate (empty overrides + minimal skill)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 8: Seed Task

**Files:**
- Create: `meta-harness-graphify/tasks/fixture_repo_001/pyproject.toml`
- Create: `meta-harness-graphify/tasks/fixture_repo_001/src/mylib/__init__.py`
- Create: `meta-harness-graphify/tasks/fixture_repo_001/src/mylib/utils.py`
- Create: `meta-harness-graphify/tasks/fixture_repo_001/src/mylib/helpers.py`
- Create: `meta-harness-graphify/tasks/fixture_repo_001/src/mylib/parsers.py`
- Create: `meta-harness-graphify/tasks/fixture_repo_001/src/mylib/formatters.py`
- Create: `meta-harness-graphify/tasks/fixture_repo_001/tests/test_utils.py`
- Create: `meta-harness-graphify/tasks/fixture_repo_001/.gitignore`
- Create: `meta-harness-graphify/tasks/seed_task_001.py`

**Background.** Fixture is a small Python package with several modules. The agent must find one specific function (`compute_score`) which lives in `utils.py` among 4 modules totaling ~15 functions, modify it to satisfy a failing test. Realistic enough that navigation matters; small enough to be fast.

The fixture repo is itself a git repo (initialized once) so `git archive` works for snapshotting.

- [ ] **Step 8.1: Create the fixture's `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "mylib"
version = "0.0.1"
requires-python = ">=3.10"

[tool.setuptools]
package-dir = {"" = "src"}
packages = ["mylib"]
```

- [ ] **Step 8.2: Create `src/mylib/__init__.py`**

```python
from mylib.utils import compute_score, normalize, summarize
from mylib.helpers import format_label, parse_tag
from mylib.parsers import parse_record, parse_header
from mylib.formatters import to_json, to_text

__all__ = [
    "compute_score", "normalize", "summarize",
    "format_label", "parse_tag",
    "parse_record", "parse_header",
    "to_json", "to_text",
]
```

- [ ] **Step 8.3: Create `src/mylib/utils.py` (contains the function the agent must edit)**

```python
"""General utilities."""


def normalize(value: float, lo: float, hi: float) -> float:
    """Linearly map value in [lo, hi] to [0, 1]."""
    if hi == lo:
        return 0.0
    return (value - lo) / (hi - lo)


def summarize(items: list[float]) -> dict:
    """Mean, min, max."""
    if not items:
        return {"n": 0, "mean": 0.0, "min": 0.0, "max": 0.0}
    return {
        "n": len(items),
        "mean": sum(items) / len(items),
        "min": min(items),
        "max": max(items),
    }


def compute_score(values: list[float]) -> float:
    """Compute a score from a list of values.

    Currently returns the mean. Tests expect this to return the *median*.
    """
    if not values:
        return 0.0
    return sum(values) / len(values)
```

- [ ] **Step 8.4: Create `src/mylib/helpers.py`**

```python
"""Label and tag helpers."""


def format_label(prefix: str, name: str) -> str:
    return f"[{prefix}] {name}"


def parse_tag(tag: str) -> tuple[str, str]:
    if ":" not in tag:
        return ("", tag)
    k, v = tag.split(":", 1)
    return (k.strip(), v.strip())
```

- [ ] **Step 8.5: Create `src/mylib/parsers.py`**

```python
"""Record parsers."""


def parse_record(line: str) -> dict:
    parts = line.split(",")
    if len(parts) < 2:
        return {}
    return {"key": parts[0].strip(), "value": ",".join(parts[1:]).strip()}


def parse_header(line: str) -> list[str]:
    return [c.strip() for c in line.split(",") if c.strip()]
```

- [ ] **Step 8.6: Create `src/mylib/formatters.py`**

```python
"""Output formatters."""
import json as _json


def to_json(obj) -> str:
    return _json.dumps(obj)


def to_text(obj) -> str:
    if isinstance(obj, dict):
        return "\n".join(f"{k}: {v}" for k, v in obj.items())
    return str(obj)
```

- [ ] **Step 8.7: Create the failing test `tests/test_utils.py`**

```python
from mylib.utils import compute_score


def test_compute_score_returns_median_of_odd_length():
    assert compute_score([1.0, 2.0, 3.0]) == 2.0


def test_compute_score_returns_median_of_even_length():
    assert compute_score([1.0, 2.0, 3.0, 4.0]) == 2.5


def test_compute_score_empty_list_returns_zero():
    assert compute_score([]) == 0.0
```

- [ ] **Step 8.8: Create `.gitignore` for the fixture**

```
__pycache__/
*.pyc
*.egg-info/
.pytest_cache/
```

- [ ] **Step 8.9: Initialize the fixture as a git repo**

```bash
cd meta-harness-graphify/tasks/fixture_repo_001
git init -q
git add .
git -c user.name=fixture -c user.email=fixture@example.com commit -q -m "fixture: initial state with failing compute_score test"
cd -
```

This is a *nested* git repo. The outer graphify repo does NOT track the fixture's `.git`. Add the fixture's `.git/` to the outer .gitignore so it's not picked up.

Add to `meta-harness-graphify/.gitignore`:
```
tasks/fixture_repo_001/.git/
```

- [ ] **Step 8.10: Create `tasks/seed_task_001.py`**

```python
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
            "in `tests/test_utils.py`. Run the test to see what's failing, find the "
            "function that needs to change, edit it so the test passes, then "
            "verify by running the test again. The package source is under `src/`."
        ),
        oracle_kind="pytest_passes",
        oracle_args={"node_id": "tests/test_utils.py"},
        budget_tokens=200_000,
        budget_seconds=900,
    )
```

- [ ] **Step 8.11: Verify the failing test does fail (sanity check before agent runs)**

```bash
cd meta-harness-graphify/tasks/fixture_repo_001
python -m pip install -e . --quiet
python -m pytest tests/test_utils.py -v
```

Expected: 3 failures (current `compute_score` returns mean, not median).

- [ ] **Step 8.12: Commit**

```bash
git add meta-harness-graphify/tasks/ meta-harness-graphify/.gitignore
git commit -m "tasks: seed_task_001 (compute_score mean→median in mylib fixture)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 9: `meta_harness.py --smoke` Entry Point

**Files:**
- Create: `meta-harness-graphify/meta_harness.py`

- [ ] **Step 9.1: Implement the entry point**

```python
"""Meta-harness CLI. Phase 1 supports `--smoke` only: one rollout (baseline ×
seed task × trial 0). Future phases add the search loop, multi-task evaluator,
and frontier management."""
import json
import os
import sys
from pathlib import Path

import click

from harness.eval_runner import RunnerConfig, run_rollout
from harness.types import Candidate

ROOT = Path(__file__).parent
GRAPHIFY_SRC = Path("/Volumes/chonk/projects/graphify")


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
    """Meta-harness for graphify navigator."""


@cli.command()
@click.option("--model", default="claude-opus-4-7", show_default=True)
@click.option("--max-iterations", default=50, show_default=True)
@click.option("--graphify-src", default=str(GRAPHIFY_SRC), show_default=True)
def smoke(model: str, max_iterations: int, graphify_src: str):
    """Run one rollout: baseline candidate × seed task × trial 0."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        click.echo("error: ANTHROPIC_API_KEY not set", err=True)
        sys.exit(1)
    candidate = _load_baseline()
    task = _load_seed_task()
    config = RunnerConfig(model=model, max_iterations=max_iterations, log_dir=ROOT / "runs")
    click.echo(f"smoke run: {candidate.candidate_id} × {task.task_id}")
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
```

- [ ] **Step 9.2: Commit**

```bash
git add meta-harness-graphify/meta_harness.py
git commit -m "cli: meta_harness.py --smoke entry point

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 10: End-to-End Smoke Test

**Files:**
- No new files; verifies the loop works.

- [ ] **Step 10.1: Confirm `ANTHROPIC_API_KEY` is set**

```bash
test -n "$ANTHROPIC_API_KEY" && echo "key present" || echo "MISSING KEY"
```

If missing: don't proceed. Exit task; the user will set it before running.

- [ ] **Step 10.2: Run the smoke test**

```bash
cd meta-harness-graphify
uv sync
uv run python meta_harness.py smoke --max-iterations 30
```

Expected (in the success case):
- ~5-15 API calls
- `passed: true` (compute_score change made test pass)
- `tokens_to_completion`: a few tens of thousands
- `wall_seconds`: 30-180s
- A `runs/baseline_navigator__seed_task_001_compute_score_median__t0/` directory with `transcript.jsonl`, `metrics.json`, and `sandbox/` (deleted after cleanup; only `transcript.jsonl` and `metrics.json` persist)

- [ ] **Step 10.3: Verify artifacts**

```bash
ls meta-harness-graphify/runs/baseline_navigator*/
cat meta-harness-graphify/runs/baseline_navigator*/metrics.json
head -2 meta-harness-graphify/runs/baseline_navigator*/transcript.jsonl
```

Expected: `metrics.json` has `passed: true` and a token count; `transcript.jsonl` has at least one valid JSON line.

- [ ] **Step 10.4: If smoke fails, debug and fix in place**

Common failure modes and fixes:
- **`anthropic.AuthenticationError`** → wrong/missing key
- **`pytest_passes` always returns false** → the sandbox `python` isn't the venv python; check `tool_bash` PATH manipulation
- **Agent loops without ever calling `bash`** → SKILL.md is too vague; tighten its first paragraph
- **Sandbox install fails** → graphify's pyproject has tree-sitter deps that may need build tools; `uv sync` in fixture should handle, but if it doesn't, add `--no-deps` to the install (tradeoff: graphify's runtime imports may fail). Note any workaround in commit.

If a fix is needed: edit, re-run, repeat.

- [ ] **Step 10.5: Commit any fixes**

```bash
git add -u
git commit -m "fix: smoke-test issues from end-to-end run

<one-line description of what broke and was fixed>

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

- [ ] **Step 10.6: Snapshot final smoke output**

Save a copy of the final smoke run's `metrics.json` to `meta-harness-graphify/docs/smoke_baseline_metrics.json` for reference. This becomes the baseline number that future candidates must beat.

```bash
cp meta-harness-graphify/runs/baseline_navigator*/metrics.json meta-harness-graphify/docs/smoke_baseline_metrics.json
git add meta-harness-graphify/docs/smoke_baseline_metrics.json
git commit -m "docs: snapshot baseline smoke metrics

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Self-Review Checklist (executed by the implementer at end)

- [ ] Spec coverage: every Phase 1 SPEC bullet has a task above
- [ ] No `TODO`/`TBD`/placeholder strings remain in plan or code
- [ ] Type names match across tasks (Candidate, Task, RolloutResult, OracleResult, ToolCall, ToolContext, RunnerConfig, Sandbox)
- [ ] All commits are atomic and follow the repo's existing message style
- [ ] Every test is concrete code, not "write a test for X"
- [ ] No fix is made by skipping a hook or `--no-verify`
