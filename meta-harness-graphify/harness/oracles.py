"""Task success oracles. Each oracle returns OracleResult(passed, detail)."""
import subprocess
import sys
from pathlib import Path
from typing import Any

from harness.types import OracleResult


def pytest_passes(
    *,
    repo_dir: Path,
    node_id: str,
    timeout: int = 120,
    python_exe: str | None = None,
) -> OracleResult:
    """Pass iff `<python_exe> -m pytest <node_id>` returns 0 from inside repo_dir.

    `python_exe` defaults to sys.executable. The eval runner overrides this
    with the sandbox venv's python so pytest sees the candidate-installed
    packages.
    """
    py = python_exe or sys.executable
    try:
        proc = subprocess.run(
            [py, "-m", "pytest", node_id, "-x", "--tb=short", "-q"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return OracleResult(passed=False, detail=f"pytest timeout after {timeout}s")
    detail = (proc.stdout or "") + (proc.stderr or "")
    return OracleResult(passed=proc.returncode == 0, detail=detail.strip()[-2000:])


def file_contains(*, repo_dir: Path, path: str, substring: str) -> OracleResult:
    target = repo_dir / path
    if not target.is_file():
        return OracleResult(passed=False, detail=f"file not found: {target}")
    content = target.read_text(errors="replace")
    if substring in content:
        return OracleResult(passed=True, detail=f"matched at offset {content.index(substring)}")
    return OracleResult(passed=False, detail=f"substring not found in {target}")


def file_contains_all(*, repo_dir: Path, path: str, substrings: list[str]) -> OracleResult:
    """Pass iff all substrings appear in the file. Used for tasks where the
    agent must produce a list of items (e.g. all callers of X) — the oracle
    confirms each required item appears, in any order, anywhere in the file."""
    target = repo_dir / path
    if not target.is_file():
        return OracleResult(passed=False, detail=f"file not found: {target}")
    content = target.read_text(errors="replace")
    missing = [s for s in substrings if s not in content]
    if not missing:
        return OracleResult(passed=True, detail=f"all {len(substrings)} substrings found")
    return OracleResult(
        passed=False,
        detail=f"missing {len(missing)}/{len(substrings)}: {missing[:5]}",
    )


_DISPATCH = {
    "pytest_passes": pytest_passes,
    "file_contains": file_contains,
    "file_contains_all": file_contains_all,
}


def run_oracle(*, kind: str, repo_dir: Path, args: dict[str, Any]) -> OracleResult:
    if kind not in _DISPATCH:
        raise ValueError(f"unknown oracle kind: {kind}")
    return _DISPATCH[kind](repo_dir=repo_dir, **args)
