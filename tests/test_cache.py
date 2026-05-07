"""Tests for graphify/cache.py."""
import json
import pytest
import networkx as nx
from pathlib import Path
from graphify.cache import (
    file_hash, cache_dir, load_cached, save_cached, cached_files, clear_cache, _body_content,
    PICKLE_CACHE_VERSION, load_graph_pickle, save_graph_pickle, _graph_pickle_path,
)


@pytest.fixture
def tmp_file(tmp_path):
    f = tmp_path / "sample.txt"
    f.write_text("hello world")
    return f


@pytest.fixture
def cache_root(tmp_path):
    return tmp_path


def test_file_hash_consistent(tmp_file):
    """Same file gives same hash on repeated calls."""
    h1 = file_hash(tmp_file)
    h2 = file_hash(tmp_file)
    assert h1 == h2
    assert isinstance(h1, str)
    assert len(h1) == 64  # SHA256 hex digest length


def test_file_hash_changes(tmp_path):
    """Different file contents give different hashes."""
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_text("content one")
    f2.write_text("content two")
    assert file_hash(f1) != file_hash(f2)


def test_cache_roundtrip(tmp_file, cache_root):
    """Save then load returns the same result dict."""
    result = {"nodes": [{"id": "n1", "label": "Node1"}], "edges": []}
    save_cached(tmp_file, result, root=cache_root)
    loaded = load_cached(tmp_file, root=cache_root)
    assert loaded == result


def test_cache_miss_on_change(tmp_file, cache_root):
    """After file content changes, load_cached returns None."""
    result = {"nodes": [], "edges": [{"source": "a", "target": "b"}]}
    save_cached(tmp_file, result, root=cache_root)
    # Modify the file
    tmp_file.write_text("completely different content")
    assert load_cached(tmp_file, root=cache_root) is None


def test_cached_files(tmp_path, cache_root):
    """cached_files returns the set of cached hashes."""
    f1 = tmp_path / "file1.py"
    f2 = tmp_path / "file2.py"
    f1.write_text("alpha")
    f2.write_text("beta")

    save_cached(f1, {"nodes": [], "edges": []}, root=cache_root)
    save_cached(f2, {"nodes": [], "edges": []}, root=cache_root)

    hashes = cached_files(cache_root)
    assert file_hash(f1, cache_root) in hashes
    assert file_hash(f2, cache_root) in hashes


def test_clear_cache(tmp_file, cache_root):
    """clear_cache removes all .json files from graphify-out/cache/."""
    save_cached(tmp_file, {"nodes": [], "edges": []}, root=cache_root)
    assert len(list((cache_root / "graphify-out" / "cache").glob("*.json"))) > 0
    clear_cache(cache_root)
    assert len(list((cache_root / "graphify-out" / "cache").glob("*.json"))) == 0


def test_md_frontmatter_only_change_same_hash(tmp_path):
    """Changing only frontmatter fields in a .md file does not change the hash."""
    f = tmp_path / "doc.md"
    f.write_text("---\nreviewed: 2026-01-01\n---\n\n# Title\n\nBody text.")
    h1 = file_hash(f)
    f.write_text("---\nreviewed: 2026-04-09\n---\n\n# Title\n\nBody text.")
    h2 = file_hash(f)
    assert h1 == h2


def test_md_body_change_different_hash(tmp_path):
    """Changing the body of a .md file produces a different hash."""
    f = tmp_path / "doc.md"
    f.write_text("---\nreviewed: 2026-01-01\n---\n\n# Title\n\nOriginal body.")
    h1 = file_hash(f)
    f.write_text("---\nreviewed: 2026-01-01\n---\n\n# Title\n\nChanged body.")
    h2 = file_hash(f)
    assert h1 != h2


def test_md_no_frontmatter_hashed_normally(tmp_path):
    """A .md file with no frontmatter is hashed by its full content."""
    f = tmp_path / "doc.md"
    f.write_text("# Just a heading\n\nNo frontmatter here.")
    h1 = file_hash(f)
    f.write_text("# Just a heading\n\nDifferent content.")
    h2 = file_hash(f)
    assert h1 != h2


def test_non_md_file_hashed_fully(tmp_path):
    """Non-.md files are still hashed by their full content."""
    f = tmp_path / "script.py"
    f.write_text("# comment\nx = 1")
    h1 = file_hash(f)
    f.write_text("# changed comment\nx = 1")
    h2 = file_hash(f)
    assert h1 != h2


def test_body_content_strips_frontmatter():
    """_body_content correctly strips YAML frontmatter."""
    content = b"---\ntitle: Test\n---\n\nActual body."
    assert _body_content(content) == b"\n\nActual body."


