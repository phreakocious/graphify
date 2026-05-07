"""Tests for hooks.py - git hook install/uninstall."""
import os
import subprocess
import time
from pathlib import Path
import pytest
from graphify.hooks import install, uninstall, status, _HOOK_MARKER, _CHECKOUT_MARKER
from graphify.__main__ import (
    _handle_pretool_hook,
    _HOOK_QUIET_ENV,
    _HOOK_RECENT_USE_TTL,
    _HOOK_MIN_FILE_BYTES,
)


def _make_git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    return tmp_path


def test_install_creates_hook(tmp_path):
    repo = _make_git_repo(tmp_path)
    result = install(repo)
    hook = repo / ".git" / "hooks" / "post-commit"
    assert hook.exists()
    assert _HOOK_MARKER in hook.read_text()
    assert "installed" in result


def test_install_is_executable(tmp_path):
    repo = _make_git_repo(tmp_path)
    install(repo)
    hook = repo / ".git" / "hooks" / "post-commit"
    if os.name == "nt":
        assert hook.read_text(encoding="utf-8").startswith("#!/bin/sh\n")
    else:
        assert hook.stat().st_mode & 0o111  # executable bit set


def test_install_idempotent(tmp_path):
    repo = _make_git_repo(tmp_path)
    install(repo)
    result = install(repo)
    assert "already installed" in result
    # marker appears only once
    hook = repo / ".git" / "hooks" / "post-commit"
    assert hook.read_text().count(_HOOK_MARKER) == 1


def test_install_appends_to_existing_hook(tmp_path):
    repo = _make_git_repo(tmp_path)
    hook = repo / ".git" / "hooks" / "post-commit"
    hook.write_text("#!/bin/bash\necho existing\n")
    hook.chmod(0o755)
    install(repo)
    content = hook.read_text()
    assert "existing" in content
    assert _HOOK_MARKER in content


def test_uninstall_removes_hook(tmp_path):
    repo = _make_git_repo(tmp_path)
    install(repo)
    result = uninstall(repo)
    hook = repo / ".git" / "hooks" / "post-commit"
    assert not hook.exists()
    assert "removed" in result.lower()


def test_uninstall_no_hook(tmp_path):
    repo = _make_git_repo(tmp_path)
    result = uninstall(repo)
    assert "nothing to remove" in result


def test_status_installed(tmp_path):
    repo = _make_git_repo(tmp_path)
    install(repo)
    result = status(repo)
    assert "installed" in result


def test_status_not_installed(tmp_path):
    repo = _make_git_repo(tmp_path)
    result = status(repo)
    assert "not installed" in result


def test_no_git_repo_raises(tmp_path):
    with pytest.raises(RuntimeError, match="No git repository"):
        install(tmp_path / "not_a_repo")


def test_install_creates_post_checkout_hook(tmp_path):
    repo = _make_git_repo(tmp_path)
    install(repo)
    hook = repo / ".git" / "hooks" / "post-checkout"
    assert hook.exists()
    assert _CHECKOUT_MARKER in hook.read_text()


def test_install_post_checkout_is_executable(tmp_path):
    repo = _make_git_repo(tmp_path)
    install(repo)
    hook = repo / ".git" / "hooks" / "post-checkout"
    if os.name == "nt":
        assert hook.read_text(encoding="utf-8").startswith("#!/bin/sh\n")
    else:
        assert hook.stat().st_mode & 0o111


def test_uninstall_removes_post_checkout_hook(tmp_path):
    repo = _make_git_repo(tmp_path)
    install(repo)
    uninstall(repo)
    hook = repo / ".git" / "hooks" / "post-checkout"
    assert not hook.exists()


def test_status_shows_both_hooks(tmp_path):
    repo = _make_git_repo(tmp_path)
    install(repo)
    result = status(repo)
    assert "post-commit" in result
    assert "post-checkout" in result
    assert result.count("installed") >= 2


# --- PreToolUse hook handler (lap-27 #9 anti-spam guardrails) -------------

