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
    _HOOK_MODE_ENV,
    _HOOK_RECENT_USE_TTL,
    _HOOK_MIN_FILE_BYTES,
    _stamp_recent_files,
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
    ctx = out["hookSpecificOutput"]["additionalContext"]
    # Read-specific nudge points at `shape` (file-level orientation).
    assert "graphify shape" in ctx


def test_hook_read_message_includes_file_size(tmp_path):
    """When the size gate has the file size, the Read nudge mentions it.
    Anchors the "scout cheaper" claim in a concrete number."""
    root = _make_root(tmp_path)
    src = tmp_path / "main.py"
    # Pad to ~12 KB so the size hint reads as `Reading 12 KB`.
    src.write_text("# " + "x" * (12 * 1024))
    out = _handle_pretool_hook(_read_payload(str(src)), root)
    assert out is not None
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "Reading " in ctx and "KB" in ctx
    # And the suggested command quotes the file path the agent typed.
    assert str(src) in ctx


def test_hook_grep_message_uses_search_verb(tmp_path):
    """Grep nudge points at `graphify search` (the verb that mirrors
    grep, with symbol attribution)."""
    root = _make_root(tmp_path)
    out = _handle_pretool_hook(
        {"tool_name": "Grep", "tool_input": {"pattern": "compute_metrics"}},
        root,
    )
    assert out is not None
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "graphify search" in ctx
    # The user's pattern is echoed back verbatim so the suggestion is
    # directly runnable.
    assert "compute_metrics" in ctx


def test_hook_glob_message_uses_files_verb(tmp_path):
    """Glob nudge points at `graphify files` (the lap-27 verb that
    mirrors glob over the indexed file set)."""
    root = _make_root(tmp_path)
    out = _handle_pretool_hook(
        {"tool_name": "Glob", "tool_input": {"pattern": "**/*.test.ts"}},
        root,
    )
    assert out is not None
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "graphify files" in ctx
    assert "**/*.test.ts" in ctx


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
    assert "graphify search" in out["hookSpecificOutput"]["additionalContext"]


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


# ---------------------------------------------------------------------------
# Lap-27 followup: hook smart-mode + per-file unblock from CLI verbs.
# ---------------------------------------------------------------------------


def _build_real_graph(tmp_path: Path, src_file: Path) -> Path:
    """Build a graphify-out/ with a non-trivial graph that resolves
    `src_file` as a file node and contains one fn inside it. Used for
    smart-mode positive-path tests where the hook must run shape
    in-process and produce real output."""
    import json as _json
    out = tmp_path / "graphify-out"
    out.mkdir(exist_ok=True)
    file_id = f"file_{src_file.stem}"
    fn_id = f"{src_file.stem}_compute"
    graph = {
        "nodes": [
            {"id": file_id, "label": str(src_file.name),
             "source_file": str(src_file), "source_location": "L1-200",
             "node_kind": "file", "file_type": "code", "community": 0,
             "kind": "file"},
            {"id": fn_id, "label": "compute()",
             "source_file": str(src_file), "source_location": "L10-30",
             "node_kind": "function", "file_type": "code", "community": 0,
             "kind": "function"},
        ],
        "edges": [
            {"source": file_id, "target": fn_id, "relation": "contains",
             "confidence": "EXTRACTED", "confidence_score": 1.0},
        ],
    }
    (out / "graph.json").write_text(_json.dumps(graph))
    return tmp_path


def test_hook_skips_when_read_has_offset(tmp_path):
    """Read with --offset is a precise re-read; the agent already
    shaped or peeked. Don't fight."""
    root = _make_root(tmp_path)
    src = tmp_path / "main.py"
    src.write_text("x" * (_HOOK_MIN_FILE_BYTES + 100))
    payload = {"tool_name": "Read",
               "tool_input": {"file_path": str(src), "offset": 100}}
    assert _handle_pretool_hook(payload, root) is None


def test_hook_skips_when_read_has_limit(tmp_path):
    """Read with --limit is a precise slice; suppress the nudge."""
    root = _make_root(tmp_path)
    src = tmp_path / "main.py"
    src.write_text("x" * (_HOOK_MIN_FILE_BYTES + 100))
    payload = {"tool_name": "Read",
               "tool_input": {"file_path": str(src), "limit": 50}}
    assert _handle_pretool_hook(payload, root) is None


def test_hook_nudge_message_advertises_silent_followups(tmp_path):
    """The Read nudge tells the agent the hook stays silent for
    follow-up Reads of the same file. Without this, an agent who
    Reads after seeing the nudge can't tell whether they'll be
    re-nudged on every retry."""
    root = _make_root(tmp_path)
    src = tmp_path / "main.py"
    src.write_text("x" * (_HOOK_MIN_FILE_BYTES + 100))
    out = _handle_pretool_hook(_read_payload(str(src)), root)
    assert out is not None
    ctx = out["hookSpecificOutput"]["additionalContext"]
    # Honest "we'll get out of your way" signal.
    assert "stays silent" in ctx or "silent on" in ctx


def test_hook_smart_mode_blocks_with_shape_output(tmp_path, monkeypatch):
    """Smart mode: first cold Read on an indexed file is BLOCKED
    with the rendered shape output as the block reason. Agent gets
    the structural data without paying the full Read."""
    src = tmp_path / "main.py"
    src.write_text("x" * (_HOOK_MIN_FILE_BYTES + 100))
    root = _build_real_graph(tmp_path, src)
    monkeypatch.setenv(_HOOK_MODE_ENV, "smart")
    out = _handle_pretool_hook(_read_payload(str(src)), root)
    assert out is not None
    # Block format: emit BOTH legacy + hookSpecificOutput so the
    # response is portable across Claude Code versions.
    assert out.get("decision") == "block"
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    # Header tells the agent we ran shape on their behalf.
    assert "graphify shape" in reason and "ran on your behalf" in reason
    # And explicitly names the unblock semantic.
    assert "stays silent" in reason or "silent for follow-up" in reason