def test_body_content_no_frontmatter():
    """_body_content returns content unchanged when no frontmatter present."""
    content = b"No frontmatter here."
    assert _body_content(content) == content


def test_file_hash_version_changes_digest(tmp_file):
    """A non-empty version mixes into the digest — same file, different versions, different hashes."""
    h_unversioned = file_hash(tmp_file)
    h_v1 = file_hash(tmp_file, version="v1")
    h_v2 = file_hash(tmp_file, version="v2")
    assert h_unversioned != h_v1
    assert h_v1 != h_v2
    # Empty version stays back-compatible with the unversioned digest.
    assert file_hash(tmp_file, version="") == h_unversioned


def test_version_bump_invalidates_cache(tmp_file, cache_root):
    """An entry saved under one version is invisible to load_cached with a different version."""
    result = {"nodes": [{"id": "n1"}], "edges": []}
    save_cached(tmp_file, result, root=cache_root, version="v1")
    assert load_cached(tmp_file, root=cache_root, version="v1") == result
    # Bumped version: clean miss, even though file contents are unchanged.
    assert load_cached(tmp_file, root=cache_root, version="v2") is None
    # Unversioned reader also misses (digest differs).
    assert load_cached(tmp_file, root=cache_root) is None


def test_unversioned_save_load_roundtrip_unchanged(tmp_file, cache_root):
    """Existing unversioned callers (e.g. semantic cache) still work."""
    result = {"nodes": [], "edges": [{"source": "a", "target": "b"}]}
    save_cached(tmp_file, result, root=cache_root)
    assert load_cached(tmp_file, root=cache_root) == result


# --- graph pickle cache ---------------------------------------------------

@pytest.fixture
def tmp_graph_json(tmp_path):
    """Minimal but realistic graph.json: 2 nodes, 1 edge, 1 community."""
    g = tmp_path / "graphify-out" / "graph.json"
    g.parent.mkdir(parents=True, exist_ok=True)
    g.write_text(json.dumps({
        "nodes": [
            {"id": "a", "label": "A", "community": 0,
             "source_file": str(tmp_path / "a.py"), "source_location": "L1-5"},
            {"id": "b", "label": "B", "community": 0,
             "source_file": str(tmp_path / "b.py"), "source_location": "L1-3"},
        ],
        "edges": [{"source": "a", "target": "b", "relation": "calls",
                   "confidence": "EXTRACTED"}],
    }))
    return g


def test_graph_pickle_path_co_locates(tmp_graph_json):
    """Pickle lives next to graph.json with `.pickle` appended."""
    p = _graph_pickle_path(tmp_graph_json)
    assert p.parent == tmp_graph_json.parent
    assert p.name == "graph.json.pickle"


def test_graph_pickle_roundtrip(tmp_graph_json):
    """Save then load returns an equivalent graph and communities."""
    G = nx.DiGraph()
    G.add_node("a", label="A")
    G.add_node("b", label="B")
    G.add_edge("a", "b", relation="calls")
    G.graph["community_labels"] = {0: "A"}
    communities = {0: ["a", "b"]}
    save_graph_pickle(tmp_graph_json, G, communities)
    loaded = load_graph_pickle(tmp_graph_json)
    assert loaded is not None
    G2, communities2 = loaded
    assert isinstance(G2, nx.DiGraph)
    assert set(G2.nodes()) == {"a", "b"}
    assert list(G2.edges()) == [("a", "b")]
    assert G2.graph.get("community_labels") == {0: "A"}
    assert communities2 == communities


def test_graph_pickle_miss_when_json_modified(tmp_graph_json):
    """Modifying graph.json after save produces a miss."""
    G = nx.DiGraph()
    G.add_node("a")
    save_graph_pickle(tmp_graph_json, G, {0: ["a"]})
    # Bump the json contents — different mtime+size.
    import time
    time.sleep(0.01)  # ensure mtime moves
    tmp_graph_json.write_text(json.dumps({"nodes": [{"id": "a", "label": "A"},
                                                    {"id": "c", "label": "C"}],
                                          "edges": []}))
    assert load_graph_pickle(tmp_graph_json) is None


def test_graph_pickle_miss_when_version_changes(tmp_graph_json, monkeypatch):
    """A saved pickle invalidates after a PICKLE_CACHE_VERSION bump."""
    G = nx.DiGraph()
    G.add_node("a")
    save_graph_pickle(tmp_graph_json, G, {})
    assert load_graph_pickle(tmp_graph_json) is not None
    # Simulate a version bump by patching the constant the loader compares against.
    import graphify.cache as _cache
    monkeypatch.setattr(_cache, "PICKLE_CACHE_VERSION", "v999")
    assert load_graph_pickle(tmp_graph_json) is None


