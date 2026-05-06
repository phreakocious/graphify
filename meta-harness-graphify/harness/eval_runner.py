"""Drive one rollout: load candidate, set up sandbox, loop with Anthropic SDK,
capture metrics, run oracle."""
import json
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
    max_iterations: int = 50
    log_dir: Path = Path("runs")


SYSTEM_PROMPT_PREFIX = """You are an automated coding agent. You will be given a coding task to solve in a working repository. Use the available tools to read code, run commands, edit files, and verify your work. When you believe the task is complete, say so explicitly in your final message and stop calling tools.

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
    if target_snap.exists():
        import shutil
        shutil.rmtree(target_snap)
    snapshot_repo(task.repo_dir, target_snap)

    if sandbox_root.exists():
        import shutil
        shutil.rmtree(sandbox_root)

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

    # Bump retries to ride out transient 529 (overloaded) and 5xx without
    # losing the whole rollout. Default is 2; observed 529 storms during
    # multi-candidate compare runs.
    client = Anthropic(max_retries=6)
    ctx = ToolContext(repo_dir=sb.target_repo, venv_python=sb.venv_python)

    messages: list[dict] = [{"role": "user", "content": task.prompt}]
    tokens_input = tokens_output = tokens_cache_read = tokens_cache_write = 0
    n_api_calls = 0
    tool_calls: list[ToolCall] = []
    start = time.monotonic()
    final_text = ""
    oracle_result: OracleResult = OracleResult(passed=False, detail="rollout did not complete")

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
                    final_text = "".join(getattr(c, "text", "") for c in resp.content if getattr(c, "type", None) == "text")
                    break

                if resp.stop_reason != "tool_use":
                    final_text = f"[unexpected stop_reason: {resp.stop_reason}]"
                    break

                tool_results = []
                for block in resp.content:
                    if getattr(block, "type", None) != "tool_use":
                        continue
                    tool_name = block.name
                    args = dict(block.input) if block.input else {}
                    t0 = time.monotonic()
                    try:
                        result_str = TOOL_DISPATCH[tool_name](ctx, **args)
                    except Exception as e:
                        result_str = f"error: {type(e).__name__}: {e}"
                    duration_ms = int((time.monotonic() - t0) * 1000)
                    tool_calls.append(ToolCall(
                        name=tool_name,
                        arguments=args,
                        result_summary=_truncate(str(result_str)),
                        duration_ms=duration_ms,
                    ))
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(result_str),
                    })
                messages.append({"role": "user", "content": tool_results})
            else:
                final_text = f"[max_iterations={config.max_iterations} reached]"

            # Inject the sandbox python into pytest_passes if not already specified.
            oracle_args_with_venv = dict(task.oracle_args)
            if task.oracle_kind == "pytest_passes" and "python_exe" not in oracle_args_with_venv:
                oracle_args_with_venv["python_exe"] = str(sb.venv_python)
            oracle_result = run_oracle(
                kind=task.oracle_kind,
                repo_dir=sb.target_repo,
                args=oracle_args_with_venv,
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
            "oracle_detail": result.oracle.detail[:1500],
            "final_assistant_text": _truncate(final_text, 2000),
        }, indent=2))
        return result
    finally:
        sb.cleanup()
