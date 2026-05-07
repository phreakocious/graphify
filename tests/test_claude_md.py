"""Tests for graphify claude install / uninstall commands.

Lap-27 #10: install writes corpus guidance to .claude/graphify.md and adds an
@-import line to CLAUDE.md. The user's CLAUDE.md stays user-owned beyond that
single line. Legacy `## graphify` sections from pre-lap-27 installers are
migrated out on re-install.
"""
from pathlib import Path
import pytest
from graphify.__main__ import (
    claude_install,
    claude_uninstall,
    _CLAUDE_MD_MARKER,
    _CLAUDE_MD_SECTION,
    _CLAUDE_MD_IMPORT_LINE,
    _GRAPHIFY_MD_RELPATH,
)


# ---------------------------------------------------------------------------
# install: writes .claude/graphify.md
# ---------------------------------------------------------------------------

def test_install_writes_graphify_md_file(tmp_path):
    """Corpus guidance lands in .claude/graphify.md, not the user's CLAUDE.md."""
    claude_install(tmp_path)
    target = tmp_path / _GRAPHIFY_MD_RELPATH
    assert target.exists()
    content = target.read_text()
    assert _CLAUDE_MD_MARKER in content
    assert "graphify navigate" in content
    assert "GRAPH_REPORT.md" in content


def test_install_creates_claude_md_with_import_when_absent(tmp_path):
    """No CLAUDE.md → install creates one containing only the import line."""
    claude_install(tmp_path)
    claude_md = tmp_path / "CLAUDE.md"
    assert claude_md.exists()
    assert _CLAUDE_MD_IMPORT_LINE in claude_md.read_text()


def test_install_appends_import_to_existing_claude_md(tmp_path):
    """Pre-existing CLAUDE.md content is preserved; import line is appended."""
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# My Project\n\nMy own rules.\n")
    claude_install(tmp_path)
    content = claude_md.read_text()
    assert "My Project" in content
    assert "My own rules" in content
    assert _CLAUDE_MD_IMPORT_LINE in content


def test_install_does_not_inline_section_in_claude_md(tmp_path):
    """User's CLAUDE.md gets only the import line — not the full section body."""
    claude_install(tmp_path)
    claude_md_content = (tmp_path / "CLAUDE.md").read_text()
    # Marker (## graphify heading) lives in the imported file, not CLAUDE.md.
    assert _CLAUDE_MD_MARKER not in claude_md_content
    # Body content also doesn't appear inline.
    assert "graphify navigate" not in claude_md_content


# ---------------------------------------------------------------------------
# install: idempotency
# ---------------------------------------------------------------------------

def test_install_is_idempotent_on_import_line(tmp_path, capsys):
    """Running install twice does not duplicate the import line."""
    claude_install(tmp_path)
    claude_install(tmp_path)
    content = (tmp_path / "CLAUDE.md").read_text()
    assert content.count(_CLAUDE_MD_IMPORT_LINE) == 1


def test_install_overwrites_graphify_md_on_rerun(tmp_path):
    """.claude/graphify.md is graphify-owned and gets refreshed on every install."""
    target = tmp_path / _GRAPHIFY_MD_RELPATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("stale content from older version")
    claude_install(tmp_path)
    assert _CLAUDE_MD_MARKER in target.read_text()
    assert "stale content" not in target.read_text()


def test_install_idempotent_message(tmp_path, capsys):
    """Second install reports the import is already present."""
    claude_install(tmp_path)
    capsys.readouterr()
    claude_install(tmp_path)
    out = capsys.readouterr().out
    assert "already imports" in out


# ---------------------------------------------------------------------------
# install: --no-import
# ---------------------------------------------------------------------------

def test_install_no_import_skips_claude_md_when_absent(tmp_path):
    """--no-import: don't create CLAUDE.md; user takes responsibility for the import."""
    claude_install(tmp_path, add_import=False)
    assert (tmp_path / _GRAPHIFY_MD_RELPATH).exists()
    assert not (tmp_path / "CLAUDE.md").exists()


def test_install_no_import_leaves_existing_claude_md_untouched(tmp_path):
    """--no-import on existing CLAUDE.md: file is left exactly as-is."""
    claude_md = tmp_path / "CLAUDE.md"
    original = "# My Project\n\nMy rules.\n"
    claude_md.write_text(original)
    claude_install(tmp_path, add_import=False)
    assert claude_md.read_text() == original


def test_install_no_import_prints_next_step(tmp_path, capsys):
    """--no-import: tell the user the line they need to add."""
    claude_install(tmp_path, add_import=False)
    out = capsys.readouterr().out
    assert _CLAUDE_MD_IMPORT_LINE in out


