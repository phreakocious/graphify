# per-file extraction cache - skip unchanged files on re-run
from __future__ import annotations

import hashlib
import json
import os
import pickle
import sys
from pathlib import Path
from typing import Any

# Pickle cache for the *parsed* graph (post build_from_json + community
# labelling). Bumped whenever build_from_json output, _backfill_node_kind
# rules, or load_graph's stamping of `community_labels`/`community_hubs`/
# `_xlang` changes. Distinct from AST_CACHE_VERSION (per-file extraction
# cache) — the pickle sits one stage downstream and invalidates on
# graph.json mtime+size, the version below, networkx version, and the
# Python (major, minor) tuple.
PICKLE_CACHE_VERSION = "v2"  # v2: lap-27 #2 stamps vendor_class on every node


def _body_content(content: bytes) -> bytes:
    """Strip YAML frontmatter from Markdown content, returning only the body."""
    text = content.decode(errors="replace")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:].encode()
    return content


def file_hash(path: Path, root: Path = Path("."), version: str = "") -> str:
    """SHA256 of file contents + path relative to root + optional cache version.

    Using a relative path (not absolute) makes cache entries portable across
    machines and checkout directories, so shared caches and CI work correctly.
    Falls back to the resolved absolute path if the file is outside root.

    For Markdown files (.md), only the body below the YAML frontmatter is hashed,
    so metadata-only changes (e.g. reviewed, status, tags) do not invalidate the cache.

    When `version` is non-empty, it is mixed into the digest. Bumping the version
    invalidates all cache entries created with a different version — the right
    move whenever an extractor adds new node/edge kinds. Without this, a file
    whose contents haven't changed since a prior run would return the stale
    extraction.
    """
    p = Path(path)
    raw = p.read_bytes()
    content = _body_content(raw) if p.suffix.lower() == ".md" else raw
    h = hashlib.sha256()
    h.update(content)
    h.update(b"\x00")
    try:
        rel = p.resolve().relative_to(Path(root).resolve())
        h.update(str(rel).encode())
    except ValueError:
        h.update(str(p.resolve()).encode())
    if version:
        h.update(b"\x00")
        h.update(version.encode())
    return h.hexdigest()


def cache_dir(root: Path = Path(".")) -> Path:
    """Returns graphify-out/cache/ - creates it if needed."""
    d = Path(root).resolve() / "graphify-out" / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_cached(path: Path, root: Path = Path("."), version: str = "") -> dict | None:
    """Return cached extraction for this file if hash matches, else None.

    Cache key: SHA256 of file contents (+ rel path + optional version).
    Cache value: stored as graphify-out/cache/{hash}.json
    Returns None if no cache entry or file has changed.

    Pass `version` to scope the lookup to a particular extractor schema.
    A bumped version means a clean miss on entries written by older extractors.
    """
    try:
        h = file_hash(path, root, version=version)
    except OSError:
        return None
    entry = cache_dir(root) / f"{h}.json"
    if not entry.exists():
        return None
    try:
        return json.loads(entry.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_cached(path: Path, result: dict, root: Path = Path("."), version: str = "") -> None:
    """Save extraction result for this file.

    Stores as graphify-out/cache/{hash}.json where hash = SHA256 of current file contents
    (+ rel path + optional version).
    result should be a dict with 'nodes' and 'edges' lists.
    """
    h = file_hash(path, root, version=version)
    entry = cache_dir(root) / f"{h}.json"
    tmp = entry.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(result), encoding="utf-8")
        try:
            os.replace(tmp, entry)
        except PermissionError:
            # Windows: os.replace can fail with WinError 5 if the target is
            # briefly locked. Fall back to copy-then-delete.
            import shutil
            shutil.copy2(tmp, entry)
            tmp.unlink(missing_ok=True)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def cached_files(root: Path = Path(".")) -> set[str]:
    """Return set of file paths that have a valid cache entry (hash still matches)."""
    d = cache_dir(root)
    return {p.stem for p in d.glob("*.json")}


def clear_cache(root: Path = Path(".")) -> None:
    """Delete all graphify-out/cache/*.json files."""
    d = cache_dir(root)
    for f in d.glob("*.json"):
        f.unlink()


def check_semantic_cache(
    files: list[str],
    root: Path = Path("."),
) -> tuple[list[dict], list[dict], list[dict], list[str]]:
    """Check semantic extraction cache for a list of absolute file paths.

    Returns (cached_nodes, cached_edges, cached_hyperedges, uncached_files).
    Uncached files need Claude extraction; cached files are merged directly.
    """
    cached_nodes: list[dict] = []
    cached_edges: list[dict] = []
    cached_hyperedges: list[dict] = []
    uncached: list[str] = []

    for fpath in files:
        result = load_cached(Path(fpath), root)
        if result is not None:
            cached_nodes.extend(result.get("nodes", []))
            cached_edges.extend(result.get("edges", []))
            cached_hyperedges.extend(result.get("hyperedges", []))
        else:
            uncached.append(fpath)

    return cached_nodes, cached_edges, cached_hyperedges, uncached


