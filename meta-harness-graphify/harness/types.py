"""Core dataclasses for candidates, tasks, and rollout results."""
from dataclasses import dataclass
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
    oracle_kind: str
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
    result_summary: str
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
        return self.tokens_input + self.tokens_output + self.tokens_cache_write
