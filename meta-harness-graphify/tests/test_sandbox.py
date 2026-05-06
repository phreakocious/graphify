import subprocess
from pathlib import Path

import pytest

from harness.sandbox import (
    Sandbox,
    build_candidate_graphify_source,
    snapshot_repo,
)

GRAPHIFY_REPO = Path("/Volumes/chonk/projects/graphify")


def test_snapshot_repo_produces_clean_dir(tmp_path):
    out = tmp_path / "snap"
    snapshot_repo(GRAPHIFY_REPO, out)
    assert (out / "graphify" / "__init__.py").exists()
    assert not (out / ".git").exists()


def test_snapshot_repo_handles_non_git_dir(tmp_path):
    # Build a non-git source dir with a python file and a junk pycache.
    src = tmp_path / "plain_src"
    src.mkdir()
    (src / "main.py").write_text("print('hi')\n")
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "junk.pyc").write_text("x")
    out = tmp_path / "snap"
    snapshot_repo(src, out)
    assert (out / "main.py").read_text() == "print('hi')\n"
    assert not (out / "__pycache__").exists()


def test_build_candidate_graphify_source_with_no_overrides(tmp_path):
    overrides = tmp_path / "empty_overrides"
    overrides.mkdir()
    out = tmp_path / "candidate_src"
    build_candidate_graphify_source(
        graphify_src=GRAPHIFY_REPO,
        overrides_dir=overrides,
        dest=out,
    )
    assert (out / "graphify" / "__init__.py").exists()
    assert (out / "pyproject.toml").exists()


def test_build_candidate_graphify_source_applies_overrides(tmp_path):
    overrides = tmp_path / "ov"
    (overrides / "graphify").mkdir(parents=True)
    (overrides / "graphify" / "__init__.py").write_text("# OVERRIDDEN\n")
    out = tmp_path / "candidate_src"
    build_candidate_graphify_source(
        graphify_src=GRAPHIFY_REPO,
        overrides_dir=overrides,
        dest=out,
    )
    assert (out / "graphify" / "__init__.py").read_text() == "# OVERRIDDEN\n"


@pytest.mark.slow
def test_sandbox_end_to_end(tmp_path):
    """Build a sandbox with empty overrides, install graphify, verify it imports
    from the sandbox venv (not the original editable install)."""
    overrides = tmp_path / "ov"
    overrides.mkdir()
    target_repo_snap = tmp_path / "target_repo_snapshot"
    snapshot_repo(GRAPHIFY_REPO, target_repo_snap)

    sb = Sandbox.create(
        sandbox_root=tmp_path / "sb",
        target_repo_snapshot=target_repo_snap,
        graphify_src=GRAPHIFY_REPO,
        candidate_overrides=overrides,
    )
    try:
        proc = subprocess.run(
            [str(sb.venv_python), "-c", "import graphify; print(graphify.__file__)"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        assert str(GRAPHIFY_REPO) not in proc.stdout
        assert str(sb.root) in proc.stdout
    finally:
        sb.cleanup()
