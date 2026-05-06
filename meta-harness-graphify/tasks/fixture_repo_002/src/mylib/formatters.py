"""Output formatters."""
import json as _json

from mylib.utils import compute_score


def to_json(obj) -> str:
    return _json.dumps(obj)


def to_text(obj) -> str:
    if isinstance(obj, dict):
        return "\n".join(f"{k}: {v}" for k, v in obj.items())
    return str(obj)


def format_summary(values: list[float]) -> str:
    score = compute_score(values)
    return f"score={score:.3f} n={len(values)}"
