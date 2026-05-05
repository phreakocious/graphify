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


def test_path_qualified_symbol_handles_decorated_labels():
    """Friction 1 (lap-5): the label index is keyed on the literal label
    (`compile()` with parens), so a path-qualifier query like
    `kg/compile-v2.ts/compile` (no parens) missed step 1b and fell
    through to fuzzy, which picked the FILE on similarity instead of
    the function. The path resolver must strip `()`/`.`/`_` decoration
    when matching the basename to a label."""
    G = nx.DiGraph()
    G.add_node("file", label="compile-v2.ts", file_type="code",
               source_file="kg/compile-v2.ts", source_location="L1")
    G.add_node("fn", label="compile()", file_type="code",
               source_file="kg/compile-v2.ts", source_location="L68")
    G.add_node("other", label="compile()", file_type="code",
               source_file="kg/compile.ts", source_location="L53")
    idx = label_index(G)
    chosen, _, match_type, _ = resolve_focus(G, idx, "kg/compile-v2.ts/compile")
    assert chosen == "fn", f"path/file/Symbol picked {chosen!r}, expected fn"
    assert match_type == "exact"
    # Without the symbol part, the file resolves
    chosen, _, _, _ = resolve_focus(G, idx, "kg/compile-v2.ts")
    assert chosen == "file"


def test_inline_pick_completes_chain_without_pause(tmp_path, monkeypatch):
    """Friction 2 (lap-5): `@compile 2 in` should land on the picked
    node and run `in` in one call. Prior behavior paused at @compile,
    queued `[2, in]`, then double-replayed when the user retyped."""
    import json as _json
    from graphify.navigate import navigate
    nodes = [
        {"id": "c1", "label": "compile()", "file_type": "code",
         "source_file": "a.ts", "source_location": "L1", "community": 0},
        {"id": "c2", "label": "compile()", "file_type": "code",
         "source_file": "b.ts", "source_location": "L1", "community": 0},
        {"id": "caller", "label": "runIt()", "file_type": "code",
         "source_file": "b.ts", "source_location": "L20", "community": 0},
    ]
    links = [
        {"source": "caller", "target": "c2", "relation": "calls",
         "confidence": "EXTRACTED", "source_file": "b.ts"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"nodes": nodes, "links": links}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    out = navigate(["@compile", "2", "in"], session=False, fmt="text")
    # Pick fired and the chain ran `in` against the resolved node.
    # The exact ranking of c1 vs c2 depends on degree/file order;
    # accept either outcome — what matters is that the chain ran
    # all three ops in one call and didn't pause.
    assert "picked [2]" in out, f"pick didn't run inline; got:\n{out}"
    assert "in(" in out, f"in pivot didn't fire; got:\n{out}"
    # No "chain paused" — the inline pick should resolve cleanly.
    assert "chain paused" not in out


def test_queue_retype_doesnt_double_fire(tmp_path, monkeypatch):
    """Friction 2 (lap-5) defensive: if the user reads our queue message
    and retypes the queued ops verbatim along with the pick, the dedupe
    pass strips the overlap so the chain doesn't run things twice."""
    import json as _json
    from graphify.navigate import navigate
    nodes = [
        {"id": "c1", "label": "compile()", "file_type": "code",
         "source_file": "a.ts", "source_location": "L1", "community": 0},
        {"id": "c2", "label": "compile()", "file_type": "code",
         "source_file": "b.ts", "source_location": "L1", "community": 0},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"nodes": nodes, "links": []}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    # Pause: @compile alone; methods queued
    navigate(["@compile", "methods"], session="qdedupe", fmt="text")
    # User retypes "2 methods" — queue had ["methods"], so the second
    # `methods` should be dedup'd out.
    out = navigate(["2", "methods"], session="qdedupe", fmt="text")
    # Only ONE methods op should have fired
    assert out.count("methods") <= 3, (
        f"likely double-fire (too many 'methods' refs in output): {out}"
    )


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


def test_community_labels_prefer_symbols_over_files(tmp_path, monkeypatch):
    """Friction 9: cluster labels pick the head, not the most-meaningful
    member. File nodes accumulate `defined_in` from every symbol → high
    degree → label gets `types.ts` instead of `buildDecodeEngine`. Fix:
    prefer non-file nodes, fall back to file only when no symbols exist."""
    import json as _json
    from graphify.navigate import load_graph
    nodes = [
        # File node — high in-degree from defined_in edges
        {"id": "types", "label": "types.ts", "file_type": "code",
         "source_file": "types.ts", "source_location": "L1", "community": 0},
        # Code symbol with lower direct degree but that's the semantic center
        {"id": "engine", "label": "buildDecodeEngine()", "file_type": "code",
         "source_file": "engine.ts", "source_location": "L10", "community": 0},
        {"id": "x1", "label": "x1", "file_type": "code",
         "source_file": "types.ts", "source_location": "L5", "community": 0},
        {"id": "x2", "label": "x2", "file_type": "code",
         "source_file": "types.ts", "source_location": "L7", "community": 0},
        {"id": "x3", "label": "x3", "file_type": "code",
         "source_file": "types.ts", "source_location": "L9", "community": 0},
    ]
    # types.ts has degree 4 (3 defined_in + 1 from engine). Among the
    # non-file nodes, engine has degree 4 (all xs call it + edge to types),
    # while x1/x2/x3 have degree 2 each. With the file filter, engine
    # wins on degree as the semantic center.
    links = [
        {"source": "x1", "target": "types", "relation": "defined_in", "confidence": "EXTRACTED"},
        {"source": "x2", "target": "types", "relation": "defined_in", "confidence": "EXTRACTED"},
        {"source": "x3", "target": "types", "relation": "defined_in", "confidence": "EXTRACTED"},
        {"source": "engine", "target": "types", "relation": "uses", "confidence": "EXTRACTED"},
        {"source": "x1", "target": "engine", "relation": "calls", "confidence": "EXTRACTED"},
        {"source": "x2", "target": "engine", "relation": "calls", "confidence": "EXTRACTED"},
        {"source": "x3", "target": "engine", "relation": "calls", "confidence": "EXTRACTED"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"nodes": nodes, "links": links}), encoding="utf-8")
    G, communities = load_graph(graph_dir / "graph.json")
    label = G.graph["community_labels"][0]
    assert "types.ts" not in label, f"file node won label: {label}"
    assert "buildDecodeEngine" in label, f"expected buildDecodeEngine, got: {label}"


def test_archived_paths_sort_last_in_disambig():
    """Lap-6/7 friction: `@compile` returned 14 matches mixing active code
    with archived variants. Archived paths (frozen/, legacy/, deprecated/,
    archive(d)/) must sort to the end so the active candidates lead the
    listing.

    Lap-7 narrowed the heuristic: `experiments/` is no longer auto-archived
    because consumers had active R&D living under that path. The matcher
    now only flags directory segments that are nearly always archive
    markers."""
    G = nx.DiGraph()
    G.add_node("active", label="compile()", file_type="code",
               source_file="src/compile.ts", source_location="L1")
    G.add_node("legacy", label="compile()", file_type="code",
               source_file="frozen/legacy_compile.py", source_location="L1")
    G.add_node("deprec", label="compile()", file_type="code",
               source_file="src/deprecated/compile.py", source_location="L1")
    idx = label_index(G)
    chosen, candidates, _, _ = resolve_focus(G, idx, "compile")
    # Multi-match — but the active code should sort first.
    assert chosen is None
    assert candidates[0] == "active", (
        f"expected active first, got order: {candidates}"
    )
    # Both archived paths land at the end (any order among them is fine).
    assert set(candidates[1:]) == {"legacy", "deprec"}


def test_archive_pattern_excludes_unrelated_segments():
    """The archive matcher is path-segment based, not substring. A symbol
    in `kgarchive.py` must NOT be tagged archived; only an `archive/` path
    component should.

    Lap-7: `experiments/` was dropped from the archive set — a consumer
    Claude reported `src/experiments/cartography-build.ts` (actively in
    development) was being false-flagged. We now only match path segments
    that are nearly-always archive markers."""
    from graphify.navigate import _is_archived_path
    # Substring traps must not trigger.
    assert not _is_archived_path("src/kgarchive.py")
    assert not _is_archived_path("src/main.ts")
    # `experiments/` is no longer flagged (lap-7 narrowing).
    assert not _is_archived_path("src/experiments/cartography-build.ts")
    assert not _is_archived_path("experiments/v3/foo.py")
    # Strong-signal patterns still match.
    assert _is_archived_path("src/legacy/x.py")
    assert _is_archived_path("frozen/m.ts")
    assert _is_archived_path("src/archived/compile.py")
    assert _is_archived_path("src/deprecated/foo.py")
    assert _is_archived_path("archive/old.py")


