"""Scoring: tokens-to-completion with failure penalty."""
from harness.types import RolloutResult

FAILURE_PENALTY_MULTIPLIER = 3


def score_rollout(rollout: RolloutResult, *, budget_tokens: int) -> float:
    if rollout.passed:
        return float(rollout.tokens_to_completion)
    return float(budget_tokens * FAILURE_PENALTY_MULTIPLIER)


def aggregate_candidate_score(
    rollouts: list[RolloutResult],
    *,
    budget_tokens: int,
) -> float:
    if not rollouts:
        raise ValueError("aggregate_candidate_score: empty rollouts")
    return sum(score_rollout(r, budget_tokens=budget_tokens) for r in rollouts) / len(rollouts)
