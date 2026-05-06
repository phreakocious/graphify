"""Record parsers."""


def parse_record(line: str) -> dict:
    parts = line.split(",")
    if len(parts) < 2:
        return {}
    return {"key": parts[0].strip(), "value": ",".join(parts[1:]).strip()}


def parse_header(line: str) -> list[str]:
    return [c.strip() for c in line.split(",") if c.strip()]