def save_semantic_cache(
    nodes: list[dict],
    edges: list[dict],
    hyperedges: list[dict] | None = None,
    root: Path = Path("."),
) -> int:
    """Save semantic extraction results to cache, keyed by source_file.

    Groups nodes and edges by source_file, then saves one cache entry per file.
    Returns the number of files cached.
    """
    from collections import defaultdict

    by_file: dict[str, dict] = defaultdict(lambda: {"nodes": [], "edges": [], "hyperedges": []})
    for n in nodes:
        src = n.get("source_file", "")
        if src:
            by_file[src]["nodes"].append(n)
    for e in edges:
        src = e.get("source_file", "")
        if src:
            by_file[src]["edges"].append(e)
    for h in (hyperedges or []):
        src = h.get("source_file", "")
        if src:
            by_file[src]["hyperedges"].append(h)

    saved = 0
    for fpath, result in by_file.items():
        p = Path(fpath)
        if not p.is_absolute():
            p = Path(root) / p
        if p.exists():
            save_cached(p, result, root)
            saved += 1
    return saved


# --- parsed-graph pickle cache --------------------------------------------

def _graph_pickle_path(graph_json: Path) -> Path:
    """Co-locate `graph.json.pickle` next to `graph.json`."""
    p = Path(graph_json)
    return p.parent / (p.name + ".pickle")


def _nx_version() -> str:
    try:
        import networkx
        return networkx.__version__
    except Exception:
        return ""


def load_graph_pickle(graph_json: Path) -> tuple[Any, dict] | None:
    """Return (G, communities) when a fresh pickle exists for graph.json.

    A pickle is fresh when its embedded mtime_ns + size match the current
    graph.json AND its embedded version tags match (PICKLE_CACHE_VERSION,
    networkx version, Python major.minor). Any mismatch — and any read or
    deserialization error — returns None so the caller falls back to JSON
    parsing.

    Pickle is trusted: it sits next to graph.json under graphify-out/, so
    anyone who can write the pickle can already write graph.json. No
    additional integrity check beyond version pinning.
    """
    pkl = _graph_pickle_path(graph_json)
    if not pkl.exists():
        return None
    try:
        st = Path(graph_json).stat()
    except OSError:
        return None
    try:
        with pkl.open("rb") as fh:
            payload = pickle.load(fh)
    except Exception:
        # Corrupt, half-written, or pickled by an incompatible version.
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("graphify_pickle_version") != PICKLE_CACHE_VERSION:
        return None
    if payload.get("nx_version") != _nx_version():
        return None
    if payload.get("py_version") != list(sys.version_info[:2]):
        return None
    if payload.get("graph_json_mtime_ns") != st.st_mtime_ns:
        return None
    if payload.get("graph_json_size") != st.st_size:
        return None
    G = payload.get("G")
    communities = payload.get("communities")
    if G is None or communities is None:
        return None
    return G, communities


def save_graph_pickle(graph_json: Path, G: Any, communities: dict) -> None:
    """Atomically write a pickle of (G, communities) next to graph.json.

    Stamps mtime_ns+size of graph.json AT WRITE TIME so a concurrent
    re-extract that bumps the JSON mid-write produces a stale pickle on
    the next load (caught by the load-side mtime/size check).
    """
    pkl = _graph_pickle_path(graph_json)
    try:
        st = Path(graph_json).stat()
    except OSError:
        return
    payload = {
        "graphify_pickle_version": PICKLE_CACHE_VERSION,
        "nx_version": _nx_version(),
        # JSON-safe form of sys.version_info[:2] — pickle stores a tuple, but
        # the load comparison normalises to list either way (sys.version_info
        # returns a named tuple, which pickle reconstitutes as the same type).
        "py_version": list(sys.version_info[:2]),
        "graph_json_mtime_ns": st.st_mtime_ns,
        "graph_json_size": st.st_size,
        "G": G,
        "communities": communities,
    }
    pkl.parent.mkdir(parents=True, exist_ok=True)
    tmp = pkl.with_suffix(pkl.suffix + ".tmp")
    try:
        with tmp.open("wb") as fh:
            pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
        try:
            os.replace(tmp, pkl)
        except PermissionError:
            import shutil
            shutil.copy2(tmp, pkl)
            tmp.unlink(missing_ok=True)
    except Exception:
        tmp.unlink(missing_ok=True)
        # Don't raise — pickle save is best-effort. The next call will
        # re-parse the JSON normally; only the latency win is lost.
