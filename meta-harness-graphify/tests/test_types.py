from pathlib import Path

from harness.types import Candidate, OracleResult, RolloutResult, Task


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


def test_rollout_result_tokens_to_completion_excludes_cache_read():
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
    assert r.tokens_to_completion == 1800


def test_rollout_result_failure_path():
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
    assert r.tokens_to_completion == 1500
    assert not r.passed
