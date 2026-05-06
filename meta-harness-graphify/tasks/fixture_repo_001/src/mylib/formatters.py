"""Output formatters."""
import json as _json


def to_json(obj) -> str:
    return _json.dumps(obj)


def to_text(obj) -> str:
    if isinstance(obj, dict):
        return "\n".join(f"{k}: {v}" for k, v in obj.items())
    return str(obj)