def test_inferred_locality_outranks_collision_noise(tmp_path, monkeypatch):
    """Lap-6 friction 8: an inferred edge to a same-named method on a
    completely different class (`.destroy()` everywhere) should not
    outrank a same-file inferred match purely on graph degree. Same-file
    candidates win the locality tiebreaker."""
    import json as _json
    from graphify.navigate import navigate
    nodes = [
        {"id": "focus", "label": "doThing()", "file_type": "code",
         "source_file": "engine.ts", "source_location": "L10", "community": 0},
        # Same file as focus — should rank above the cross-file match
        {"id": "near", "label": "helper()", "file_type": "code",
         "source_file": "engine.ts", "source_location": "L20", "community": 0},
        # Different file — high degree, but should sort below near
        {"id": "far", "label": "destroy()", "file_type": "code",
         "source_file": "other.ts", "source_location": "L5", "community": 1},
        # Add degree to `far` to make it higher-degree than near
        {"id": "deg1", "label": "x1", "file_type": "code",
         "source_file": "other.ts", "source_location": "L1", "community": 1},
        {"id": "deg2", "label": "x2", "file_type": "code",
         "source_file": "other.ts", "source_location": "L2", "community": 1},
        {"id": "deg3", "label": "x3", "file_type": "code",
         "source_file": "other.ts", "source_location": "L3", "community": 1},
    ]
    links = [
        # Both inferred OUT edges from focus
        {"source": "focus", "target": "near", "relation": "calls",
         "confidence": "INFERRED", "confidence_score": 0.8},
        {"source": "focus", "target": "far", "relation": "calls",
         "confidence": "INFERRED", "confidence_score": 0.8},
        # Boost far's degree
        {"source": "deg1", "target": "far", "relation": "calls",
         "confidence": "EXTRACTED"},
        {"source": "deg2", "target": "far", "relation": "calls",
         "confidence": "EXTRACTED"},
        {"source": "deg3", "target": "far", "relation": "calls",
         "confidence": "EXTRACTED"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"nodes": nodes, "links": links}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    out = navigate(["@doThing", "out"], session=False,
                    fmt="text", extracted_only=False)
    near_idx = out.find("helper()")
    far_idx = out.find("destroy()")
    assert near_idx > 0 and far_idx > 0, f"expected both in output:\n{out}"
    assert near_idx < far_idx, (
        f"same-file 'helper()' should rank above cross-file 'destroy()' "
        f"despite lower degree; got:\n{out}"
    )


def test_session_id_suppressed_on_one_shot(tmp_path, monkeypatch):
    """Lap-6 friction 9: default ephemeral one-shots shouldn't print the
    session id — chain-resumption affordance was the most-cited noise."""
    import json as _json
    from graphify.navigate import navigate
    nodes = [
        {"id": "n1", "label": "alpha()", "file_type": "code",
         "source_file": "a.py", "source_location": "L1", "community": 0},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"nodes": nodes, "links": []}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    # Default session=True, single focus op, no chain → id should NOT print
    out = navigate(["@alpha"], session=True, fmt="text")
    assert "session:" not in out, f"id leaked on one-shot:\n{out}"
    # Explicit session DOES print so the agent confirms the id
    out2 = navigate(["@alpha"], session="myid", fmt="text")
    assert "session: myid" in out2


def test_siblings_on_file_returns_n_a(tmp_path, monkeypatch):
    """Lap-6 friction 10: `siblings` on a file used to return empty;
    the user couldn't tell whether it was a real empty or a misuse.
    Now emits an n/a message naming the right pivot."""
    import json as _json
    from graphify.navigate import navigate
    nodes = [
        {"id": "f", "label": "main.py", "file_type": "code",
         "source_file": "main.py", "source_location": "L1", "community": 0},
        {"id": "fn", "label": "go()", "file_type": "code",
         "source_file": "main.py", "source_location": "L5", "community": 0},
    ]
    links = [
        {"source": "f", "target": "fn", "relation": "contains",
         "confidence": "EXTRACTED"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"nodes": nodes, "links": links}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    out = navigate(["@main.py", "siblings"], session=False, fmt="text")
    assert "n/a on file nodes" in out, f"expected n/a directive, got:\n{out}"


def test_path_drops_file_edges_on_symbol_to_symbol(tmp_path):
    """Lap-6 friction 2: path between two symbols must NOT use file-graph
    hops (contains/imports) as a shortcut. The auto-detect drops those
    edges when both endpoints are symbol nodes."""
    import json as _json
    import subprocess
    nodes = [
        {"id": "fA", "label": "a.ts", "file_type": "code",
         "source_file": "a.ts", "source_location": "L1"},
        {"id": "fB", "label": "b.ts", "file_type": "code",
         "source_file": "b.ts", "source_location": "L1"},
        {"id": "symA", "label": "compile()", "file_type": "code",
         "source_file": "a.ts", "source_location": "L10"},
        {"id": "symB", "label": "embed()", "file_type": "code",
         "source_file": "b.ts", "source_location": "L20"},
    ]
    links = [
        {"source": "fA", "target": "symA", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "fB", "target": "symB", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "fA", "target": "fB", "relation": "imports",
         "confidence": "EXTRACTED"},
        # No call edge between symA and symB → no semantic path
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    # `multigraph: false` matches how production graph.json is written;
    # without it `node_link_graph` defaults to MultiGraph and the path
    # subcommand fails on `G.edges[u, v]` indexing.
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False,
         "graph": {}, "nodes": nodes, "links": links}), encoding="utf-8")
    res = subprocess.run(
        ["graphify", "path", "compile()", "embed()",
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    out = res.stdout + res.stderr
    # Should NOT print a Shortest path through contains/imports.
    assert "Shortest path" not in out or "via file graph" in out, (
        f"path used file-edge cheat:\n{out}"
    )
    # The "no semantic path" branch surfaces the file-graph route as
    # additional info — that's the user-visible signal.
    assert ("No semantic path" in out
            or "via file graph" in out
            or "No path found" in out), f"unexpected output:\n{out}"


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


def test_path_qualified_query_accepts_stem_form():
    """Lap-7 friction 2: a consumer Claude reported that the disambig hint
    suggested `path/symbol` to qualify, but only the full source path
    (`src/kg/compile-v2.ts/compile`) worked. The stem forms
    `compile-v2/compile` and `kg/compile-v2/compile` (without the `.ts`)
    failed because the resolver required `endswith(prefix)` on the full
    file path. Now resolves with extension elision."""
    G = nx.DiGraph()
    G.add_node("f", label="compile-v2.ts", file_type="code",
               source_file="src/kg/compile-v2.ts", source_location="L1")
    G.add_node("sym", label="compile()", file_type="code",
               source_file="src/kg/compile-v2.ts", source_location="L68")
    G.add_node("other", label="compile()", file_type="code",
               source_file="src/legacy/compile.py", source_location="L1")
    idx = label_index(G)
    # Full-path form (already worked).
    c, _, mt, _ = resolve_focus(G, idx, "src/kg/compile-v2.ts/compile")
    assert c == "sym" and mt == "exact"
    # File-stem form (lap-7 fix).
    c, _, mt, _ = resolve_focus(G, idx, "kg/compile-v2/compile")
    assert c == "sym" and mt == "exact", f"stem form should hit sym, got {c!r}"
    # Bare basename stem (one-segment prefix).
    c, _, mt, _ = resolve_focus(G, idx, "compile-v2/compile")
    assert c == "sym" and mt == "exact", f"bare stem form should hit sym, got {c!r}"


def test_rank_prefers_symbol_over_rationale():
    """Lap-7 B3: `@FOO_BAR` was landing on a `[rationale]` docstring node
    instead of the symbol it documented. _rank_match must rank symbol
    nodes above rationale on the same label."""
    G = nx.DiGraph()
    G.add_node("rat", label="ADJECTIVES", file_type="rationale",
               source_file="src/m.py", source_location="L1")
    G.add_node("sym", label="ADJECTIVES", file_type="code",
               source_file="src/m.py", source_location="L10")
    idx = label_index(G)
    # Both labels match exactly — multi-exact returns sorted candidates.
    chosen, candidates, _, _ = resolve_focus(G, idx, "ADJECTIVES")
    assert chosen is None  # ambiguous between symbol and rationale
    assert candidates[0] == "sym", (
        f"expected symbol first, rationale demoted; got {candidates}"
    )


def test_path_excludes_type_ref_by_default(tmp_path):
    """Lap-7 friction 3: `path A B` was routing through `type_ref` edges
    (parameter type signatures), reporting paths like
    `compile() --calls→ X --type_ref→ Foo --method→ helper()`. That's
    literal connectedness, not call-graph reachability. With default
    `--edges reach`, type_ref hops are blocked.

    Fixture: a directed chain `compile → Foo (type_ref) → helper (method)`.
    Without the type_ref hop there's no path; with it there is. Default
    must block, `--edges all` must allow."""
    import json as _json
    import subprocess
    nodes = [
        {"id": "compile", "label": "compile()", "file_type": "code",
         "source_file": "a.ts", "source_location": "L1"},
        {"id": "FooT", "label": "Foo", "file_type": "code",
         "source_file": "b.ts", "source_location": "L20",
         "node_kind": "type_alias"},
        {"id": "helper", "label": "helper()", "file_type": "code",
         "source_file": "b.ts", "source_location": "L40"},
    ]
    links = [
        # compile's signature mentions Foo (type_ref); Foo class contains
        # `helper` as a method. Walking compile→Foo→helper requires a
        # type_ref hop that crosses the call/use semantics boundary.
        {"source": "compile", "target": "FooT", "relation": "type_ref",
         "confidence": "EXTRACTED"},
        {"source": "FooT", "target": "helper", "relation": "method",
         "confidence": "EXTRACTED"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False,
         "graph": {}, "nodes": nodes, "links": links}), encoding="utf-8")
    res = subprocess.run(
        ["graphify", "path", "compile()", "helper()",
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    out = res.stdout + res.stderr
    # Default `--edges reach` must block the type_ref hop and either
    # report no semantic path or surface that the only route is via
    # type-ref. It must NOT print a clean "Shortest path" line.
    assert ("No semantic path" in out
            or "type-ref" in out
            or "No path found" in out), f"path used type_ref cheat:\n{out}"
    assert "Shortest path" not in out or "type-ref" in out, (
        f"path silently routed through type_ref:\n{out}"
    )

    # `--edges all` should opt back in: the chain compile→Foo→helper is now valid.
    res2 = subprocess.run(
        ["graphify", "path", "compile()", "helper()",
         "--graph", str(graph_dir / "graph.json"), "--edges", "all"],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    out2 = res2.stdout + res2.stderr
    assert "type_ref" in out2, (
        f"--edges all should surface type_ref hops:\n{out2}"
    )


def test_navigate_no_archived_filter(tmp_path, monkeypatch):
    """Lap-7 S3: `--no-archived` hides archive-path candidates from
    listings and surfaces the count. `--archived-only` inverts."""
    import json as _json
    from graphify.navigate import navigate
    nodes = [
        {"id": "f1", "label": "compile()", "file_type": "code",
         "source_file": "src/main.ts", "source_location": "L1"},
        {"id": "f2", "label": "compile()", "file_type": "code",
         "source_file": "src/legacy/compile.py", "source_location": "L1"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False,
         "graph": {}, "nodes": nodes, "links": []}), encoding="utf-8")
    out = navigate(["@compile"], graph_path=str(graph_dir / "graph.json"),
                   session=False, fmt="text", archived_mode="no")
    # Only the active candidate survives the filter; the archived count
    # is announced rather than silently dropped.
    assert "src/main.ts" in out
    assert "legacy/compile.py" not in out, f"archived leaked through:\n{out}"
    assert "1 archived hidden" in out, (
        f"hidden count must surface, got:\n{out}"
    )
    out2 = navigate(["@compile"], graph_path=str(graph_dir / "graph.json"),
                    session=False, fmt="text", archived_mode="only")
    assert "legacy/compile.py" in out2
    assert "src/main.ts" not in out2


def test_focus_header_shows_archived_tag(tmp_path):
    """Lap-7 friction 6: focused-node header was missing `[archived]` tag
    while child listings showed it — inconsistent. Now both surfaces."""
    import json as _json
    from graphify.navigate import navigate
    nodes = [
        {"id": "f1", "label": "doomed()", "file_type": "code",
         "source_file": "src/legacy/x.py", "source_location": "L1"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False,
         "graph": {}, "nodes": nodes, "links": []}), encoding="utf-8")
    out = navigate(["@doomed"], graph_path=str(graph_dir / "graph.json"),
                   session=False, fmt="text")
    # Header line starts with `@ <label>` — `[archived]` must appear on it.
    header_line = next((ln for ln in out.splitlines() if ln.startswith("@ ")), "")
    assert "[archived]" in header_line, (
        f"expected [archived] tag in focus header:\n{header_line}"
    )


def test_methods_on_file_returns_directive(tmp_path):
    """Lap-8 friction 4: `@file.ts methods` returned methods(0) and a
    chained `1` errored. Files don't carry methods. The pivot should
    surface an n/a directive pointing at `contains` instead."""
    import json as _json
    from graphify.navigate import navigate
    nodes = [
        {"id": "f1", "label": "engine.ts", "file_type": "code",
         "source_file": "engine.ts", "source_location": "L1"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False,
         "graph": {}, "nodes": nodes, "links": []}), encoding="utf-8")
    out = navigate(["@engine.ts", "methods"],
                   graph_path=str(graph_dir / "graph.json"),
                   session=False, fmt="text")
    assert "n/a on file nodes" in out
    assert "contains" in out, (
        f"directive should point at contains:\n{out}"
    )


def test_path_edges_calls_blocks_imports(tmp_path):
    """Lap-8 friction 3: even file-involved paths under `--edges reach`
    routed through `imports → contains` and presented as a "path". The
    `--edges calls` mode walks call-graph proper only (calls + method +
    impl_of + inherits) and reports no path when only structural edges
    connect endpoints."""
    import json as _json
    import subprocess
    nodes = [
        {"id": "fA", "label": "a.ts", "file_type": "code",
         "source_file": "a.ts", "source_location": "L1"},
        {"id": "fB", "label": "b.ts", "file_type": "code",
         "source_file": "b.ts", "source_location": "L1"},
        {"id": "symA", "label": "compile()", "file_type": "code",
         "source_file": "a.ts", "source_location": "L10"},
        {"id": "symB", "label": "embed()", "file_type": "code",
         "source_file": "b.ts", "source_location": "L20"},
    ]
    links = [
        {"source": "fA", "target": "symA", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "fB", "target": "symB", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "fA", "target": "fB", "relation": "imports",
         "confidence": "EXTRACTED"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False,
         "graph": {}, "nodes": nodes, "links": links}), encoding="utf-8")
    # `--edges reach` (default) finds the file-graph route since one
    # endpoint is a file. `--edges calls` should NOT — there's no call
    # edge between any of these nodes.
    res = subprocess.run(
        ["graphify", "path", "a.ts", "embed()",
         "--graph", str(graph_dir / "graph.json"), "--edges", "calls"],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    out = res.stdout + res.stderr
    assert "Shortest path" not in out or "co-location" in out, (
        f"--edges calls leaked structural path:\n{out}"
    )
    assert ("No semantic path" in out
            or "No path found" in out
            or "co-location" in out), f"unexpected --edges calls output:\n{out}"


def test_coc_excludes_files_by_default(tmp_path):
    """Lap-8 friction 7: `coc` returned 7/25 file nodes by default.
    Symbol-level co-occurrence is the typical intent. Default hides
    file hubs; --include-files widens."""
    import json as _json
    from graphify.navigate import navigate
    nodes = [
        {"id": "focus", "label": "DecodeEngine", "file_type": "code",
         "community": 1,
         "source_file": "engine.ts", "source_location": "L1"},
        # File-level hubs (label == basename of source_file) — should be hidden.
        {"id": "fhub1", "label": "compile.ts", "file_type": "code",
         "community": 1,
         "source_file": "compile.ts", "source_location": "L1"},
        {"id": "fhub2", "label": "decode.ts", "file_type": "code",
         "community": 1,
         "source_file": "decode.ts", "source_location": "L1"},
        # Symbol nodes — should remain in coc.
        {"id": "s1", "label": "buildEngine()", "file_type": "code",
         "community": 1,
         "source_file": "build.ts", "source_location": "L10"},
        {"id": "s2", "label": "tearDown()", "file_type": "code",
         "community": 1,
         "source_file": "build.ts", "source_location": "L20"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False,
         "graph": {}, "nodes": nodes, "links": []}), encoding="utf-8")
    # Default: file hubs hidden, symbols visible.
    out = navigate(["@DecodeEngine", "coc"],
                   graph_path=str(graph_dir / "graph.json"),
                   session=False, fmt="text")
    assert "buildEngine()" in out
    assert "compile.ts" not in out, f"file hub leaked into coc:\n{out}"
    assert "files hidden" in out, (
        f"hidden count must surface, got:\n{out}"
    )
    # --include-files: file hubs visible.
    out2 = navigate(["@DecodeEngine", "coc"],
                    graph_path=str(graph_dir / "graph.json"),
                    session=False, fmt="text", include_files=True)
    assert "compile.ts" in out2
    assert "decode.ts" in out2


