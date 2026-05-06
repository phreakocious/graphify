"""Minimal tool surface mimicking Claude Code's Read/Edit/Write/Bash contracts."""
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ToolContext:
    repo_dir: Path
    venv_python: Path        # path to sandbox venv's python — controls `graphify` resolution


TOOL_DEFINITIONS = [
    {
        "name": "read_file",
        "description": "Read a UTF-8 text file from the working repo. Returns up to 2000 lines from the start. Lines are prefixed with line numbers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Repo-relative path"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write a file. Overwrites if it exists. Creates parent dirs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace exactly one occurrence of `old_string` with `new_string` in the file. Errors if the match isn't unique.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "bash",
        "description": "Run a shell command in the working repo. The sandbox venv's bin is on PATH first, so `graphify`, `python`, `pytest` resolve to the candidate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
            },
            "required": ["command"],
        },
    },
]


def _resolve(ctx: ToolContext, path: str) -> Path:
    p = (ctx.repo_dir / path).resolve()
    repo_real = ctx.repo_dir.resolve()
    if p != repo_real and repo_real not in p.parents:
        raise ValueError(f"path escapes repo_dir: {path}")
    return p


def tool_read(ctx: ToolContext, *, path: str, max_lines: int = 2000) -> str:
    try:
        p = _resolve(ctx, path)
    except ValueError as e:
        return f"error: {e}"
    if not p.is_file():
        return f"error: file not found: {path}"
    lines = p.read_text(errors="replace").splitlines()
    truncated = len(lines) > max_lines
    body = "\n".join(f"{i+1:6d}\t{ln}" for i, ln in enumerate(lines[:max_lines]))
    if truncated:
        body += f"\n... ({len(lines) - max_lines} more lines truncated)"
    return body


def tool_write(ctx: ToolContext, *, path: str, content: str) -> str:
    p = _resolve(ctx, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"wrote {len(content)} bytes to {path}"


def tool_edit(ctx: ToolContext, *, path: str, old_string: str, new_string: str) -> str:
    p = _resolve(ctx, path)
    if not p.is_file():
        return f"error: file not found: {path}"
    text = p.read_text(errors="replace")
    n = text.count(old_string)
    if n == 0:
        return f"error: old_string not found in {path}"
    if n > 1:
        return f"error: old_string matches {n} times in {path}; need a unique match"
    p.write_text(text.replace(old_string, new_string, 1))
    return f"edited {path}"


def tool_bash(ctx: ToolContext, *, command: str, timeout: int = 120) -> str:
    env = os.environ.copy()
    venv_bin = str(ctx.venv_python.parent)
    env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")
    env["VIRTUAL_ENV"] = str(ctx.venv_python.parent.parent)
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=ctx.repo_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return f"error: command timed out after {timeout}s"
    out = (proc.stdout or "")[-8000:]
    err = (proc.stderr or "")[-2000:]
    parts = [f"exit={proc.returncode}"]
    if out:
        parts.append(f"stdout:\n{out}")
    if err:
        parts.append(f"stderr:\n{err}")
    return "\n".join(parts)


TOOL_DISPATCH = {
    "read_file": tool_read,
    "write_file": tool_write,
    "edit_file": tool_edit,
    "bash": tool_bash,
}
