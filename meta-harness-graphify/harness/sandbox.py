"""Per-eval sandbox: target-repo snapshot + isolated venv with candidate's graphify."""
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path


def snapshot_repo(repo: Path, dest: Path) -> None:
    """Snapshot a git repo's HEAD into `dest` using `git archive`. dest must not exist."""
    if dest.exists():
        raise FileExistsError(dest)
    dest.mkdir(parents=True)
    with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as tf:
        tar_path = Path(tf.name)
    try:
        subprocess.run(
            ["git", "archive", "--format=tar", "-o", str(tar_path), "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        with tarfile.open(tar_path) as tar:
            tar.extractall(dest)
    finally:
        tar_path.unlink(missing_ok=True)


_REQUIRED_TOP_LEVEL = ("pyproject.toml", "LICENSE", "README.md")
_REQUIRED_PACKAGE_DIRS = ("graphify",)


def build_candidate_graphify_source(
    *,
    graphify_src: Path,
    overrides_dir: Path,
    dest: Path,
) -> None:
    """Build a candidate graphify source tree at `dest`. Copies only the files
    needed for `pip install`: pyproject.toml, LICENSE, README.md, and the
    graphify/ package dir. Then overlays `overrides_dir` on top.

    Why the explicit allowlist (vs. copytree of the whole repo): the graphify
    repo contains symlinks to sibling projects (e.g. `exotic-geometry-framework@`,
    `zero-tvm@`) that copytree follows by default — copying GB of unrelated
    code. Allowlist keeps the candidate tree small and deterministic.

    `dest` must not exist.
    """
    if dest.exists():
        raise FileExistsError(dest)
    dest.mkdir(parents=True)
    for fname in _REQUIRED_TOP_LEVEL:
        src_file = graphify_src / fname
        if src_file.is_file():
            shutil.copy2(src_file, dest / fname)
    for dname in _REQUIRED_PACKAGE_DIRS:
        src_dir = graphify_src / dname
        if src_dir.is_dir():
            shutil.copytree(
                src_dir,
                dest / dname,
                ignore=shutil.ignore_patterns(
                    "__pycache__", "*.pyc", "*.egg-info",
                ),
                symlinks=True,
            )
    if overrides_dir.exists():
        for src in overrides_dir.rglob("*"):
            if src.is_file():
                rel = src.relative_to(overrides_dir)
                dst = dest / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)


def create_sandbox_venv(
    *,
    venv_dir: Path,
    candidate_graphify_src: Path,
    install_timeout: int = 300,
) -> Path:
    """Create a venv at venv_dir and pip-install the candidate graphify (non-editable).
    Returns the path to the venv's python."""
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    venv_python = venv_dir / "bin" / "python"
    if not venv_python.exists():
        venv_python = venv_dir / "Scripts" / "python.exe"
    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--quiet", "--upgrade", "pip"],
        check=True,
        timeout=install_timeout,
    )
    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--quiet", str(candidate_graphify_src)],
        check=True,
        timeout=install_timeout,
    )
    # pytest is needed for tasks whose oracle is pytest_passes — install into the
    # candidate venv so the oracle runs the candidate's installed packages.
    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--quiet", "pytest"],
        check=True,
        timeout=install_timeout,
    )
    return venv_python


@dataclass
class Sandbox:
    root: Path
    target_repo: Path
    venv_dir: Path
    venv_python: Path
    candidate_graphify_src: Path

    @classmethod
    def create(
        cls,
        *,
        sandbox_root: Path,
        target_repo_snapshot: Path,
        graphify_src: Path,
        candidate_overrides: Path,
    ) -> "Sandbox":
        sandbox_root.mkdir(parents=True, exist_ok=False)
        target_repo = sandbox_root / "repo"
        shutil.copytree(target_repo_snapshot, target_repo)
        candidate_src = sandbox_root / "candidate_graphify"
        build_candidate_graphify_source(
            graphify_src=graphify_src,
            overrides_dir=candidate_overrides,
            dest=candidate_src,
        )
        venv_dir = sandbox_root / "venv"
        venv_python = create_sandbox_venv(
            venv_dir=venv_dir,
            candidate_graphify_src=candidate_src,
        )
        return cls(
            root=sandbox_root,
            target_repo=target_repo,
            venv_dir=venv_dir,
            venv_python=venv_python,
            candidate_graphify_src=candidate_src,
        )

    def cleanup(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)