def test_dupe_label_collapse_default(tmp_path):
    """Lap-9 #3: groups of >=5 same-label items collapse into one row.
    `[N]` picks the group's first member; cursor.last_listing length
    matches visible row count, not underlying node count."""
    import json as _json
    from graphify.navigate import navigate
    nodes = [
        {"id": "tgt", "label": "embeddingGenerate()", "file_type": "code",
         "source_file": "lib/decode.ts", "source_location": "L1"},
    ]
    # 6 callers all labeled `stagedDecode()` in different files.
    links = []
    for i in range(6):
        cid = f"call{i}"
        nodes.append({
            "id": cid, "label": "stagedDecode()", "file_type": "code",
            "source_file": f"experiments/triadic-{chr(ord('a') + i)}/decode.ts",
            "source_location": f"L{100 + i}",
        })
        links.append({
            "source": cid, "target": "tgt", "relation": "calls",
            "confidence": "EXTRACTED",
        })
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False,
         "graph": {}, "nodes": nodes, "links": links}), encoding="utf-8")
    out = navigate(["@embeddingGenerate", "in"],
                   graph_path=str(graph_dir / "graph.json"),
                   session=False, fmt="text")
    # The collapse banner names the count of folded rows and the listing
    # has exactly one stagedDecode() row with `×6`.
    assert "collapsed 5 dupe-label rows" in out, f"missing banner:\n{out}"
    assert "stagedDecode()" in out
    assert "×6" in out, f"expected `×6` collapsed marker:\n{out}"
    # Sample paths surfaced inline so the differentiating field is visible.
    assert "samples:" in out
    assert "triadic-a/decode.ts" in out

    # --no-collapse expands the listing back to 6 individual rows.
    out2 = navigate(["@embeddingGenerate", "in"],
                    graph_path=str(graph_dir / "graph.json"),
                    session=False, fmt="text", collapse_dupes=False)
    assert "×6" not in out2
    assert out2.count("stagedDecode()") >= 6


