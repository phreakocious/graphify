"""Post-hoc analysis: scan runs/ for all rollouts of a given task, group by
candidate, and report aggregates + tool-call distributions. Useful when a
compare run was interrupted or when you want fresh analysis without re-running.

Usage:
    python scripts/analyze_runs.py [--task seed_task_001] [--runs-dir runs]
"""
import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path


def parse_run_dir(run_dir: Path) -> dict | None:
    metrics_path = run_dir / "metrics.json"
    if not metrics_path.is_file():
        return None
    return json.loads(metrics_path.read_text())


def parse_tool_calls(transcript_path: Path) -> Counter:
    """Read transcript.jsonl and return a counter of (tool, verb) tuples
    where verb is the graphify subcommand if applicable, otherwise the tool name."""
    counts: Counter = Counter()
    if not transcript_path.is_file():
        return counts
    for line in transcript_path.read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        for block in event.get("content", []):
            if block.get("type") != "tool_use":
                continue
            tool_name = block.get("name", "?")
            args = block.get("input", {}) or {}
            if tool_name == "bash":
                cmd = args.get("command", "")
                # Pull out top-level verb: graphify <verb>, pytest, ls, cat, etc.
                m = re.match(r"^[\s;|&]*(\w+(?:-\w+)?)", cmd)
                top = m.group(1) if m else "bash:?"
                if top == "graphify":
                    m2 = re.match(r"^[\s;|&]*graphify\s+(\S+)", cmd)
                    sub = m2.group(1) if m2 else ""
                    counts[f"graphify {sub}"] += 1
                else:
                    counts[f"bash:{top}"] += 1
            else:
                counts[tool_name] += 1
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="seed_task_001_compute_score_median",
                    help="task name substring to match (default: seed_task_001)")
    ap.add_argument("--all-tasks", action="store_true",
                    help="produce a per-task table for every task with rollouts")
    ap.add_argument("--runs-dir", default="runs")
    args = ap.parse_args()

    runs_dir = Path(args.runs_dir)
    if not runs_dir.is_dir():
        print(f"no runs dir at {runs_dir}", file=sys.stderr)
        sys.exit(1)

    if args.all_tasks:
        # collect all task ids present in runs/
        seen_tasks = set()
        for run_dir in runs_dir.iterdir():
            if not run_dir.is_dir():
                continue
            metrics_path = run_dir / "metrics.json"
            if not metrics_path.is_file():
                continue
            try:
                d = json.loads(metrics_path.read_text())
                seen_tasks.add(d.get("task_id", ""))
            except Exception:
                continue
        for task in sorted(seen_tasks):
            args.task = task
            _print_task_table(runs_dir, task)
        return
    _print_task_table(runs_dir, args.task)


def _print_task_table(runs_dir: Path, task: str):
    rollouts: dict[str, list[dict]] = defaultdict(list)
    tool_counts: dict[str, Counter] = defaultdict(Counter)
    for run_dir in sorted(runs_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        if task not in run_dir.name:
            continue
        metrics = parse_run_dir(run_dir)
        if metrics is None:
            continue
        candidate = metrics["candidate_id"]
        rollouts[candidate].append(metrics)
        tool_counts[candidate].update(parse_tool_calls(run_dir / "transcript.jsonl"))

    if not rollouts:
        print(f"no rollouts found for task {task}", file=sys.stderr)
        return

    print(f"\n=== task: {task} ===\n")
    rows: list[tuple[str, dict]] = []
    for candidate, ms in sorted(rollouts.items()):
        ttcs = [m["tokens_to_completion"] for m in ms if m["tokens_to_completion"] > 0]
        passes = [m["passed"] for m in ms]
        if not ttcs:
            print(f"{candidate:32s}: no successful rollouts ({len(ms)} attempts)")
            continue
        agg = {
            "n": len(ms),
            "n_valid": len(ttcs),
            "pass_rate": sum(passes) / len(passes),
            "ttc_mean": round(statistics.mean(ttcs)),
            "ttc_stdev": round(statistics.stdev(ttcs), 1) if len(ttcs) > 1 else 0,
            "ttc_min": min(ttcs),
            "ttc_max": max(ttcs),
            "calls_mean": round(statistics.mean([m["n_api_calls"] for m in ms]), 1),
            "sec_mean": round(statistics.mean([m["wall_seconds"] for m in ms]), 1),
        }
        rows.append((candidate, agg))

    if not rows:
        return

    # baseline_navigator is the reference for ablations
    ref = "baseline_navigator"
    ref_row = next((r for c, r in rows if c == ref), None)
    if ref_row is None:
        ref_row = rows[0][1]
        ref = rows[0][0]

    print(f"{'candidate':<32s} {'n/N':>5} {'pass':>5} {'ttc mean':>9} {'(±stdev)':>10} {'min':>6} {'max':>6} {'calls':>6} {'sec':>5}  delta vs {ref}")
    print("-" * 130)
    ref_ttc = ref_row["ttc_mean"]
    for candidate, agg in rows:
        delta = agg["ttc_mean"] - ref_ttc
        pct = 100.0 * delta / ref_ttc if ref_ttc else 0.0
        marker = " (ref)" if candidate == ref else f"  {delta:+6d} ({pct:+5.1f}%)"
        print(
            f"{candidate:<32s} {agg['n_valid']:>2}/{agg['n']:<2} "
            f"{agg['pass_rate']:>5.2f} "
            f"{agg['ttc_mean']:>9d} ±{agg['ttc_stdev']:>8.1f} "
            f"{agg['ttc_min']:>6d} {agg['ttc_max']:>6d} "
            f"{agg['calls_mean']:>6.1f} {agg['sec_mean']:>5.1f}"
            f"{marker}"
        )

    # Tool usage breakdown
    print("\n=== tool/verb call counts (across all trials per candidate) ===\n")
    for candidate in sorted(tool_counts):
        cs = tool_counts[candidate]
        if not cs:
            continue
        top = cs.most_common(8)
        line = ", ".join(f"{name}={n}" for name, n in top)
        print(f"  {candidate}: {line}")


if __name__ == "__main__":
    main()
