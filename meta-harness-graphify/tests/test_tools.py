from pathlib import Path

from harness.tools import (
    TOOL_DEFINITIONS,
    ToolContext,
    tool_bash,
    tool_edit,
    tool_read,
    tool_write,
)


def _ctx(tmp_path: Path) -> ToolContext:
    venv_python = tmp_path / "venv_python"
    venv_python.write_text("# fake")
    repo = tmp_path / "repo"
    repo.mkdir()
    return ToolContext(repo_dir=repo, venv_python=venv_python)


def test_tool_read_returns_content(tmp_path):
    ctx = _ctx(tmp_path)
    (ctx.repo_dir / "f.txt").write_text("hello\nworld\n")
    result = tool_read(ctx, path="f.txt")
    assert "hello" in result
    assert "world" in result


def test_tool_read_missing_file_returns_error(tmp_path):
    ctx = _ctx(tmp_path)
    result = tool_read(ctx, path="missing.txt")
    assert "not found" in result.lower()


def test_tool_write_creates_file(tmp_path):
    ctx = _ctx(tmp_path)
    tool_write(ctx, path="new.txt", content="hi")
    assert (ctx.repo_dir / "new.txt").read_text() == "hi"


def test_tool_edit_str_replace(tmp_path):
    ctx = _ctx(tmp_path)
    (ctx.repo_dir / "f.txt").write_text("foo bar baz")
    tool_edit(ctx, path="f.txt", old_string="bar", new_string="QUX")
    assert (ctx.repo_dir / "f.txt").read_text() == "foo QUX baz"


def test_tool_edit_old_string_not_found_errors(tmp_path):
    ctx = _ctx(tmp_path)
    (ctx.repo_dir / "f.txt").write_text("foo")
    result = tool_edit(ctx, path="f.txt", old_string="xyz", new_string="abc")
    assert "not found" in result.lower()


def test_tool_edit_multiple_matches_errors(tmp_path):
    ctx = _ctx(tmp_path)
    (ctx.repo_dir / "f.txt").write_text("ab ab ab")
    result = tool_edit(ctx, path="f.txt", old_string="ab", new_string="X")
    assert "matches" in result.lower()


def test_tool_bash_runs_in_repo(tmp_path):
    ctx = _ctx(tmp_path)
    (ctx.repo_dir / "marker").write_text("here")
    result = tool_bash(ctx, command="ls")
    assert "marker" in result


def test_tool_definitions_include_all_four():
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert names == {"read_file", "write_file", "edit_file", "bash"}