def _make_root(tmp_path: Path, *, with_graph: bool = True) -> Path:
    """Build a tmpdir with graphify-out/graph.json so the hook handler
    finds the project structure it expects."""
    if with_graph:
        (tmp_path / "graphify-out").mkdir()
        (tmp_path / "graphify-out" / "graph.json").write_text("{}")
    return tmp_path


def _read_payload(file_path: str) -> dict:
    return {"tool_name": "Read", "tool_input": {"file_path": file_path}}


def _grep_payload() -> dict:
    return {"tool_name": "Grep", "tool_input": {"pattern": "foo"}}


def test_hook_fires_on_cold_read(tmp_path):
    """Default path: code file, no recent CLI use, no recent-paths
    match → emit the navigate-nudge."""
    root = _make_root(tmp_path)
    src = tmp_path / "main.py"
    # Big enough to clear the size floor.
    src.write_text("# " + "x" * (_HOOK_MIN_FILE_BYTES + 100))
    out = _handle_pretool_hook(_read_payload(str(src)), root)
    assert out is not None
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert "graphify navigate" in out["hookSpecificOutput"]["additionalContext"]


def test_hook_silent_when_env_var_set(tmp_path, monkeypatch):
    """`GRAPHIFY_HOOK_QUIET=1` opt-out blocks every fire path."""
    root = _make_root(tmp_path)
    src = tmp_path / "main.py"
    src.write_text("x" * (_HOOK_MIN_FILE_BYTES + 100))
    monkeypatch.setenv(_HOOK_QUIET_ENV, "1")
    assert _handle_pretool_hook(_read_payload(str(src)), root) is None
    # And on Grep too — the gate is universal.
    assert _handle_pretool_hook(_grep_payload(), root) is None


def test_hook_silent_when_quiet_env_is_zero(tmp_path, monkeypatch):
    """An empty / 0 / `false` value DOES NOT activate quiet mode."""
    root = _make_root(tmp_path)
    rp = root / "graphify-out" / ".session" / "recent-paths"
    for i, falsy in enumerate(("", "0", "false", "False")):
        # Use a fresh file per iteration so per-file dedup doesn't mask
        # the test (the hook writes to recent-paths on each fire).
        src = tmp_path / f"main{i}.py"
        src.write_text("x" * (_HOOK_MIN_FILE_BYTES + 100))
        monkeypatch.setenv(_HOOK_QUIET_ENV, falsy)
        assert _handle_pretool_hook(_read_payload(str(src)), root) is not None


def test_hook_silent_when_cli_stamp_recent(tmp_path):
    """If graphify was used in the last `_HOOK_RECENT_USE_TTL` seconds,
    the hook stays quiet — agent is in the flow."""
    root = _make_root(tmp_path)
    src = tmp_path / "main.py"
    src.write_text("x" * (_HOOK_MIN_FILE_BYTES + 100))
    session = root / "graphify-out" / ".session"
    session.mkdir(parents=True, exist_ok=True)
    (session / "cli-stamp").write_text(f"{time.time():.0f}\n")
    # Read AND Grep both suppress on recent-CLI-use.
    assert _handle_pretool_hook(_read_payload(str(src)), root) is None
    assert _handle_pretool_hook(_grep_payload(), root) is None


def test_hook_fires_when_cli_stamp_stale(tmp_path):
    """Stamp older than the TTL doesn't suppress."""
    root = _make_root(tmp_path)
    src = tmp_path / "main.py"
    src.write_text("x" * (_HOOK_MIN_FILE_BYTES + 100))
    session = root / "graphify-out" / ".session"
    session.mkdir(parents=True, exist_ok=True)
    old_ts = time.time() - (_HOOK_RECENT_USE_TTL + 60)
    (session / "cli-stamp").write_text(f"{old_ts:.0f}\n")
    assert _handle_pretool_hook(_read_payload(str(src)), root) is not None


def test_hook_silent_for_small_files(tmp_path):
    """Reading a small file doesn't trigger the nudge — orientation
    isn't useful when the file is already cheap to read."""
    root = _make_root(tmp_path)
    src = tmp_path / "tiny.py"
    src.write_text("def foo(): return 1\n")  # ~20 bytes
    assert _handle_pretool_hook(_read_payload(str(src)), root) is None


