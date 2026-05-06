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
    """Median of a list of floats. Empty list returns 0.0."""
    if not values:
        return 0.0
    sorted_values = sorted(values)
    n = len(sorted_values)
    mid = n // 2
    if n % 2 == 1:
        return sorted_values[mid]
    return (sorted_values[mid - 1] + sorted_values[mid]) / 2
