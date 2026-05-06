"""End-to-end data pipelines."""
from mylib.formatters import format_summary
from mylib.helpers import rank_scores
from mylib.utils import compute_score, normalize


def scoring_pipeline(rows: list[dict]) -> list[dict]:
    """Compute a score per row and tag each row with it."""
    out = []
    for row in rows:
        score = compute_score(row.get("values", []))
        out.append({**row, "score": score})
    return out


def analytics_pipeline(rows: list[dict]) -> dict:
    """Aggregate score (median across rows) plus min/max for sanity."""
    scores = [compute_score(r.get("values", [])) for r in rows]
    return {
        "agg_score": compute_score(scores),
        "min": min(scores) if scores else 0.0,
        "max": max(scores) if scores else 0.0,
    }


def normalize_pipeline(rows: list[dict], lo: float, hi: float) -> list[dict]:
    """Normalize each row's `value` field. Does NOT call compute_score."""
    return [{**r, "value": normalize(r.get("value", 0.0), lo, hi)} for r in rows]