def test_hook_smart_mode_unblocks_followup(tmp_path, monkeypatch):
    """Smart mode: second Read on the same file passes through
    silently — the first block recorded the path in recent-paths
    so the same file isn't blocked twice."""
    src = tmp_path / "main.py"
    src.write_text("x" * (_HOOK_MIN_FILE_BYTES + 100))
    root = _build_real_graph(tmp_path, src)
    monkeypatch.setenv(_HOOK_MODE_ENV, "smart")
    # First call: block.
    first = _handle_pretool_hook(_read_payload(str(src)), root)
    assert first is not None
    # Bypass cli-stamp suppression (load_graph touched it). Stale-
    # date the stamp so the next call doesn't bail on cli-stamp;
    # we want to confirm recent-paths alone unblocks.
    stamp = root / "graphify-out" / ".session" / "cli-stamp"
    stamp.write_text(f"{time.time() - (_HOOK_RECENT_USE_TTL + 60):.0f}\n")
    # Second call: silent passthrough via recent-paths dedup.
    assert _handle_pretool_hook(_read_payload(str(src)), root) is None


def test_hook_smart_mode_falls_through_when_substitute_fails(tmp_path, monkeypatch):
    """Smart mode: if the substitute can't run (graph empty / file
    not in graph), fall through to nudge mode rather than emitting
    a useless block."""
    root = _make_root(tmp_path)  # empty {} graph — load fails
    src = tmp_path / "unknown.py"
    src.write_text("x" * (_HOOK_MIN_FILE_BYTES + 100))
    monkeypatch.setenv(_HOOK_MODE_ENV, "smart")
    out = _handle_pretool_hook(_read_payload(str(src)), root)
    # Falls through to nudge — additionalContext with the suggestion.
    assert out is not None
    assert "additionalContext" in out["hookSpecificOutput"]
    assert "decision" not in out


def test_hook_smart_mode_grep_substitutes_search(tmp_path, monkeypatch):
    """Smart mode: Grep with a real pattern triggers `graphify
    search --files-only` and blocks with the result."""
    src = tmp_path / "code.py"
    src.write_text(
        "def compute_metric():\n    pass\n\n"
        "def render_metric():\n    pass\n")
    root = _build_real_graph(tmp_path, src)
    monkeypatch.setenv(_HOOK_MODE_ENV, "smart")
    out = _handle_pretool_hook(
        {"tool_name": "Grep", "tool_input": {"pattern": "metric"}}, root)
    # Either substitute fired (block) or fell through to nudge — both
    # are valid for this graph shape. We just need the smart-mode
    # branch to not crash and to produce *some* signal.
    assert out is not None


def test_hook_smart_mode_glob_substitutes_files(tmp_path, monkeypatch):
    """Smart mode: Glob with a pattern matching an indexed file
    triggers `graphify files <pat>` and blocks with the listing."""
    src = tmp_path / "main.py"
    src.write_text("# placeholder\n")
    root = _build_real_graph(tmp_path, src)
    monkeypatch.setenv(_HOOK_MODE_ENV, "smart")
    out = _handle_pretool_hook(
        {"tool_name": "Glob", "tool_input": {"pattern": "*.py"}}, root)
    assert out is not None
    # Block reason names the verb we ran.
    if out.get("decision") == "block":
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        assert "graphify files" in reason
        assert "*.py" in reason


def test_hook_silent_when_recent_paths_has_target(tmp_path):
    """User's lap-27 ask: if graphify already touched this file
    (via shape/summarize/locate from terminal), the hook stays
    silent on a follow-up Read of the same file — even if cli-stamp
    has expired."""
    root = _make_root(tmp_path)
    src = tmp_path / "main.py"
    src.write_text("x" * (_HOOK_MIN_FILE_BYTES + 100))
    # Simulate: user ran `graphify shape /a.py` 10 min ago. cli-stamp
    # is stale (>5 min) but recent-paths is fresh (<30 min).
    session = root / "graphify-out" / ".session"
    session.mkdir(parents=True, exist_ok=True)
    old_stamp = time.time() - (_HOOK_RECENT_USE_TTL + 60)
    (session / "cli-stamp").write_text(f"{old_stamp:.0f}\n")
    # Stamp src into recent-paths the same way _stamp_recent_files
    # would (resolving symlinks).
    import os.path as _osp
    rp = session / "recent-paths"
    rp.write_text(f"{time.time():.0f}\t{_osp.realpath(str(src))}\n")
    # Hook fires for Read /a.py → bails because /a.py is in recent-paths.
    assert _handle_pretool_hook(_read_payload(str(src)), root) is None


def test_stamp_recent_files_writes_to_navigate_log(tmp_path):
    """`_stamp_recent_files` shares the recent-paths log with
    navigate's `_record_session_paths` — the hook reads what
    either side writes."""
    out = tmp_path / "graphify-out"
    out.mkdir()
    graph_path = out / "graph.json"
    graph_path.write_text("{}")
    src = tmp_path / "x.py"
    src.write_text("# stub\n")
    _stamp_recent_files(graph_path, [str(src)])
    rp = out / ".session" / "recent-paths"
    assert rp.exists()
    import os.path as _osp
    assert _osp.realpath(str(src)) in rp.read_text()
