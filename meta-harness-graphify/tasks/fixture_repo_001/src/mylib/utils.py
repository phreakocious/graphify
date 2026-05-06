"""General utilities."""


def normalize(value: float, lo: float, hi: float) -> float:
    """Linearly map value in [lo, hi] to [0, 1]."""
    if hi == lo:
        return 0.0
    return (value - lo) / (hi - lo)


def summarize(items: list[float]) -> dict:
    """Mean, min, max."""
    if not items:
        return {"n": 0, "mean": 0.0, "min": 0.0, "max": 0.0}
    return {
        "n": len(items),
        "mean": sum(items) / len(items),
        "min": min(items),
        "max": max(items),
    }


def compute_score(values: list[float]) -> float:
    """Compute a score from a list of values.

    Currently returns the mean. Tests expect this to return the median.
    """
    if not values:
        return 0.0
    return sum(values) / len(values)
