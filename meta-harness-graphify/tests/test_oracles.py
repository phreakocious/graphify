from pathlib import Path

import pytest

from harness.oracles import all_of, file_contains, file_contains_all, pytest_passes, run_oracle

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
    with pytest.raises(ValueError, match="unknown oracle"):
        run_oracle(kind="banana", repo_dir=FIXTURES, args={})


def test_file_contains_all_passes_when_all_present():
    result = file_contains_all(
        repo_dir=FIXTURES,
        path="sample_file.txt",
        substrings=["quick brown", "hello"],
    )
    assert result.passed


def test_file_contains_all_fails_when_any_missing():
    result = file_contains_all(
        repo_dir=FIXTURES,
        path="sample_file.txt",
        substrings=["quick brown", "definitely-not-here"],
    )
    assert not result.passed
    assert "definitely-not-here" in result.detail


def test_file_contains_all_handles_missing_file():
    result = file_contains_all(
        repo_dir=FIXTURES,
        path="nonexistent.txt",
        substrings=["anything"],
    )
    assert not result.passed


def test_all_of_passes_when_every_step_passes():
    """Composite oracle for session/multi-step tasks. Pass = every
    sub-oracle passes."""
    result = all_of(
        repo_dir=FIXTURES,
        oracles=[
            {"kind": "file_contains", "args": {"path": "sample_file.txt", "substring": "hello"}},
            {"kind": "file_contains_all", "args": {"path": "sample_file.txt", "substrings": ["quick brown"]}},
        ],
    )
    assert result.passed
    # Per-step PASS markers in detail so logs surface what worked.
    assert "step 1" in result.detail and "PASS" in result.detail
    assert "step 2" in result.detail and "PASS" in result.detail


def test_all_of_fails_when_any_step_fails():
    """If any sub-oracle fails, the composite fails — but the detail
    still names which step(s) broke down."""
    result = all_of(
        repo_dir=FIXTURES,
        oracles=[
            {"kind": "file_contains", "args": {"path": "sample_file.txt", "substring": "hello"}},
            {"kind": "file_contains", "args": {"path": "sample_file.txt", "substring": "definitely-not-here"}},
        ],
    )
    assert not result.passed
    assert "step 2" in result.detail and "FAIL" in result.detail
    # Step 1 still ran and is reported as PASS — useful for diagnosing
    # where in the chain things broke.
    assert "step 1" in result.detail and "PASS" in result.detail


def test_all_of_dispatches_via_run_oracle():
    """all_of must be reachable via the kind-dispatch path so it can be
    declared as a Task's oracle_kind."""
    result = run_oracle(
        kind="all_of",
        repo_dir=FIXTURES,
        args={
            "oracles": [
                {"kind": "file_contains", "args": {"path": "sample_file.txt", "substring": "hello"}},
            ],
        },
    )
    assert result.passed