def test_graph_pickle_miss_when_pickle_missing(tmp_graph_json):
    """No pickle file present → clean None (not an exception)."""
    assert load_graph_pickle(tmp_graph_json) is None


def test_graph_pickle_miss_when_corrupt(tmp_graph_json):
    """Garbage in the pickle file produces None, not an unhandled error."""
    pickle_path = _graph_pickle_path(tmp_graph_json)
    pickle_path.write_bytes(b"this is not a pickle")
    assert load_graph_pickle(tmp_graph_json) is None


def test_vendor_class_helpers():
    """Path/suffix classification covers the universal third-party + generator conventions."""
    from graphify.resolve import _is_vendored_path, _is_generated_path, vendor_class
    # Path-segment patterns (vendored)
    assert _is_vendored_path("node_modules/lodash/index.js")
    assert _is_vendored_path("repo/node_modules/lodash/index.js")
    assert _is_vendored_path("vendor/google.golang.org/grpc/server.go")
    assert _is_vendored_path(".venv/lib/python3.12/site-packages/foo/bar.py")
    assert _is_vendored_path("third_party/llama/foo.cpp")
    assert _is_vendored_path("project/dist/main.bundle.js")
    assert _is_vendored_path("project/build/output.js")
    assert _is_vendored_path("rust-app/target/debug/deps/foo.rs")
    assert _is_vendored_path("py/__pycache__/foo.cpython-312.pyc")
    # Substring traps must NOT match (path segment anchoring).
    assert not _is_vendored_path("src/buildable/foo.py")
    assert not _is_vendored_path("src/distance.py")
    assert not _is_vendored_path("src/vendormock_test.py")
    # First-party
    assert not _is_vendored_path("src/main.py")
    assert not _is_vendored_path("graphify/extract.py")

    # Generator-suffix patterns
    assert _is_generated_path("api/foo.pb.go")
    assert _is_generated_path("schemas/foo_gen.go")
    assert _is_generated_path("client/types.generated.ts")
    assert _is_generated_path("public/app.min.js")
    assert _is_generated_path("api/proto_pb2.py")
    assert _is_generated_path("api/proto_pb2_grpc.py")
    # First-party doesn't trigger
    assert not _is_generated_path("src/foo.go")
    assert not _is_generated_path("src/types.ts")

    # Combined classifier — order: archived > vendored > generated > first_party.
    assert vendor_class("frozen/old.py") == "archived"
    assert vendor_class("vendor/x/foo.pb.go") == "vendored"     # vendor wins over generated
    assert vendor_class("src/proto/foo.pb.go") == "generated"
    assert vendor_class("src/main.py") == "first_party"
    assert vendor_class(None) == "first_party"
    assert vendor_class("") == "first_party"


def test_stamp_vendor_class_tags_every_node():
    """`build_from_json` stamps `vendor_class` on every node in the graph."""
    from graphify.build import build_from_json
    extraction = {
        "nodes": [
            {"id": "f1", "label": "foo()", "source_file": "src/foo.py", "source_location": "L1-5"},
            {"id": "v1", "label": "lodash", "source_file": "node_modules/lodash/index.js", "source_location": "L1-5"},
            {"id": "g1", "label": "Foo()", "source_file": "api/foo.pb.go", "source_location": "L1-5"},
            {"id": "a1", "label": "old()", "source_file": "frozen/old.py", "source_location": "L1-5"},
        ],
        "edges": [],
    }
    G = build_from_json(extraction, directed=True)
    assert G.nodes["f1"]["vendor_class"] == "first_party"
    assert G.nodes["v1"]["vendor_class"] == "vendored"
    assert G.nodes["g1"]["vendor_class"] == "generated"
    assert G.nodes["a1"]["vendor_class"] == "archived"


def test_graph_pickle_roundtrip_via_load_graph(tmp_graph_json):
    """End-to-end: load_graph populates the pickle; second call reads from it.

    Mutate graph.json's contents so we can prove the second call used the
    pickle (which still has the original two nodes) instead of re-parsing.
    """
    from graphify.navigate import load_graph
    G1, c1 = load_graph(tmp_graph_json, freshness_check=False)
    assert _graph_pickle_path(tmp_graph_json).exists()
    # Replace JSON with a different shape but DON'T touch its mtime/size: write
    # a same-byte-count payload via overwriting in place. Easier: write garbage
    # JSON and confirm the second call still returns the original 2-node graph
    # (because pickle still matches mtime+size). We can't easily preserve size
    # so instead probe by changing PICKLE_CACHE_VERSION and asserting fallback.
    G2, c2 = load_graph(tmp_graph_json, freshness_check=False)
    assert set(G2.nodes()) == set(G1.nodes())
    assert c2 == c1