def test_closure_dispatch_hint_fires(tmp_path):
    """Lap-9 #2: method-shaped node with 0 ext-in but high inferred-in
    should surface a closure-dispatch hint instead of the generic
    "no direct edges" steering."""
    import json as _json
    from graphify.navigate import navigate
    nodes = [
        {"id": "tgt", "label": ".probeForward()", "file_type": "code",
         "source_file": "engine.ts", "source_location": "L20"},
    ]
    links = []
    # 5 INFERRED callers — extracted_only filter hides them but the
    # drop count drives the hint.
    for i in range(5):
        cid = f"c{i}"
        nodes.append({
            "id": cid, "label": f"caller{i}()", "file_type": "code",
            "source_file": f"a{i}.ts", "source_location": f"L{i}",
        })
        links.append({
            "source": cid, "target": "tgt", "relation": "calls",
            "confidence": "INFERRED", "confidence_score": 0.80,
        })
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False,
         "graph": {}, "nodes": nodes, "links": links}), encoding="utf-8")
    out = navigate(["@.probeForward"],
                   graph_path=str(graph_dir / "graph.json"),
                   session=False, fmt="text")
    assert "closure dispatch likely" in out, (
        f"expected closure-dispatch hint:\n{out}"
    )


def test_unique_cross_file_call_marked_extracted(tmp_path):
    """Lap-9 #1: a callee name that resolves to exactly one node across
    all files gets an EXTRACTED edge in the global resolver. Ambiguous
    names stay INFERRED."""
    import textwrap
    from graphify.extract import extract
    pkg = tmp_path / "src"
    pkg.mkdir()
    # caller.ts calls `obj.uniqueClosureMethod()` — `uniqueClosureMethod`
    # is the closure-method name and only exists once across the
    # extracted set.
    (pkg / "caller.ts").write_text(textwrap.dedent("""
        function consume(eng) {
            eng.uniqueClosureMethod();
        }
    """))
    # factory.ts defines a function `uniqueClosureMethod()` (the only
    # node in the codebase with this name).
    (pkg / "factory.ts").write_text(textwrap.dedent("""
        function uniqueClosureMethod() { return 1; }
        function buildEngine() {
            return { uniqueClosureMethod };
        }
    """))
    res = extract([pkg / "caller.ts", pkg / "factory.ts"])
    edges = res["edges"]
    # The cross-file call from consume() to uniqueClosureMethod() must
    # be EXTRACTED (unique name resolution).
    by_label = {n["id"]: n["label"] for n in res["nodes"]}
    matched = [e for e in edges
               if by_label.get(e["source"], "").startswith("consume")
               and by_label.get(e["target"], "").startswith("uniqueClosureMethod")
               and e["relation"] == "calls"]
    assert matched, f"no calls edge found between consume and uniqueClosureMethod"
    assert any(e["confidence"] == "EXTRACTED" for e in matched), (
        f"unique cross-file call should be EXTRACTED, got: "
        f"{[e['confidence'] for e in matched]}"
    )


def test_inline_hidden_count_format():
    """Lap-8 F5: hidden counts render inline as `↗in(0+41inf)` rather than
    parenthetical `(0; +41 INFERRED hidden)`. Verify via _glyph closure."""
    # _glyph is defined inside _render_frontier_text. Test via the rendered
    # output: build a frontier with INFERRED in-edges and check the format.
    import json as _json
    from graphify.navigate import navigate
    nodes = [
        {"id": "tgt", "label": "target()", "file_type": "code",
         "source_file": "a.ts", "source_location": "L10"},
        {"id": "src1", "label": "caller1()", "file_type": "code",
         "source_file": "b.ts", "source_location": "L1"},
        {"id": "src2", "label": "caller2()", "file_type": "code",
         "source_file": "c.ts", "source_location": "L1"},
    ]
    links = [
        # Both inferred — extracted_only default drops them, so count=0
        # but +2inf hidden.
        {"source": "src1", "target": "tgt", "relation": "calls",
         "confidence": "INFERRED", "confidence_score": 0.80},
        {"source": "src2", "target": "tgt", "relation": "calls",
         "confidence": "INFERRED", "confidence_score": 0.85},
    ]
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        from pathlib import Path as _P
        graph_dir = _P(td) / "graphify-out"
        graph_dir.mkdir()
        (graph_dir / "graph.json").write_text(_json.dumps(
            {"directed": True, "multigraph": False,
             "graph": {}, "nodes": nodes, "links": links}), encoding="utf-8")
        out = navigate(["@target"], graph_path=str(graph_dir / "graph.json"),
                       session=False, fmt="text")
    # Inline form: `↗in(0+2inf...)`. The old form was `(0; +2 ... hidden)`.
    assert "↗in(0+2inf" in out, (
        f"expected inline `↗in(0+2inf...)` form, got:\n{out}"
    )


def test_underscore_prefix_qualifier_resolves():
    """Lap-10 bug #1: `tools/foo.py/_classify_file` silently failed because
    `_label_matches_basename` stripped leading `_` from the LABEL
    (`_classify_file()` → `classify_file`) but compared against the
    target verbatim (`_classify_file`). The fix strips both sides
    symmetrically, so both `_classify_file` and `classify_file` (with
    or without a leading underscore in the qualifier) hit the same node."""
    G = nx.DiGraph()
    # Same-basename collision (forces the path-qualifier branch).
    G.add_node("a", label="_classify_file()", file_type="code",
               source_file="tools/dynamical_fingerprint.py",
               source_location="L100")
    G.add_node("b", label="_classify_file()", file_type="code",
               source_file="tools/legacy/sniffer.py",
               source_location="L50")
    idx = label_index(G)
    # Underscore-form qualifier — the user-typed shape that broke pre-fix.
    chosen, candidates, mt, _ = resolve_focus(
        G, idx, "tools/dynamical_fingerprint.py/_classify_file")
    assert chosen == "a", (
        f"underscore-prefix qualifier should hit `a`, "
        f"got chosen={chosen!r} candidates={candidates}"
    )
    assert mt == "exact"
    # Without underscore — should still resolve via the strip-from-label fallback.
    chosen2, _, _, _ = resolve_focus(
        G, idx, "tools/dynamical_fingerprint.py/classify_file")
    assert chosen2 == "a", "non-underscore form should also hit `a`"
    # Stem-form (extension elided) with underscore.
    chosen3, _, _, _ = resolve_focus(
        G, idx, "tools/dynamical_fingerprint/_classify_file")
    assert chosen3 == "a", "stem-form + underscore should also hit `a`"


