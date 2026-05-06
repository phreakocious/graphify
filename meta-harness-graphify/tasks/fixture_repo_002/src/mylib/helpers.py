"""Label, tag, and ranking helpers."""
from mylib.utils import compute_score


def format_label(prefix: str, name: str) -> str:
    return f"[{prefix}] {name}"


def parse_tag(tag: str) -> tuple[str, str]:
    if ":" not in tag:
        return ("", tag)
    k, v = tag.split(":", 1)
    return (k.strip(), v.strip())


def rank_scores(items: list[dict]) -> list[dict]:
    """Sort items by their score (median of values), descending."""
    return sorted(
        items,
        key=lambda it: compute_score(it.get("values", [])),
        reverse=True,
    )