def test_hook_silent_for_non_code_extension(tmp_path):
    """The existing gate: nudge only fires on Read of a known code
    extension. A markdown file wouldn't be in graphify's index."""
    root = _make_root(tmp_path)
    src = tmp_path / "doc.md"
    src.write_text("x" * (_HOOK_MIN_FILE_BYTES + 100))
    assert _handle_pretool_hook(_read_payload(str(src)), root) is None


def test_hook_silent_inside_graphify_out(tmp_path):
    """Reading our own report doesn't trigger a recursive nudge."""
    root = _make_root(tmp_path)
    src = tmp_path / "graphify-out" / "report.py"
    src.write_text("x" * (_HOOK_MIN_FILE_BYTES + 100))
    assert _handle_pretool_hook(_read_payload(str(src)), root) is None


def test_hook_writes_recent_paths_on_fire(tmp_path):
    """First Read of a file fires; second Read of the same file is
    silent because the hook itself wrote the path into recent-paths."""
    root = _make_root(tmp_path)
    src = tmp_path / "main.py"
    src.write_text("x" * (_HOOK_MIN_FILE_BYTES + 100))
    first = _handle_pretool_hook(_read_payload(str(src)), root)
    assert first is not None
    # Recent-paths now contains this file.
    rp = root / "graphify-out" / ".session" / "recent-paths"
    assert rp.exists()
    # Second Read of the same file: silent.
    assert _handle_pretool_hook(_read_payload(str(src)), root) is None


def test_hook_recent_paths_dedup_per_file(tmp_path):
    """Per-file dedup: nudges on file A don't suppress nudges on file B."""
    root = _make_root(tmp_path)
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("x" * (_HOOK_MIN_FILE_BYTES + 100))
    b.write_text("y" * (_HOOK_MIN_FILE_BYTES + 100))
    assert _handle_pretool_hook(_read_payload(str(a)), root) is not None
    # b should still nudge — different file.
    assert _handle_pretool_hook(_read_payload(str(b)), root) is not None
    # a is muted now.
    assert _handle_pretool_hook(_read_payload(str(a)), root) is None


def test_hook_grep_fires_when_cold(tmp_path):
    """Grep / Glob have no per-file gates; they fire whenever no other
    suppression applies. (CLI-stamp / env opt-out can still silence.)"""
    root = _make_root(tmp_path)
    out = _handle_pretool_hook(_grep_payload(), root)
    assert out is not None
    assert "graphify navigate" in out["hookSpecificOutput"]["additionalContext"]


def test_hook_unknown_tool_silent(tmp_path):
    """Defense-in-depth: if Claude Code dispatches a non-Read/Glob/Grep
    tool to this hook, stay silent."""
    root = _make_root(tmp_path)
    out = _handle_pretool_hook(
        {"tool_name": "Bash", "tool_input": {"command": "ls"}}, root,
    )
    assert out is None


def test_hook_load_graph_touches_cli_stamp(tmp_path):
    """Integration: calling load_graph should write graphify-out/.session/cli-stamp,
    which the hook then reads to suppress its nudge."""
    import json as _json
    from graphify.navigate import load_graph
    out = tmp_path / "graphify-out"
    out.mkdir()
    (out / "graph.json").write_text(_json.dumps({
        "nodes": [{"id": "a", "label": "A", "source_file": str(tmp_path / "a.py"),
                   "source_location": "L1-3"}],
        "edges": [],
    }))
    load_graph(out / "graph.json", freshness_check=False)
    stamp = out / ".session" / "cli-stamp"
    assert stamp.exists()
    # Newly-touched stamp blocks the nudge for any subsequent tool call.
    src = tmp_path / "main.py"
    src.write_text("x" * (_HOOK_MIN_FILE_BYTES + 100))
    assert _handle_pretool_hook(_read_payload(str(src)), tmp_path) is None