def test_module_config_hint_skips_function_nodes(tmp_path):
    """Lap-10 bug #2: the module-config hint (`file is N lines but 0 decls`)
    was firing on function-leaf nodes inside large files because the gate
    only checked `file_type == 'code'`. Function nodes inherit `code`
    typing too, and have 0 contains/0 methods by definition. The fix
    gates on `label == basename(source_file)` so the hint only fires when
    focus IS the file hub."""
    import json as _json
    from graphify.navigate import navigate
    # The function node sits in a 749-line file — sibling decls exist
    # at file level but the function itself is a leaf.
    nodes = [
        {"id": "filehub", "label": "dynamical_fingerprint.py",
         "file_type": "code",
         "source_file": "tools/dynamical_fingerprint.py",
         "source_location": "L1"},
        {"id": "fn", "label": "classify_residual_axis()",
         "file_type": "code",
         "source_file": "tools/dynamical_fingerprint.py",
         "source_location": "L300"},
    ]
    # 21 sibling decls (also leaves) so the file isn't actually
    # decl-empty — that's the misread the old hint was producing.
    links = []
    for i in range(21):
        sid = f"sib{i}"
        nodes.append({
            "id": sid, "label": f"sibling_{i}()",
            "file_type": "code",
            "source_file": "tools/dynamical_fingerprint.py",
            "source_location": f"L{100 + i}",
        })
        links.append({"source": "filehub", "target": sid,
                      "relation": "contains",
                      "confidence": "EXTRACTED",
                      "confidence_score": 1.0})
    links.append({"source": "filehub", "target": "fn",
                  "relation": "contains",
                  "confidence": "EXTRACTED",
                  "confidence_score": 1.0})
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False,
         "graph": {}, "nodes": nodes, "links": links}), encoding="utf-8")
    # Mock the file-meta lookup so _file_meta returns >50 lines (the
    # other gate condition). Easiest: write a synthetic source file.
    src_dir = tmp_path / "tools"
    src_dir.mkdir()
    (src_dir / "dynamical_fingerprint.py").write_text(
        "\n".join(["# line"] * 200), encoding="utf-8")
    import os as _os
    cwd = _os.getcwd()
    _os.chdir(tmp_path)
    try:
        out = navigate(["@classify_residual_axis"],
                       graph_path=str(graph_dir / "graph.json"),
                       session=False, fmt="text")
    finally:
        _os.chdir(cwd)
    # The misfire signature: hint claims "0 decls — likely a const/data module"
    # while focus is a function. After the gate, this hint should NOT appear.
    assert "0 decls" not in out, (
        f"module-config hint should NOT fire on function-node focus, got:\n{out}"
    )


def test_module_config_hint_still_fires_on_file_hub(tmp_path):
    """Sanity check the lap-10 gate didn't kill the legitimate hint:
    when focus IS the file hub (label == basename) and the file has 0
    decls but >50 lines, the data-module hint should still fire."""
    import json as _json
    from graphify.navigate import navigate
    nodes = [
        {"id": "filehub", "label": "config.ts", "file_type": "code",
         "source_file": "src/config.ts", "source_location": "L1"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False,
         "graph": {}, "nodes": nodes, "links": []}), encoding="utf-8")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "config.ts").write_text(
        "\n".join(["// line"] * 200), encoding="utf-8")
    import os as _os
    cwd = _os.getcwd()
    _os.chdir(tmp_path)
    try:
        out = navigate(["@config.ts"],
                       graph_path=str(graph_dir / "graph.json"),
                       session=False, fmt="text")
    finally:
        _os.chdir(cwd)
    assert "0 decls" in out, (
        f"data-module hint should fire on file-hub focus with 0 decls "
        f"+ >50 lines, got:\n{out}"
    )


def test_read_op_in_ops_hint_when_enabled():
    """Lap-10 F4: the `read` op (lap-3 wishlist) was implemented but never
    surfaced in the cheat-sheet, so consumers kept reaching for
    `parent → contains --bodies` even when sitting on the function they
    wanted. Verify the discoverable ops line includes `read` when
    --ops-hint is enabled.

    Lap-11: ops cheat-sheet defaults OFF (chained agent calls don't need
    repetitive verb listings); the line is opt-in via --ops-hint or
    appears unconditionally on the empty-cursor first-contact message.
    Test the opt-in path here."""
    import json as _json
    import tempfile
    from pathlib import Path as _P
    from graphify.navigate import navigate
    nodes = [
        {"id": "n1", "label": "foo()", "file_type": "code",
         "source_file": "a.py", "source_location": "L1"},
    ]
    with tempfile.TemporaryDirectory() as td:
        graph_dir = _P(td) / "graphify-out"
        graph_dir.mkdir()
        (graph_dir / "graph.json").write_text(_json.dumps(
            {"directed": True, "multigraph": False,
             "graph": {}, "nodes": nodes, "links": []}), encoding="utf-8")
        out = navigate(["@foo"], graph_path=str(graph_dir / "graph.json"),
                       session=False, fmt="text", show_ops_hint=True)
    assert " read" in out, (
        f"ops hint with --ops-hint enabled should surface `read` so it's discoverable, got:\n{out}"
    )


def test_ops_hint_off_by_default():
    """Lap-11: cheat-sheet noise on routine calls — flipped to default-off.
    --ops-hint is the new opt-in. Verify a default-args navigate call
    omits the `ops:` line, and that --ops-hint adds it back."""
    import json as _json
    import tempfile
    from pathlib import Path as _P
    from graphify.navigate import navigate
    nodes = [
        {"id": "n1", "label": "foo()", "file_type": "code",
         "source_file": "a.py", "source_location": "L1"},
    ]
    with tempfile.TemporaryDirectory() as td:
        graph_dir = _P(td) / "graphify-out"
        graph_dir.mkdir()
        (graph_dir / "graph.json").write_text(_json.dumps(
            {"directed": True, "multigraph": False,
             "graph": {}, "nodes": nodes, "links": []}), encoding="utf-8")
        gp = str(graph_dir / "graph.json")
        default_out = navigate(["@foo"], graph_path=gp, session=False, fmt="text")
        opted_in = navigate(["@foo"], graph_path=gp, session=False, fmt="text",
                            show_ops_hint=True)
    assert "ops:" not in default_out, (
        f"default navigate should NOT include `ops:` cheat-sheet, got:\n{default_out}"
    )
    assert "ops:" in opted_in, (
        f"--ops-hint should include `ops:` cheat-sheet, got:\n{opted_in}"
    )


def test_empty_cursor_message_includes_cheat_sheet():
    """Lap-11: with cheat-sheet defaulting off, first-contact discoverability
    moves to the empty-cursor path: a navigate() call with no @-focus
    surfaces the ops as part of `no cursor — focus a node with @<label>`
    so a brand-new agent doesn't need to know `--ops-hint` exists.
    Verify the empty-cursor frontier always carries the verb list."""
    import json as _json
    import tempfile
    from pathlib import Path as _P
    from graphify.navigate import navigate
    with tempfile.TemporaryDirectory() as td:
        graph_dir = _P(td) / "graphify-out"
        graph_dir.mkdir()
        (graph_dir / "graph.json").write_text(_json.dumps(
            {"directed": True, "multigraph": False,
             "graph": {}, "nodes": [
                 {"id": "n1", "label": "foo()", "file_type": "code",
                  "source_file": "a.py", "source_location": "L1"},
             ], "links": []}), encoding="utf-8")
        # No ops → frontier with no current → empty-cursor message path.
        out = navigate([], graph_path=str(graph_dir / "graph.json"),
                       session=False, fmt="text")
    assert "@<label>" in out and "in | out | methods" in out, (
        f"empty-cursor message should embed the cheat-sheet, got:\n{out}"
    )


def _write_graph(graph_dir, nodes, links):
    """Test helper: serialize a node/link graph at graph_dir/graph.json."""
    import json as _json
    graph_dir.mkdir(exist_ok=True)
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False, "graph": {},
         "nodes": nodes, "links": links}), encoding="utf-8")


