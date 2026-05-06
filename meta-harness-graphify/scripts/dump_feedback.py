"""Print all agent friction feedback collected across runs/, grouped by
candidate and task. Skim for recurring themes — verbs that don't exist,
output that's confusing, defaults that bite.

Usage:
    python scripts/dump_feedback.py [--task <id>] [--candidate <id>]

Without filters, prints every non-empty feedback entry across all
rollouts. Use grep / less to narrow further.
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default=None, help="match candidate runs containing this task id")
    ap.add_argument("--candidate", default=None, help="match a specific candidate id")
    ap.add_argument("--runs-dir", default="runs")
    ap.add_argument("--keywords", action="store_true",
                    help="print top recurring keywords across all feedback")
    args = ap.parse_args()

    runs_dir = Path(args.runs_dir)
    if not runs_dir.is_dir():
        print(f"no runs dir at {runs_dir}", file=sys.stderr)
        sys.exit(1)

    by_key: dict[tuple[str, str], list[tuple[int, str]]] = defaultdict(list)
    all_feedback: list[str] = []

    for run_dir in sorted(runs_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        metrics_path = run_dir / "metrics.json"
        if not metrics_path.is_file():
            continue
        try:
            d = json.loads(metrics_path.read_text())
        except Exception:
            continue
        feedback = d.get("agent_feedback", "").strip()
        if not feedback:
            continue
        if feedback.lower() in ("no friction", "no friction.", "[no friction]"):
            continue
        candidate = d.get("candidate_id", "?")
        task = d.get("task_id", "?")
        trial = d.get("trial", -1)
        if args.task and args.task not in task:
            continue
        if args.candidate and args.candidate != candidate:
            continue
        by_key[(candidate, task)].append((trial, feedback))
        all_feedback.append(feedback)

    if not by_key:
        print("no agent_feedback entries match the filters")
        return

    if args.keywords:
        # Tokenize feedback into lowercase words ≥4 chars, drop common stopwords.
        stop = {
            "would", "could", "should", "graphify", "navigate", "task", "this",
            "that", "with", "from", "have", "been", "were", "more", "than",
            "into", "when", "what", "which", "they", "them", "their", "much",
            "such", "only", "even", "some", "very", "also", "just", "like",
            "seem", "seemed", "good", "fine", "well", "tool", "call", "calls",
            "wished", "would've", "could've", "really", "still", "after",
            "first", "second", "third", "made", "make", "makes", "having",
        }
        words = []
        for f in all_feedback:
            words.extend(re.findall(r"[a-z][a-z\-_]{3,}", f.lower()))
        counts = Counter(w for w in words if w not in stop)
        print("\n=== top recurring keywords ===\n")
        for word, n in counts.most_common(30):
            print(f"  {n:3d}  {word}")
        print()
        return

    for (candidate, task), entries in sorted(by_key.items()):
        print(f"\n{'=' * 78}")
        print(f"  candidate: {candidate}")
        print(f"  task:      {task}")
        print(f"  trials with feedback: {len(entries)}")
        print(f"{'=' * 78}\n")
        for trial, feedback in sorted(entries):
            print(f"--- trial {trial} ---")
            print(feedback)
            print()


if __name__ == "__main__":
    main()
