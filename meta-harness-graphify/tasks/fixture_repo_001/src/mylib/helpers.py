"""Label and tag helpers."""


def format_label(prefix: str, name: str) -> str:
    return f"[{prefix}] {name}"


def parse_tag(tag: str) -> tuple[str, str]:
    if ":" not in tag:
        return ("", tag)
    k, v = tag.split(":", 1)
    return (k.strip(), v.strip())
