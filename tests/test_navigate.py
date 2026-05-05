import networkx as nx
from graphify.navigate import resolve_focus, label_index


def _two_files_same_basename():
    """Two `foo.py` files in different directories — exactly the disambiguation
    a path-qualified `@<dir>/<file>` query needs to nail."""
    G = nx.DiGraph()
    G.add_node("f1", label="metric_diagnostic.py", file_type="code",
               source_file="tools/metric_diagnostic.py", source_location="L1")
    G.add_node("f2", label="ab_metric_diagnostic.py", file_type="code",
               source_file="1d/ab_metric_diagnostic.py", source_location="L1")
    G.add_node("f3", label="twist_spectrum_diagnostic.py", file_type="code",
               source_file="tools/twist_spectrum_diagnostic.py", source_location="L1")
    return G


def test_path_qualified_query_resolves_unambiguously():
    """`@tools/metric_diagnostic.py` should land on the file in `tools/`,
    not be flagged as 3-way fuzzy ambiguous against basename-similar typos."""
    G = _two_files_same_basename()
    idx = label_index(G)
    chosen, candidates, match_type, _ = resolve_focus(G, idx, "tools/metric_diagnostic.py")
    assert chosen == "f1", f"path-qualified query should hit f1, got chosen={chosen!r} candidates={candidates}"
    assert candidates == []
    assert match_type == "exact"


def test_path_qualified_query_at_prefix_stripped():
    """The `@` prefix is stripped before resolution (matches CLI usage)."""
    G = _two_files_same_basename()
    idx = label_index(G)
    chosen, _, match_type, _ = resolve_focus(G, idx, "@tools/metric_diagnostic.py")
    assert chosen == "f1"
    assert match_type == "exact"


def test_basename_only_still_resolves_when_unique():
    """Path branch is additive — basename queries with one match still work."""
    G = _two_files_same_basename()
    idx = label_index(G)
    chosen, _, match_type, _ = resolve_focus(G, idx, "metric_diagnostic.py")
    assert chosen == "f1"
    assert match_type == "exact"


def test_path_qualified_no_match_falls_through():
    """Path that matches no node falls through to fuzzy — doesn't crash."""
    G = _two_files_same_basename()
    idx = label_index(G)
    chosen, candidates, match_type, _ = resolve_focus(G, idx, "nope/nonexistent.py")
    # Either no match or fuzzy — but no exception, no spurious exact hit
    assert chosen is None or match_type == "fuzzy"


def test_path_qualified_symbol_disambiguates_collision():
    """Symbol-shape path qualifier `dir/file.rs/Symbol` — disambiguates a
    label that collides across many files. Critical for Rust crates / JS
    monorepos where the same struct name lives in 10+ modules."""
    G = nx.DiGraph()
    G.add_node("vi_a", label="VectorIndex", file_type="code",
               source_file="crates/foo/src/gate.rs", source_location="L11")
    G.add_node("vi_b", label="VectorIndex", file_type="code",
               source_file="crates/foo/src/walk.rs", source_location="L15")
    G.add_node("vi_c", label="VectorIndex", file_type="code",
               source_file="crates/bar/src/gate.rs", source_location="L19")
    idx = label_index(G)
    # `path/file.ext/Symbol` should resolve to the one in crates/foo/src/walk.rs
    chosen, _, match_type, _ = resolve_focus(G, idx, "crates/foo/src/walk.rs/VectorIndex")
    assert chosen == "vi_b", f"expected vi_b, got {chosen!r}"
    assert match_type == "exact"


def test_queued_ops_replay_after_disambig_pick(tmp_path, monkeypatch):
    """Friction P5: chain `@compile methods` aborted at the disambig and
    threw away `methods`. After ship, the abort queues remaining ops to
    the cursor; the next call's pick replays them automatically so the
    agent doesn't retype the chain tail."""
    import json as _json
    from graphify.navigate import navigate
    # Build a tiny graph with two nodes both labeled `compile` so resolution is ambiguous
    nodes = [
        {"id": "c1", "label": "compile", "file_type": "code",
         "source_file": "a.py", "source_location": "L1", "community": 0},
        {"id": "c2", "label": "compile", "file_type": "code",
         "source_file": "b.py", "source_location": "L1", "community": 1},
        {"id": "x", "label": "X", "file_type": "code",
         "source_file": "a.py", "source_location": "L5", "community": 0},
        {"id": "y", "label": "Y", "file_type": "code",
         "source_file": "a.py", "source_location": "L8", "community": 0},
    ]
    links = [
        {"source": "c1", "target": "x", "relation": "method", "confidence": "EXTRACTED"},
        {"source": "c1", "target": "y", "relation": "method", "confidence": "EXTRACTED"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"nodes": nodes, "links": links}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    # First call: @compile methods → disambig → methods queued
    out1 = navigate(["@compile", "methods"], session="qtest", fmt="text")
    assert "ambiguous" in out1.lower()
    assert "queued" in out1.lower(), f"expected queued-replay note, got:\n{out1}"

    # Second call: pick [1] → should replay `methods` automatically
    out2 = navigate(["1"], session="qtest", fmt="text")
    assert "resuming queued ops" in out2 or "methods" in out2.lower()
    # The pick lands on c1, which has 2 methods (X and Y)
    assert "X" in out2 or "Y" in out2, f"expected methods listing, got:\n{out2}"


def test_queued_ops_dropped_on_non_pick_followup(tmp_path, monkeypatch):
    """Queue is per-disambig — if the next call doesn't pick, the user
    has moved on and the queue must be dropped to avoid surprise."""
    import json as _json
    from graphify.navigate import navigate
    nodes = [
        {"id": "c1", "label": "compile", "file_type": "code",
         "source_file": "a.py", "source_location": "L1", "community": 0},
        {"id": "c2", "label": "compile", "file_type": "code",
         "source_file": "b.py", "source_location": "L1", "community": 0},
        {"id": "z", "label": "zeta", "file_type": "code",
         "source_file": "z.py", "source_location": "L1", "community": 0},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"nodes": nodes, "links": []}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    navigate(["@compile", "methods"], session="qtest2", fmt="text")
    # Pivot somewhere new instead of picking
    out2 = navigate(["@zeta"], session="qtest2", fmt="text")
    assert "resuming queued ops" not in out2
    # Third call should also not replay
    out3 = navigate([], session="qtest2", fmt="text")
    assert "resuming queued ops" not in out3


def test_resolve_works_on_undirected_graph():
    """`path` and `explain` subcommands load via networkx node_link_graph
    which yields an undirected Graph. _rank_match used to call
    G.in_degree / G.out_degree → AttributeError. Regression test:
    resolution must not crash on undirected input."""
    G = nx.Graph()  # undirected on purpose
    G.add_node("a", label="alpha", file_type="code",
               source_file="m.py", source_location="L1")
    G.add_node("b", label="alpha", file_type="code",
               source_file="m.py", source_location="L5")
    G.add_edge("a", "b")  # symmetric
    idx = label_index(G)
    # Multiple-match path triggers _rank_match for sort ordering
    chosen, candidates, match_type, _ = resolve_focus(G, idx, "alpha")
    assert chosen is None
    assert len(candidates) == 2  # didn't crash and returned both