def test_dependents_alias_routes_through_calls(tmp_path, monkeypatch):
    """Lap-11: `dependents` is sugar for transitive callers (in --kind=calls
    --depth=3). Two-hop call chain should surface as a single dependents
    listing rather than requiring three pivots. Confirms the alias resolves
    via the in/calls codepath and applies the implicit depth boost."""
    import json as _json
    from graphify.navigate import navigate
    nodes = [
        {"id": "leaf", "label": "leaf()", "file_type": "code",
         "source_file": "a.py", "source_location": "L1"},
        {"id": "mid", "label": "mid()", "file_type": "code",
         "source_file": "a.py", "source_location": "L10"},
        {"id": "top", "label": "top()", "file_type": "code",
         "source_file": "a.py", "source_location": "L20"},
    ]
    links = [
        {"source": "mid", "target": "leaf", "relation": "calls",
         "confidence": "EXTRACTED"},
        {"source": "top", "target": "mid", "relation": "calls",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    out = navigate(["@leaf", "dependents"], session=False, fmt="text")
    # Both `mid` and `top` should appear (transitive-3); without the
    # depth boost only `mid` would appear under callers.
    assert "mid()" in out, f"`mid` not in dependents output:\n{out}"
    assert "top()" in out, (
        f"transitive boost missed `top`; depth was probably 1:\n{out}"
    )
    # Glyph should announce the depth so the agent knows it ran transitively.
    assert "depth≤" in out, f"depth indicator missing:\n{out}"


def test_dependencies_alias_routes_callees(tmp_path, monkeypatch):
    """Mirror of dependents: `dependencies` walks outbound calls transitively."""
    import json as _json
    from graphify.navigate import navigate
    nodes = [
        {"id": "leaf", "label": "leaf()", "file_type": "code",
         "source_file": "a.py", "source_location": "L1"},
        {"id": "mid", "label": "mid()", "file_type": "code",
         "source_file": "a.py", "source_location": "L10"},
        {"id": "top", "label": "top()", "file_type": "code",
         "source_file": "a.py", "source_location": "L20"},
    ]
    links = [
        {"source": "mid", "target": "leaf", "relation": "calls",
         "confidence": "EXTRACTED"},
        {"source": "top", "target": "mid", "relation": "calls",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    out = navigate(["@top", "dependencies"], session=False, fmt="text")
    assert "mid()" in out and "leaf()" in out, (
        f"transitive callees should surface `mid` and `leaf`:\n{out}"
    )


def test_explain_cost_short_circuits_listing(tmp_path, monkeypatch):
    """`--explain-cost` returns a `would return N nodes ≈ K bytes` preview
    instead of rendering the listing. Cursor state is unchanged, so a
    follow-up call without the flag commits."""
    import json as _json
    from graphify.navigate import navigate
    nodes = [
        {"id": "f", "label": "ClassA", "file_type": "code",
         "source_file": "a.py", "source_location": "L1", "community": 0},
    ]
    # Ten neighbors via methods to populate a non-trivial coc-style listing.
    for i in range(10):
        nodes.append({
            "id": f"m{i}", "label": f"m{i}()", "file_type": "code",
            "source_file": "a.py", "source_location": f"L{i+5}",
            "community": 0,
        })
    links = [
        {"source": "f", "target": f"m{i}", "relation": "method",
         "confidence": "EXTRACTED"} for i in range(10)
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    out = navigate(["@ClassA", "methods"], session=False, fmt="text",
                   explain_cost=True)
    assert "would return" in out, f"preview text missing: {out}"
    assert "10 node" in out, f"node count wrong in preview: {out}"
    # No row-level details — preview should not enumerate.
    assert "[ 1]" not in out and "[1]" not in out, (
        f"explain-cost should not render listing rows:\n{out}"
    )


def test_md_mode_renders_markdown_links(tmp_path, monkeypatch):
    """`--md` wraps the focus label as `[label](file:line)` so IDE / Claude
    Code can make it clickable. Verify the link shape on a frontier
    header."""
    import json as _json
    from graphify.navigate import navigate
    nodes = [
        {"id": "f", "label": "ClassA", "file_type": "code",
         "source_file": "a.py", "source_location": "L42", "community": 0},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, [])
    monkeypatch.chdir(tmp_path)
    out = navigate(["@ClassA"], session=False, fmt="text", md=True)
    assert "[ClassA](a.py:42)" in out, (
        f"md-mode should render `[label](src:line)` link, got:\n{out}"
    )


def test_md_mode_off_yields_plain_label(tmp_path, monkeypatch):
    """Md is opt-in. Plain mode should not introduce link syntax that breaks
    grep-friendliness for non-IDE consumers."""
    import json as _json
    from graphify.navigate import navigate
    nodes = [
        {"id": "f", "label": "ClassA", "file_type": "code",
         "source_file": "a.py", "source_location": "L42", "community": 0},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, [])
    monkeypatch.chdir(tmp_path)
    out = navigate(["@ClassA"], session=False, fmt="text")
    assert "[ClassA](" not in out, (
        f"plain mode should NOT emit md links:\n{out}"
    )


def test_coc_summary_returns_structural_shape(tmp_path, monkeypatch):
    """`coc summary` returns top hubs / composition / edge mix instead of
    enumerating members. Should be cheap on big communities."""
    import json as _json
    from graphify.navigate import navigate
    # Community 0 with one big hub plus several leaves.
    nodes = [
        {"id": "hub", "label": "Hub()", "file_type": "code",
         "source_file": "a.py", "source_location": "L1", "community": 0},
        {"id": "a.py", "label": "a.py", "file_type": "code",
         "source_file": "a.py", "source_location": "L1", "community": 0},
    ]
    for i in range(8):
        nodes.append({
            "id": f"leaf{i}", "label": f"leaf{i}()", "file_type": "code",
            "source_file": "a.py", "source_location": f"L{i+5}",
            "community": 0,
        })
    links = [
        {"source": "hub", "target": f"leaf{i}", "relation": "calls",
         "confidence": "EXTRACTED"} for i in range(8)
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    out = navigate(["@Hub()", "coc", "summary"], session=False, fmt="text")
    assert "coc summary" in out, f"summary header missing:\n{out}"
    assert "composition" in out, f"composition line missing:\n{out}"
    assert "Hub()" in out, f"top hub not surfaced:\n{out}"
    # No row enumeration in summary mode.
    assert "[ 1]" not in out and "[1]" not in out, (
        f"summary should not enumerate members:\n{out}"
    )


def test_node_kind_interface_hint(tmp_path, monkeypatch):
    """Lap-11 hint: focused on a TS interface, the steering hint should
    name `in --kind=impl_of` as the way to find implementations."""
    import json as _json
    from graphify.navigate import navigate
    nodes = [
        {"id": "iface", "label": "Pet", "file_type": "code",
         "source_file": "a.ts", "source_location": "L1",
         "node_kind": "interface", "community": 0},
        {"id": "impl", "label": "Cat", "file_type": "code",
         "source_file": "a.ts", "source_location": "L20",
         "node_kind": "class", "community": 0},
    ]
    links = [
        {"source": "impl", "target": "iface", "relation": "impl_of",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    out = navigate(["@Pet"], session=False, fmt="text")
    assert "interface" in out and "impl_of" in out, (
        f"interface hint should name `in --kind=impl_of`:\n{out}"
    )


def test_node_kind_type_alias_hint(tmp_path, monkeypatch):
    """Type-alias focus surfaces a hint pointing at `in --kind=type_ref`."""
    import json as _json
    from graphify.navigate import navigate
    nodes = [
        {"id": "ta", "label": "UserId", "file_type": "code",
         "source_file": "a.ts", "source_location": "L1",
         "node_kind": "type_alias", "community": 0},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, [])
    monkeypatch.chdir(tmp_path)
    out = navigate(["@UserId"], session=False, fmt="text")
    assert "type alias" in out and "type_ref" in out, (
        f"type_alias hint should name `in --kind=type_ref`:\n{out}"
    )


def test_transitive_routes_file_node_through_contains(tmp_path, monkeypatch):
    """`--transitive` on a file node with 0 direct out edges walks via
    contains then aggregates the contained nodes' out edges. Single call
    instead of `parent → contains → pick → out`."""
    import json as _json
    from graphify.navigate import navigate
    nodes = [
        {"id": "file", "label": "a.py", "file_type": "code",
         "source_file": "a.py", "source_location": "L1", "community": 0},
        {"id": "fn", "label": "foo()", "file_type": "code",
         "source_file": "a.py", "source_location": "L10", "community": 0},
        {"id": "tgt", "label": "bar()", "file_type": "code",
         "source_file": "b.py", "source_location": "L1", "community": 1},
    ]
    links = [
        {"source": "file", "target": "fn", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "fn", "target": "tgt", "relation": "calls",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    # Without transitive: out from file → 0 results
    plain = navigate(["@a.py", "out"], session=False, fmt="text")
    assert "empty" in plain or "out: empty" in plain or "out (0" in plain, (
        f"plain `out` from file should be empty:\n{plain}"
    )
    # With transitive: should aggregate fn's out → bar()
    rt = navigate(["@a.py", "out"], session=False, fmt="text", transitive=True)
    assert "bar()" in rt, (
        f"transitive `out` should surface bar() via contains→out:\n{rt}"
    )
    assert "via contains" in rt, (
        f"transitive should announce route-through-contains:\n{rt}"
    )


def test_read_walker_handles_multiline_def_signature(tmp_path, monkeypatch):
    """Regression for lap-11 bug: `peek`/`read` bailed at the end of a
    multi-line def signature because the deeply-indented continuation
    lines fixed body_indent at the param column, then the actual body
    at a smaller indent dedented below it and the walker broke.

    Reporter: classify_residual_axis() spans 30 body lines but read
    returned just the 3 signature lines. Single-line defs were unaffected.

    Fix: track paren depth across lines, skip continuation lines before
    establishing body_indent."""
    src = (tmp_path / "a.py")
    src.write_text(
        "def classify_residual_axis(signal_metrics,\n"
        "                           axis_entry,\n"
        "                           anchor_scores):\n"
        "    \"\"\"Score signal_metrics on each pole.\"\"\"\n"
        "    score = 0.0\n"
        "    pole_idx = 0\n"
        "    for k, v in signal_metrics.items():\n"
        "        score += v\n"
        "        pole_idx += 1\n"
        "    return score, pole_idx, len(anchor_scores)\n"
        "\n"
        "\n"
        "def next_unrelated():\n"
        "    pass\n",
        encoding="utf-8",
    )
    from graphify.navigate import _read_body_full
    body, ln, trunc = _read_body_full(str(src), "L1", max_lines=200)
    assert ln == 1
    # Body must include the multi-line signature AND the actual body
    # AND nothing past the next sibling def.
    text = "\n".join(body)
    assert "def classify_residual_axis" in text
    assert "score = 0.0" in text, (
        f"body walker stopped before reaching the body indent; got:\n{text}"
    )
    assert "return score, pole_idx" in text, (
        f"body walker stopped before reaching the return; got:\n{text}"
    )
    assert "def next_unrelated" not in text, (
        f"body walker leaked into the next sibling def; got:\n{text}"
    )


def test_read_walker_handles_multiline_class_def(tmp_path, monkeypatch):
    """Same fix applies to multi-line class definitions with bases on
    several lines."""
    src = (tmp_path / "a.py")
    src.write_text(
        "class Foo(\n"
        "    SomeBase,\n"
        "    AnotherBase,\n"
        "):\n"
        "    x = 1\n"
        "    def m(self):\n"
        "        return 42\n"
        "\n"
        "class Bar:\n"
        "    pass\n",
        encoding="utf-8",
    )
    from graphify.navigate import _read_body_full
    body, ln, trunc = _read_body_full(str(src), "L1", max_lines=200)
    text = "\n".join(body)
    assert "x = 1" in text and "return 42" in text, (
        f"multi-line class header confused body walker:\n{text}"
    )
    assert "class Bar" not in text, (
        f"walker leaked past Foo into Bar:\n{text}"
    )


def test_read_walker_handles_multiline_ts_arrow(tmp_path, monkeypatch):
    """TypeScript multi-line signatures use `{` to open the body. Same
    paren-tracking fix should keep us from setting body_indent on the
    deeply-indented argument-continuation lines."""
    src = (tmp_path / "a.ts")
    src.write_text(
        "function compileEngine(\n"
        "  spec: Spec,\n"
        "  opts: Opts,\n"
        ") {\n"
        "  const x = 1;\n"
        "  return spec;\n"
        "}\n"
        "\n"
        "function other() { return 0; }\n",
        encoding="utf-8",
    )
    from graphify.navigate import _read_body_full
    body, ln, _ = _read_body_full(str(src), "L1", max_lines=200)
    text = "\n".join(body)
    assert "const x = 1" in text and "return spec" in text, (
        f"TS multi-line sig confused walker:\n{text}"
    )


def test_unknown_op_suggests_at_prefix_on_path_shape(tmp_path, monkeypatch):
    """Path-shaped first arg without `@` prefix should hint at the @-form
    rather than just spitting `unknown op`. Lap-11 friction: agents type
    `graphify navigate "tools/x/y" deps` and get an unhelpful error."""
    import json as _json
    from graphify.navigate import navigate
    nodes = [{"id": "n1", "label": "foo()", "file_type": "code",
              "source_file": "a.py", "source_location": "L1"}]
    _write_graph(tmp_path / "graphify-out", nodes, [])
    monkeypatch.chdir(tmp_path)
    out = navigate(["tools/x/y"], session=False, fmt="text")
    assert "@tools/x/y" in out, (
        f"unknown-op error should suggest @-prefixed form for path shapes:\n{out}"
    )


def test_coc_default_limit_lower_than_global(tmp_path, monkeypatch):
    """Lap-12: coc listings on big communities flood context at the global
    default of 25. The coc-specific default is 10 (cursor caches the resolved
    set so re-running with --limit N for a wider window is cheap). Explicit
    --limit overrides for both."""
    from graphify.navigate import navigate, COC_LIST_LIMIT_DEFAULT, LIST_LIMIT
    assert COC_LIST_LIMIT_DEFAULT < LIST_LIMIT, (
        "coc default must be tighter than the global listing default"
    )
    # Build a community of 30 members so the limit is what cuts the listing.
    nodes = [{"id": "f", "label": "focus", "file_type": "code",
              "source_file": "a.py", "source_location": "L1", "community": 7}]
    for i in range(30):
        nodes.append({
            "id": f"m{i}", "label": f"sib{i}",
            "file_type": "code",
            "source_file": "a.py", "source_location": f"L{i+10}",
            "community": 7,
        })
    _write_graph(tmp_path / "graphify-out", nodes, [])
    monkeypatch.chdir(tmp_path)
    # Default coc: should show COC_LIST_LIMIT_DEFAULT items max.
    out = navigate(["@focus", "coc"], session=False, fmt="text")
    visible_count = sum(1 for ln in out.splitlines()
                        if ln.strip().startswith("[")
                        and "sib" in ln)
    assert 0 < visible_count <= COC_LIST_LIMIT_DEFAULT, (
        f"coc listing should cap at {COC_LIST_LIMIT_DEFAULT} by default, "
        f"got {visible_count} visible:\n{out}"
    )
    # User-set --limit overrides the coc default.
    out_wide = navigate(["@focus", "coc"], session=False, fmt="text",
                        limit=20)
    visible_wide = sum(1 for ln in out_wide.splitlines()
                       if ln.strip().startswith("[") and "sib" in ln)
    assert visible_wide > COC_LIST_LIMIT_DEFAULT, (
        f"explicit --limit should override coc default, got "
        f"{visible_wide} visible:\n{out_wide}"
    )


def test_inferred_hint_fires_on_low_inferred_in_count(tmp_path, monkeypatch):
    """Lap-12: lower the threshold from 5 to 1 inferred-hidden — closure-heavy
    designs (interface dispatch) often have few inferred edges per method, but
    the AST-only-is-misleading signal is still load-bearing. A `.foo()` shape
    with in(0) and 1+ inferred hidden should still get the include-inferred
    hint."""
    from graphify.navigate import navigate
    nodes = [
        {"id": "iface", "label": "Engine", "file_type": "code",
         "source_file": "a.ts", "source_location": "L1",
         "node_kind": "interface"},
        {"id": "method", "label": ".step()", "file_type": "code",
         "source_file": "a.ts", "source_location": "L5",
         "node_kind": "iface_method"},
        {"id": "caller", "label": "go()", "file_type": "code",
         "source_file": "a.ts", "source_location": "L20"},
    ]
    links = [
        {"source": "iface", "target": "method", "relation": "method",
         "confidence": "EXTRACTED"},
        # One inferred caller — was below the lap-11 threshold of 5.
        {"source": "caller", "target": "method", "relation": "calls",
         "confidence": "INFERRED", "confidence_score": 0.85},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    out = navigate(["@.step()"], session=False, fmt="text")
    # Hint must fire even with only 1 inferred hidden.
    assert "--include-inferred" in out, (
        f"hint should fire on low-count inferred in:\n{out}"
    )
    # Interface-method specific phrasing.
    assert "interface method" in out, f"iface phrasing missing:\n{out}"


def test_read_op_on_file_node_dumps_flat(tmp_path, monkeypatch):
    """Lap-12: `read` on a file node should dump the file inline, not bail
    when the indent walker hits the shebang/imports. The user's cursor
    already knows the path; no separate Read tool round-trip needed."""
    from graphify.navigate import navigate
    src = tmp_path / "data.py"
    src.write_text("# header\nimport os\nx = 1\ny = 2\nprint(x + y)\n",
                   encoding="utf-8")
    nodes = [
        {"id": "fnode", "label": "data.py", "file_type": "code",
         "source_file": str(src), "source_location": "L1"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, [])
    monkeypatch.chdir(tmp_path)
    out = navigate(["@data.py", "read"], session=False, fmt="text")
    # Body walker on file nodes uses flat=True so every line lands.
    for fragment in ("# header", "import os", "x = 1", "y = 2",
                     "print(x + y)"):
        assert fragment in out, (
            f"file-node read should dump {fragment!r}, got:\n{out}"
        )


def test_cross_lang_inferred_edge_dropped(tmp_path, monkeypatch):
    """Lap-12: an INFERRED edge from a .ts source to a .py target is filtered
    at load time (no flag). The drop surfaces in the inline hidden cue (`+1xl`)
    on the affected pivot. Same-language inferred edge passes through; AST
    cross-language edges are exempt (ground truth)."""
    from graphify.navigate import navigate
    nodes = [
        {"id": "ts_caller", "label": "compile()", "file_type": "code",
         "source_file": "src/compile.ts", "source_location": "L1"},
        {"id": "py_target", "label": "get()", "file_type": "code",
         "source_file": "runtime/state.py", "source_location": "L1"},
        {"id": "ts_target", "label": "build()", "file_type": "code",
         "source_file": "src/build.ts", "source_location": "L1"},
    ]
    links = [
        # Cross-lang inferred: should be filtered.
        {"source": "ts_caller", "target": "py_target", "relation": "calls",
         "confidence": "INFERRED", "confidence_score": 0.85},
        # Same-lang inferred: should pass.
        {"source": "ts_caller", "target": "ts_target", "relation": "calls",
         "confidence": "INFERRED", "confidence_score": 0.85},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    out = navigate(["@compile()", "out"],
                   session=False, fmt="text", extracted_only=False)
    assert "build()" in out, f"same-lang INFERRED should pass:\n{out}"
    assert "get()" not in out, (
        f"cross-lang INFERRED should be hidden:\n{out}"
    )
    assert "cross-lang" in out.lower() or "xl" in out, (
        f"drop count should surface as '+Nxl' or 'cross-lang':\n{out}"
    )


def test_cross_lang_extracted_edge_passes(tmp_path, monkeypatch):
    """AST (EXTRACTED) edges are never cross-lang-filtered, even when they
    legitimately cross languages (rare: CFFI imports, template files)."""
    from graphify.navigate import navigate
    nodes = [
        {"id": "ts_caller", "label": "wrap()", "file_type": "code",
         "source_file": "src/wrap.ts", "source_location": "L1"},
        {"id": "py_target", "label": "core()", "file_type": "code",
         "source_file": "runtime/core.py", "source_location": "L1"},
    ]
    links = [
        {"source": "ts_caller", "target": "py_target", "relation": "calls",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    out = navigate(["@wrap()", "out"], session=False, fmt="text")
    assert "core()" in out, (
        f"EXTRACTED cross-lang edge should NOT be filtered:\n{out}"
    )


def test_peek_subcommand_no_session_write(tmp_path, monkeypatch):
    """`graphify peek <symbol>` resolves and dumps body without touching
    cursor / session state. Verify no .navigate dir gets created."""
    import json as _json
    import subprocess
    nodes = [
        {"id": "f", "label": "foo()", "file_type": "code",
         "source_file": "a.py", "source_location": "L1"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, [])
    # Make a real source file so _read_body_full has content.
    (tmp_path / "a.py").write_text("def foo():\n    return 42\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    res = subprocess.run(
        ["python", "-m", "graphify", "peek", "foo"],
        capture_output=True, text=True, cwd=str(tmp_path), timeout=15,
    )
    assert res.returncode == 0, f"peek failed: {res.stderr}"
    assert "foo" in res.stdout
    # peek should NOT have created a session dir.
    assert not (tmp_path / "graphify-out" / ".navigate").exists(), (
        "peek should not create a .navigate session directory"
    )


def _make_class_with_methods(method_labels: list[str]) -> tuple[list[dict], list[dict]]:
    """Build a class node with N method children, all in the same file."""
    nodes = [
        {"id": "f", "label": "a.py", "file_type": "code",
         "source_file": "a.py", "source_location": "L1"},
        {"id": "cls", "label": "Foo", "file_type": "code",
         "source_file": "a.py", "source_location": "L2",
         "node_kind": "class"},
    ]
    links = [
        {"source": "f", "target": "cls", "relation": "contains",
         "confidence": "EXTRACTED"},
    ]
    for i, lbl in enumerate(method_labels):
        nid = f"m{i}"
        nodes.append({
            "id": nid, "label": lbl,
            "file_type": "code",
            "source_file": "a.py",
            "source_location": f"L{10 + i}",
            "node_kind": "impl_method",
        })
        links.append({
            "source": "cls", "target": nid, "relation": "method",
            "confidence": "EXTRACTED",
        })
    return nodes, links


def test_filter_op_narrows_listing_by_regex(tmp_path, monkeypatch):
    """Lap-13: `filter <regex>` chain op narrows the most recent listing
    by regex against the row label. Works after any listing producer
    (methods, siblings, in/out, contains, …) without needing a per-op
    --filter flag."""
    from graphify.navigate import navigate
    nodes, links = _make_class_with_methods([
        ".compute_metrics()",
        ".closure_deficit()",
        ".peak_frequency()",
        ".cardinal_deficit()",
        ".asymmetry_score()",
    ])
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    out = navigate(["@Foo", "methods", "filter", "_deficit"],
                   session=False, fmt="text")
    # Only the two _deficit methods should survive.
    assert "closure_deficit" in out, f"closure_deficit dropped:\n{out}"
    assert "cardinal_deficit" in out, f"cardinal_deficit dropped:\n{out}"
    assert "compute_metrics" not in out, f"compute_metrics leaked:\n{out}"
    assert "peak_frequency" not in out, f"peak_frequency leaked:\n{out}"
    # Header surfaces the from-count so the agent sees what was excluded.
    assert "(2 of 5)" in out, f"filter header missing match-count:\n{out}"


def test_filter_op_substring_fallback_on_invalid_regex(tmp_path, monkeypatch):
    """A pattern that fails to compile as a regex (`(unclosed`) falls back
    to case-insensitive substring match — saves the agent from escaping
    special chars for a quick narrow."""
    from graphify.navigate import navigate
    nodes, links = _make_class_with_methods([
        ".foo(unclosed",
        ".bar()",
        ".baz()",
    ])
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    out = navigate(["@Foo", "methods", "filter", "(unclosed"],
                   session=False, fmt="text")
    # The literal `(unclosed` is an invalid regex; substring fallback
    # finds the foo method that contains the literal sequence.
    assert "foo(unclosed" in out, f"substring fallback failed:\n{out}"
    # Header surfaces `, substring` so the agent knows the regex didn't
    # compile and fallback fired — they may want to escape and re-run.
    assert ", substring" in out, (
        f"header should flag substring fallback:\n{out}"
    )


def test_filter_op_errors_when_no_prior_listing(tmp_path, monkeypatch):
    """Calling `filter` without a prior listing op is a useful error,
    not a silent empty result."""
    from graphify.navigate import navigate
    nodes = [
        {"id": "f", "label": "foo()", "file_type": "code",
         "source_file": "a.py", "source_location": "L1"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, [])
    monkeypatch.chdir(tmp_path)
    out = navigate(["@foo()", "filter", "anything"],
                   session=False, fmt="text")
    assert "no listing to filter" in out, (
        f"filter without prior listing should error clearly:\n{out}"
    )


def test_filter_renumbers_picks_after_narrow(tmp_path, monkeypatch):
    """After `filter`, `[N]` indexes into the filtered set 1-based.
    `methods filter _deficit 1` should land on the first matching method,
    not the first method of the original listing."""
    from graphify.navigate import navigate
    nodes, links = _make_class_with_methods([
        ".alpha()",       # 1 in unfiltered
        ".beta_deficit()", # would be 2 unfiltered, 1 after filter
        ".gamma()",       # 3 unfiltered, dropped after filter
        ".delta_deficit()", # 4 unfiltered, 2 after filter
    ])
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    # methods → filter _deficit → pick [1] should focus beta_deficit
    out = navigate(["@Foo", "methods", "filter", "_deficit", "[1]"],
                   session=False, fmt="text")
    assert "beta_deficit" in out, (
        f"pick [1] after filter should land on first match:\n{out}"
    )
    # The original alpha (would be [1] without filter) must NOT be the
    # focused node — verify its label only appears as a non-focus mention
    # if at all. The frontier card shows the focused label prominently;
    # check the line starting with "now:" or the focus header.
    focus_lines = [ln for ln in out.splitlines()
                   if ln.startswith("now:") or "focus" in ln.lower()]
    head = "\n".join(focus_lines) if focus_lines else out.splitlines()[0]
    assert "alpha" not in head, (
        f"focus should be beta_deficit, not alpha:\n{out}"
    )
