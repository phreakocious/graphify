from pathlib import Path

from harness.metrics import (
    FAILURE_PENALTY_MULTIPLIER,
    aggregate_candidate_score,
    score_rollout,
)
from harness.types import OracleResult, RolloutResult


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
    assert score == (10_000 + 100_000 * FAILURE_PENALTY_MULTIPLIER) / 2
