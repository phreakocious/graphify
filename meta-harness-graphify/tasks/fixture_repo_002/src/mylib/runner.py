"""Top-level pipeline driver."""
from mylib.pipelines import analytics_pipeline, scoring_pipeline
from mylib.utils import compute_score


def process(rows: list[dict]) -> dict:
    """Run scoring + analytics, return combined report.

    Note: this is the only function that calls compute_score directly
    in addition to delegating to the pipelines.
    """
    scored = scoring_pipeline(rows)
    overall = compute_score([r["score"] for r in scored])
    analytics = analytics_pipeline(rows)
    return {"overall": overall, "rows": scored, "analytics": analytics}