# ---------------------------------------------------------------------------
# install: legacy migration
# ---------------------------------------------------------------------------

def test_install_migrates_legacy_section_out_of_claude_md(tmp_path):
    """Pre-lap-27 inline section is removed; replaced with import line."""
    claude_md = tmp_path / "CLAUDE.md"
    legacy = (
        "# My Project\n\nMy rules.\n\n"
        + _CLAUDE_MD_SECTION
        + "\n## My Other Section\n\nMore rules.\n"
    )
    claude_md.write_text(legacy)
    claude_install(tmp_path)
    content = claude_md.read_text()
    assert "My Project" in content
    assert "My rules" in content
    assert "My Other Section" in content
    assert "More rules" in content
    # Legacy inline content is gone.
    assert "graphify navigate" not in content
    # Import line replaces it.
    assert _CLAUDE_MD_IMPORT_LINE in content


def test_install_migration_writes_graphify_md(tmp_path):
    """Migration also creates .claude/graphify.md."""
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# Stuff\n\n" + _CLAUDE_MD_SECTION)
    claude_install(tmp_path)
    assert (tmp_path / _GRAPHIFY_MD_RELPATH).exists()


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------

def test_uninstall_removes_graphify_md_file(tmp_path):
    """The managed file is deleted."""
    claude_install(tmp_path)
    claude_uninstall(tmp_path)
    assert not (tmp_path / _GRAPHIFY_MD_RELPATH).exists()


def test_uninstall_removes_import_line(tmp_path):
    """The import line is removed from CLAUDE.md."""
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# Mine\n\nMy stuff.\n")
    claude_install(tmp_path)
    claude_uninstall(tmp_path)
    if claude_md.exists():
        assert _CLAUDE_MD_IMPORT_LINE not in claude_md.read_text()


def test_uninstall_preserves_user_content(tmp_path):
    """User's own CLAUDE.md content survives uninstall."""
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# My Project\n\nMy rules.\n")
    claude_install(tmp_path)
    claude_uninstall(tmp_path)
    content = claude_md.read_text()
    assert "My Project" in content
    assert "My rules" in content


def test_uninstall_removes_legacy_section_defensively(tmp_path):
    """If the user is on the pre-lap-27 installer and never re-ran install,
    uninstall still cleans up the inline section."""
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# Stuff\n\n" + _CLAUDE_MD_SECTION)
    claude_uninstall(tmp_path)
    if claude_md.exists():
        content = claude_md.read_text()
        assert _CLAUDE_MD_MARKER not in content
        assert "Stuff" in content


def test_uninstall_deletes_empty_claude_md(tmp_path):
    """If CLAUDE.md only contained the import line, it's removed."""
    claude_install(tmp_path)
    claude_uninstall(tmp_path)
    assert not (tmp_path / "CLAUDE.md").exists()


def test_uninstall_no_op_when_nothing_installed(tmp_path, capsys):
    """Uninstall with no graphify state prints a clean message."""
    claude_uninstall(tmp_path)
    out = capsys.readouterr().out
    assert "nothing to do" in out


# ---------------------------------------------------------------------------
# settings.json PreToolUse hook
# ---------------------------------------------------------------------------

def test_install_creates_settings_json(tmp_path):
    """claude_install writes .claude/settings.json with PreToolUse hook."""
    import json
    claude_install(tmp_path)
    settings_path = tmp_path / ".claude" / "settings.json"
    assert settings_path.exists()
    settings = json.loads(settings_path.read_text())
    hooks = settings.get("hooks", {}).get("PreToolUse", [])
    assert any("graphify" in str(h) for h in hooks)


def test_install_settings_json_idempotent(tmp_path):
    """Running install twice does not duplicate the PreToolUse hook."""
    import json
    claude_install(tmp_path)
    claude_install(tmp_path)
    settings_path = tmp_path / ".claude" / "settings.json"
    settings = json.loads(settings_path.read_text())
    hooks = settings.get("hooks", {}).get("PreToolUse", [])
    graphify_hooks = [h for h in hooks if "graphify" in str(h)]
    assert len(graphify_hooks) == 1


def test_uninstall_removes_settings_hook(tmp_path):
    """claude_uninstall removes the PreToolUse hook from settings.json."""
    import json
    claude_install(tmp_path)
    claude_uninstall(tmp_path)
    settings_path = tmp_path / ".claude" / "settings.json"
    if settings_path.exists():
        settings = json.loads(settings_path.read_text())
        hooks = settings.get("hooks", {}).get("PreToolUse", [])
        assert not any("graphify" in str(h) for h in hooks)
