import networkx as nx
from graphify.resolve import resolve_focus, label_index


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


def test_directory_query_returns_files_in_dir():
    """Lap-27 #6: `@<dir>/` (trailing slash) should land on the files in
    that directory, not fall through to substring fuzzy. Without this,
    `@graphify/` returns the 6 nodes whose label happens to contain the
    literal `graphify/` (file-shaped labels like `graphify/__main__.py`)
    — useless overlap with what the query actually meant.
    """
    G = nx.DiGraph()
    # Two files in `tools/`, plus a noise file outside it and a deeper
    # `tools/sub/` file that should NOT match an immediate-children query.
    G.add_node("f1", label="metric_diagnostic.py", file_type="code",
               source_file="tools/metric_diagnostic.py", source_location="L1",
               node_kind="file")
    G.add_node("f2", label="twist_spectrum_diagnostic.py", file_type="code",
               source_file="tools/twist_spectrum_diagnostic.py",
               source_location="L1", node_kind="file")
    G.add_node("f3", label="ab_metric_diagnostic.py", file_type="code",
               source_file="1d/ab_metric_diagnostic.py", source_location="L1",
               node_kind="file")
    idx = label_index(G)
    chosen, candidates, match_type, _ = resolve_focus(G, idx, "tools/")
    assert chosen is None, "directory query must yield a listing, not auto-pick"
    assert match_type == "exact"
    assert set(candidates) == {"f1", "f2"}, (
        f"@tools/ should list files in tools/ (f1, f2); got {candidates}"
    )


def test_directory_query_no_trailing_slash_still_recognized():
    """Lap-27 #6: bare `@<dir>` with no trailing slash and no `.` in the
    last segment, where the input matches a directory prefix in the
    graph, should also land on the directory. Without this, every
    `@tests` query goes through fuzzy and lands on the nearest typo
    rather than the directory the agent actually meant."""
    G = nx.DiGraph()
    G.add_node("f1", label="t1.py", file_type="code",
               source_file="tests/t1.py", source_location="L1",
               node_kind="file")
    G.add_node("f2", label="t2.py", file_type="code",
               source_file="tests/t2.py", source_location="L1",
               node_kind="file")
    G.add_node("noise", label="other.py", file_type="code",
               source_file="src/other.py", source_location="L1",
               node_kind="file")
    idx = label_index(G)
    chosen, candidates, match_type, _ = resolve_focus(G, idx, "tests")
    # Two files in tests/ → disambig listing.
    assert chosen is None
    assert set(candidates) == {"f1", "f2"}, (
        f"@tests should list files in tests/ when no symbol named tests "
        f"exists; got {candidates}"
    )


def test_directory_query_loses_to_real_symbol_match():
    """Lap-27 #6 guard: when a node label is exactly the dir-shaped
    input (e.g. `tests` IS a class or fn), the symbol match wins.
    Directory inference only fires when no real label match exists.
    Trailing-slash form is unambiguous and still triggers dir lookup."""
    G = nx.DiGraph()
    # A real class named `tests` (contrived but possible).
    G.add_node("cls", label="tests", file_type="code",
               source_file="src/foo.py", source_location="L10",
               node_kind="class")
    # File in tests/ directory.
    G.add_node("f1", label="t1.py", file_type="code",
               source_file="tests/t1.py", source_location="L1",
               node_kind="file")
    idx = label_index(G)
    # Bare `@tests` → exact label match wins.
    chosen, _, match_type, _ = resolve_focus(G, idx, "tests")
    assert chosen == "cls", "exact label match must beat directory inference"
    assert match_type == "exact"
    # `@tests/` (slash form) → unambiguous dir intent, ignores the class.
    # One file in tests/ → auto-pick (matches single-hit path-qualified
    # behavior). Multiple files would yield a listing.
    chosen2, candidates2, match_type2, _ = resolve_focus(G, idx, "tests/")
    assert chosen2 == "f1", "trailing slash must skip class match"
    assert match_type2 == "exact"


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


def test_community_labels_prefer_internal_callables_over_external_stubs(tmp_path):
    """Lap-26 graphify-on-graphify field report: top-community labels
    picked the highest-degree non-file symbol, which routinely lands
    on external stubs (`str`, `pathlib`, `Response`) — built-in or
    cross-corpus references with high degree but uninformative as a
    cluster label. Real graph: `c0=str 509 members`, `c1=pathlib 196`.

    Fix adds a 3rd preference tier: internal callables (`source_file`
    set + `node_kind` in {function,method,class,interface}) are
    preferred over generic symbols, falling through cleanly when a
    community is purely external (e.g., a cluster of stdlib refs).
    """
    import json as _json
    from graphify.navigate import load_graph
    nodes = [
        # External stub: HIGHER degree (4) but no source_file, no node_kind.
        # This is how `str` shows up in real graphs — referenced by every
        # node that uses string types, but it carries no domain meaning.
        {"id": "stub", "label": "str", "file_type": "code",
         "source_file": "", "source_location": "", "community": 0},
        # Internal callable: LOWER degree (3) but a real function.
        # Without the lap-26 fix, the stub wins on degree and the
        # cluster gets labeled `c0=str` instead of `c0=buildEngine()`.
        {"id": "engine", "label": "buildEngine()", "file_type": "code",
         "source_file": "engine.ts", "source_location": "L10",
         "node_kind": "function", "community": 0},
        {"id": "c1", "label": "c1", "file_type": "code",
         "source_file": "engine.ts", "source_location": "L20",
         "community": 0},
        {"id": "c2", "label": "c2", "file_type": "code",
         "source_file": "engine.ts", "source_location": "L21",
         "community": 0},
    ]
    links = [
        # stub gets in-degree 4 (each consumer references it).
        {"source": "engine", "target": "stub", "relation": "uses",
         "confidence": "EXTRACTED"},
        {"source": "c1", "target": "stub", "relation": "uses",
         "confidence": "EXTRACTED"},
        {"source": "c2", "target": "stub", "relation": "uses",
         "confidence": "EXTRACTED"},
        # engine gets in-degree 2 + out-degree 1 = 3. Lower than stub's 4
        # — only the internal-callable preference tier flips the pick.
        {"source": "c1", "target": "engine", "relation": "calls",
         "confidence": "EXTRACTED"},
        {"source": "c2", "target": "engine", "relation": "calls",
         "confidence": "EXTRACTED"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"nodes": nodes, "links": links}), encoding="utf-8")
    G, _comm = load_graph(graph_dir / "graph.json")
    label = G.graph["community_labels"][0]
    assert "str" != label, (
        f"external stub won label despite internal callable available: {label}"
    )
    assert "buildEngine" in label, (
        f"expected internal callable to win the label, got: {label}"
    )
    # community_hubs should also point at the internal callable, not
    # the stub — the hub-id is used by `is_community_hub` annotations
    # in listings, and pointing at a stub there is the same problem
    # (agent navigates to `str`, learns nothing about the cluster).
    hub = G.graph["community_hubs"][0]
    assert hub == "engine", f"expected hub=engine (internal), got hub={hub}"


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
    from graphify.resolve import _is_archived_path
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


def test_session_id_printed_on_every_persist_call(tmp_path, monkeypatch):
    """Lap-22 meta-harness field-report fix (reverses lap-6 friction 9):
    agents running `graphify navigate` across separate CLI invocations
    couldn't chain because the first call (a bare focus, history=0) used
    to suppress the session id, leaving them with nothing to pass to
    `--session` on the second call. Print the id every time persist
    is on so chain-resumption is always available."""
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
    # Default session=True, single focus op → id IS printed (was suppressed pre-lap-22)
    out = navigate(["@alpha"], session=True, fmt="text")
    assert "session:" in out, f"id missing on one-shot, agent has no way to chain:\n{out}"
    # Explicit session also prints
    out2 = navigate(["@alpha"], session="myid", fmt="text")
    assert "session: myid" in out2
    # session=False (machine pipelines) suppresses entirely
    out3 = navigate(["@alpha"], session=False, fmt="text")
    assert "session:" not in out3, f"session=False should suppress:\n{out3}"


def test_disambig_listing_names_session_pick_command(tmp_path, monkeypatch):
    """Lap-22b meta-harness field-report fix: when an `@<label>` query
    matches multiple nodes, the rendered listing shows numbered rows
    `[1]`, `[2]`, ... that ARE pickable via `navigate "[N]" --session
    <id>` — but three lap-22 rollouts cited "tried [N], got 'no listing
    to pick from'." After the cursor-persistence fix in ff214ee the
    pick succeeds; agents just don't realise they have to thread
    --session. Name the pick command explicitly next to the session id
    on disambig output so the affordance is visible."""
    import json as _json
    from graphify.navigate import navigate
    # Two same-label nodes in different files → disambig listing.
    nodes = [
        {"id": "fa", "label": "lib.py", "file_type": "code",
         "source_file": "lib.py", "source_location": "L1",
         "node_kind": "file"},
        {"id": "fb", "label": "tools.py", "file_type": "code",
         "source_file": "tools.py", "source_location": "L1",
         "node_kind": "file"},
        {"id": "ra", "label": "run()", "file_type": "code",
         "source_file": "lib.py", "source_location": "L10-20",
         "node_kind": "function", "community": 0},
        {"id": "rb", "label": "run()", "file_type": "code",
         "source_file": "tools.py", "source_location": "L30-40",
         "node_kind": "function", "community": 0},
    ]
    links = [
        {"source": "fa", "target": "ra", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "fb", "target": "rb", "relation": "contains",
         "confidence": "EXTRACTED"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"nodes": nodes, "links": links}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    out = navigate(["@run"], session=True, fmt="text")
    # Sanity: this is a disambig listing.
    assert "ambiguous" in out, f"expected disambig listing:\n{out}"
    # Session id present (was already covered by ff214ee test).
    assert "session:" in out, f"session id missing:\n{out}"
    # New: pick-row command appears next to the session id with the
    # actual session id substituted in. Without this the agent reads
    # the numbered rows as pickable but doesn't know they need
    # --session, and gets "no listing to pick from" on the next call.
    assert "graphify navigate \"[N]\" --session" in out, (
        f"disambig output should name the pick-row command + --session "
        f"so the agent sees the affordance:\n{out}"
    )


def test_pivot_listing_does_not_emit_pick_hint(tmp_path, monkeypatch):
    """The pick-row hint is scoped to disambig listings (where the
    field reports came from). On a regular pivot listing (in/out/methods
    /contains), the agent typically already passed --session or is
    chaining within one call — emitting the hint there would be noise
    on every chain step. Belt-and-suspenders: assert the hint stays
    quiet on a non-disambig listing."""
    import json as _json
    from graphify.navigate import navigate
    nodes = [
        {"id": "f", "label": "lib.py", "file_type": "code",
         "source_file": "lib.py", "source_location": "L1",
         "node_kind": "file"},
        {"id": "g", "label": "go()", "file_type": "code",
         "source_file": "lib.py", "source_location": "L5",
         "node_kind": "function", "community": 0},
        {"id": "h", "label": "helper()", "file_type": "code",
         "source_file": "lib.py", "source_location": "L20",
         "node_kind": "function", "community": 0},
    ]
    links = [
        {"source": "f", "target": "g", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "f", "target": "h", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "g", "target": "h", "relation": "calls",
         "confidence": "EXTRACTED"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"nodes": nodes, "links": links}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    # Focus → out (regular pivot listing, not disambig).
    out = navigate(["@go", "out"], session=True, fmt="text")
    assert "session:" in out
    assert "to pick a row" not in out, (
        f"non-disambig pivot should not emit the pick hint:\n{out}"
    )


def test_session_id_suppressed_on_resolver_miss(tmp_path, monkeypatch):
    """Lap-27 #8: when `@<symbol>` resolves to no candidates, the call
    produced nothing the agent can chain on — the cursor didn't move.
    Printing `session: <id>` in that case is noise on a hard miss.
    Drop it; agents who got here via an existing chain can re-pass
    --session manually if they want to recover."""
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
    out = navigate(["@zqqqzzz_nonexistent"], session=True, fmt="text")
    assert "no node matches" in out
    assert "session:" not in out, (
        f"session-id should be suppressed on a hard resolver miss:\n{out}"
    )


def test_session_id_suppressed_on_kind_filter_zeroed_listing(tmp_path, monkeypatch):
    """Lap-27 #8: when --node-kind filters every row out of a listing,
    the result has zero pickable items. Session-id is noise — there's
    nothing to chain on, and the agent got the message that the filter
    was too tight from the omission count itself."""
    import json as _json
    from graphify.navigate import navigate
    nodes = [
        {"id": "f", "label": "lib.py", "file_type": "code",
         "source_file": "lib.py", "source_location": "L1",
         "node_kind": "file"},
        {"id": "g", "label": "go()", "file_type": "code",
         "source_file": "lib.py", "source_location": "L5",
         "node_kind": "function", "community": 0},
    ]
    links = [
        {"source": "f", "target": "g", "relation": "contains",
         "confidence": "EXTRACTED"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"nodes": nodes, "links": links}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    # contains listing of one fn, filtered to interfaces only → zero items.
    out = navigate(["@lib.py", "contains", "--node-kind", "interface"],
                   session=True, fmt="text")
    assert "session:" not in out, (
        f"session-id should be suppressed when --node-kind zeros the listing:\n{out}"
    )


def test_session_id_kept_on_disambig_listing_with_pickable_rows(tmp_path, monkeypatch):
    """Lap-27 #8 regression guard: the lap-22 pickable-disambig affordance
    must not regress. Disambig listings have items and ARE chainable via
    `[N] --session <id>`; session-id stays."""
    import json as _json
    from graphify.navigate import navigate
    nodes = [
        {"id": "fa", "label": "lib.py", "file_type": "code",
         "source_file": "lib.py", "source_location": "L1",
         "node_kind": "file"},
        {"id": "fb", "label": "tools.py", "file_type": "code",
         "source_file": "tools.py", "source_location": "L1",
         "node_kind": "file"},
        {"id": "ra", "label": "run()", "file_type": "code",
         "source_file": "lib.py", "source_location": "L10-20",
         "node_kind": "function", "community": 0},
        {"id": "rb", "label": "run()", "file_type": "code",
         "source_file": "tools.py", "source_location": "L30-40",
         "node_kind": "function", "community": 0},
    ]
    links = [
        {"source": "fa", "target": "ra", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "fb", "target": "rb", "relation": "contains",
         "confidence": "EXTRACTED"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"nodes": nodes, "links": links}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    out = navigate(["@run"], session=True, fmt="text")
    assert "ambiguous" in out
    assert "session:" in out, (
        f"disambig keeps session-id (rows are pickable):\n{out}"
    )
    assert "to pick a row" in out


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


def test_path_qualified_partial_filename_resolves_via_symbol_restriction():
    """Lap-20 bug A: `multianti.ts/unit` should resolve to a node in
    `experiments/ref-id-logit-delta-multianti.ts` because that's the only
    file containing a `unit` symbol whose basename contains `multianti.ts`
    as a substring. Old behavior: strict endswith match failed
    (`-multianti.ts` ≠ `/multianti.ts`), substring failed (key has `/`),
    fuzzy on labels was below cutoff → `no node matches`."""
    G = nx.DiGraph()
    # Target file: long dash-separated name, contains a `unit` symbol.
    G.add_node("target", label="unit", file_type="code",
               source_file="experiments/ref-id-logit-delta-multianti.ts",
               source_location="L42")
    # Distractor: dash-stem prefix-of `multi`, no `unit` symbol.
    G.add_node("entity_file", label="multi-entity.ts", file_type="code",
               source_file="src/multi-entity.ts", source_location="L1")
    G.add_node("entity_other", label="something_else", file_type="code",
               source_file="src/multi-entity.ts", source_location="L20")
    idx = label_index(G)
    chosen, candidates, match_type, _ = resolve_focus(G, idx, "multianti.ts/unit")
    assert chosen == "target", (
        f"partial filename + symbol should resolve to target; "
        f"got chosen={chosen!r} candidates={candidates}"
    )
    assert match_type == "exact"


def test_path_qualified_does_not_fuzzy_match_unrelated_file():
    """Lap-20 bug B: `multianti.ts/unit` must NOT silently resolve to
    `multi-entity.ts` just because difflib similarity ranks it close.
    Symbol-restriction skips files that don't contain the requested symbol;
    if the only file containing `unit` is unrelated to the typed prefix,
    return no match rather than a misleading fuzzy hit."""
    G = nx.DiGraph()
    # File `multi-entity.ts` exists but has NO `unit` symbol.
    G.add_node("entity_file", label="multi-entity.ts", file_type="code",
               source_file="src/multi-entity.ts", source_location="L1")
    G.add_node("entity_thing", label="EntityThing", file_type="code",
               source_file="src/multi-entity.ts", source_location="L20")
    # Some unrelated file with a `unit` symbol — shouldn't be picked
    # because its filename doesn't substring/stem-match `multianti.ts`.
    G.add_node("unit_unrelated", label="unit", file_type="code",
               source_file="totally/different/path.ts", source_location="L1")
    idx = label_index(G)
    chosen, candidates, match_type, _ = resolve_focus(G, idx, "multianti.ts/unit")
    # Either no match or candidates listing — but never silently picks
    # `multi-entity.ts` (which doesn't have a `unit` symbol at all).
    assert chosen != "entity_file", (
        f"must not auto-pick semantically unrelated file; got chosen={chosen!r}"
    )
    # If it did match `unit_unrelated`, that's also wrong (the prefix
    # `multianti.ts` doesn't substring/stem-match `path.ts`).
    assert chosen != "unit_unrelated", (
        f"must not pick file whose name doesn't match the typed prefix; "
        f"got chosen={chosen!r}"
    )


def test_path_qualified_substring_beats_dash_stem_prefix():
    """Lap-20 ranking: when both a substring-match file and a dash-stem
    prefix file contain the symbol, prefer the substring match. The user
    typing `multianti.ts/unit` is more likely targeting the file whose
    basename literally contains `multianti.ts` than one that merely
    starts with `multi-`."""
    G = nx.DiGraph()
    # Substring match: 'multianti.ts' is a substring of basename.
    G.add_node("substr_unit", label="unit", file_type="code",
               source_file="experiments/ref-id-logit-delta-multianti.ts",
               source_location="L42")
    # Dash-stem prefix match: basename starts with 'multi-' but doesn't
    # contain 'multianti.ts' as a substring.
    G.add_node("stem_unit", label="unit", file_type="code",
               source_file="src/multi-entity.ts", source_location="L1")
    idx = label_index(G)
    chosen, candidates, match_type, _ = resolve_focus(G, idx, "multianti.ts/unit")
    assert chosen == "substr_unit", (
        f"substring match should beat dash-stem prefix; "
        f"got chosen={chosen!r} candidates={candidates}"
    )
    assert match_type == "exact"


def test_path_qualified_dotted_suffix_matches_object_method():
    """Lap-20b: object-literal method nodes have labels like `config.run()`.
    A user typing `<file>/run` should resolve to that node when the file
    portion narrows it down. The basename `run` should match the after-
    dot suffix of `config.run()`. Without this, every TS experiment file's
    `config.run` falls through to global fuzzy on `/run` queries."""
    G = nx.DiGraph()
    G.add_node("file_a", label="multianti.ts", file_type="code",
               source_file="experiments/ref-id-logit-delta-multianti.ts",
               source_location="L1", node_kind="file")
    G.add_node("config_a", label="config", file_type="code",
               source_file="experiments/ref-id-logit-delta-multianti.ts",
               source_location="L195")
    G.add_node("run_a", label="config.run()", file_type="code",
               source_file="experiments/ref-id-logit-delta-multianti.ts",
               source_location="L205-503")
    idx = label_index(G)
    chosen, _, match_type, _ = resolve_focus(G, idx, "multianti.ts/run")
    assert chosen == "run_a", (
        f"`<file>/run` should resolve to `config.run()` via dotted-suffix "
        f"match; got chosen={chosen!r}"
    )
    assert match_type == "exact"


def test_dotted_class_method_walks_all_matching_classes():
    """Lap-20c EGF head-to-head field report: when a class name is shared
    across files (e.g. a production version + an `investigations/` draft),
    the dotted resolver must walk BOTH classes' methods and emit a
    disambig listing rather than bailing to fuzzy and silently landing
    on the wrong one. Today's failure mode: `peek
    SpectralGraphGeometry.compute_metrics` silently picked the draft
    because fuzzy ranked the long similar labels close enough that
    investigations/ won the tie-break."""
    G = nx.DiGraph()
    # Production class + method
    G.add_node("prod_cls", label="SpectralGraphGeometry", file_type="code",
               source_file="src/kg/spectral.ts", source_location="L10")
    G.add_node("prod_method", label=".compute_metrics()", file_type="code",
               source_file="src/kg/spectral.ts", source_location="L42")
    G.add_edge("prod_cls", "prod_method", relation="method")
    # Draft in investigations/
    G.add_node("draft_cls", label="SpectralGraphGeometry", file_type="code",
               source_file="investigations/spectral-draft.ts", source_location="L1")
    G.add_node("draft_method", label=".compute_metrics()", file_type="code",
               source_file="investigations/spectral-draft.ts", source_location="L20")
    G.add_edge("draft_cls", "draft_method", relation="method")
    idx = label_index(G)
    chosen, candidates, match_type, _ = resolve_focus(
        G, idx, "SpectralGraphGeometry.compute_metrics"
    )
    assert chosen is None, (
        f"shared class name should produce disambig, not silent pick; "
        f"got chosen={chosen!r}"
    )
    assert match_type == "exact"
    assert set(candidates) == {"prod_method", "draft_method"}, (
        f"both class' methods should appear; got {candidates}"
    )


def test_path_qualified_no_symbol_lists_files_actual_symbols():
    """Lap-20b TS-Claude field report: `<file>/<sym>` where the symbol
    portion misses must list the prefix-matching file's actual symbols
    rather than fuzzy-jumping to a similar-named file in another
    directory. Match_type `no_match_in_file` flags the case so navigate
    can render a clear "<basename> not in <file>" pivot label."""
    G = nx.DiGraph()
    G.add_node("file_a", label="multianti.ts", file_type="code",
               source_file="experiments/ref-id-logit-delta-multianti.ts",
               source_location="L1", node_kind="file")
    G.add_node("foo_a", label="foo()", file_type="code",
               source_file="experiments/ref-id-logit-delta-multianti.ts",
               source_location="L10")
    G.add_node("bar_a", label="bar()", file_type="code",
               source_file="experiments/ref-id-logit-delta-multianti.ts",
               source_location="L20")
    # An unrelated file with similar dash-stem name — must NOT win.
    G.add_node("file_b", label="multi-entity.ts", file_type="code",
               source_file="src/multi-entity.ts",
               source_location="L1", node_kind="file")
    idx = label_index(G)
    chosen, candidates, match_type, _ = resolve_focus(
        G, idx, "multianti.ts/nonexistent"
    )
    assert chosen is None, (
        f"missing symbol should not silently resolve; got chosen={chosen!r}"
    )
    assert match_type == "no_match_in_file", (
        f"expected no_match_in_file, got match_type={match_type!r}"
    )
    assert set(candidates) == {"foo_a", "bar_a"}, (
        f"candidates should be the file's symbols, got {candidates}"
    )


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


def test_methods_on_function_returns_redirect(tmp_path):
    """Lap-16 TS field-report friction 6: `methods` on a function returned
    a silent empty listing. The agent had no signal that the pivot didn't
    apply to the kind. The redirect should surface `out`/`in`/`read`."""
    import json as _json
    from graphify.navigate import navigate
    nodes = [
        # Python-style function: no node_kind, label foo()
        {"id": "py_helper", "label": "helper()", "file_type": "code",
         "source_file": "lib.py", "source_location": "L10"},
        # TS-style impl_method: explicit node_kind
        {"id": "ts_impl", "label": ".forward()", "file_type": "code",
         "source_file": "engine.ts", "source_location": "L271",
         "node_kind": "impl_method"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False,
         "graph": {}, "nodes": nodes, "links": []}), encoding="utf-8")
    for q in ("@helper()", "@.forward()"):
        out = navigate([q, "methods"],
                       graph_path=str(graph_dir / "graph.json"),
                       session=False, fmt="text")
        assert "n/a on function nodes" in out, f"{q}: {out}"
        assert "out" in out and "in" in out, f"{q}: redirect missing pivots: {out}"


def test_contains_on_function_returns_redirect(tmp_path):
    """Same redirect for `contains` — function nodes don't contain
    anything; agent should be steered to `out`/`in`/`read`."""
    import json as _json
    from graphify.navigate import navigate
    nodes = [
        {"id": "ts_impl", "label": ".dispatch()", "file_type": "code",
         "source_file": "engine.ts", "source_location": "L80",
         "node_kind": "impl_method"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False,
         "graph": {}, "nodes": nodes, "links": []}), encoding="utf-8")
    out = navigate(["@.dispatch()", "contains"],
                   graph_path=str(graph_dir / "graph.json"),
                   session=False, fmt="text")
    assert "n/a on function nodes" in out, out


def test_resolve_focus_demotes_orphan_in_fuzzy_match(tmp_path):
    """Lap-16 Python field-report: `@MöbiusS3` landed on `TestMobiusS3`
    (orphan, length_pad=4) ahead of `MobiusS3Geometry` (deg=high,
    length_pad=8). Connected near-matches should beat orphan exact-shape
    matches."""
    import networkx as nx
    from graphify.resolve import resolve_focus, label_index
    G = nx.DiGraph()
    # Source class — connected via methods + inh
    G.add_node("real_geo", label="MobiusS3Geometry", file_type="code",
               source_file="src/geom.py", source_location="L1",
               norm_label="mobiuss3geometry")
    G.add_node("real_method", label="forward()", file_type="code",
               source_file="src/geom.py", source_location="L10",
               norm_label="forward()")
    G.add_node("real_base", label="Base", file_type="code",
               source_file="src/base.py", source_location="L1",
               norm_label="base")
    G.add_edge("real_geo", "real_method", relation="method")
    G.add_edge("real_geo", "real_base", relation="inherits")
    # Test class — orphan, label looks closer to query
    G.add_node("test_cls", label="TestMobiusS3", file_type="code",
               source_file="tests/test_geom.py", source_location="L1",
               norm_label="testmobiuss3")
    idx = label_index(G)
    chosen, candidates, mt, _ = resolve_focus(G, idx, "@MobiusS3")
    # Substring lookup returns multiple candidates; ranker decides ordering.
    if chosen:
        assert chosen == "real_geo", (
            f"expected MobiusS3Geometry (connected) over TestMobiusS3 "
            f"(orphan), got {chosen}")
    else:
        assert candidates[0] == "real_geo", (
            f"expected real_geo first, got {candidates}")


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


def test_coc_code_only_filters_rationale(tmp_path):
    """Lap-16 Python field-report: a 514-member coc was mostly rationale
    nodes. `--code-only` strips file_type=rationale members so the
    listing is the actual code-symbol neighbourhood. Off by default."""
    import json as _json
    from graphify.navigate import navigate
    nodes = [
        {"id": "focus", "label": "Geometry", "file_type": "code",
         "community": 1,
         "source_file": "geom.py", "source_location": "L1"},
        # Rationale fragments — should be filtered when --code-only is on.
        {"id": "rat1", "label": "geometry rationale 1", "file_type": "rationale",
         "community": 1,
         "source_file": "geom.py", "source_location": "L40"},
        {"id": "rat2", "label": "geometry rationale 2", "file_type": "rationale",
         "community": 1,
         "source_file": "geom.py", "source_location": "L80"},
        # Code symbol — should always remain.
        {"id": "s1", "label": "compute()", "file_type": "code",
         "community": 1,
         "source_file": "geom.py", "source_location": "L20"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False,
         "graph": {}, "nodes": nodes, "links": []}), encoding="utf-8")
    # Default (no flag): rationale visible.
    out_default = navigate(["@Geometry", "coc"],
                           graph_path=str(graph_dir / "graph.json"),
                           session=False, fmt="text")
    assert "geometry rationale 1" in out_default
    # --code-only: rationale filtered, drop count surfaced.
    out_code = navigate(["@Geometry", "coc"],
                        graph_path=str(graph_dir / "graph.json"),
                        session=False, fmt="text", code_only=True)
    assert "geometry rationale 1" not in out_code, (
        f"rationale leaked into --code-only listing:\n{out_code}"
    )
    assert "compute()" in out_code
    assert "rationale hidden" in out_code, (
        f"drop count must surface so the agent knows it filtered:\n{out_code}"
    )


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


def test_top_level_help_is_short_by_default(tmp_path):
    """Lap-27 #5: `graphify --help` defaults to a short summary —
    one-line-per-verb, no flag dump. Cold-start agents reach for verb
    names, not flags; the long form (~3K tokens) was making every
    `--help` call expensive. Short form must (a) include all top-level
    verb names, (b) NOT include verb-specific flags like --depth or
    --include-inferred, (c) point to --help-long for the long form."""
    import subprocess
    res = subprocess.run(
        ["graphify", "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert res.returncode == 0
    out = res.stdout
    # All verb names present.
    for verb in ("navigate", "peek", "shape", "doc", "blast", "locate",
                 "summarize", "search", "path", "explain", "changed"):
        assert verb in out, f"verb `{verb}` missing from short help:\n{out}"
    # Flag dump suppressed.
    for flag in ("--depth", "--include-inferred", "--bodies", "--node-kind",
                 "--explain-cost"):
        assert flag not in out, (
            f"short help should not include `{flag}` (use --help-long):\n{out}"
        )
    # Discoverability of --help-long.
    assert "--help-long" in out, (
        f"short help must point to --help-long:\n{out}"
    )


def test_top_level_help_long_includes_flags(tmp_path):
    """Lap-27 #5: `graphify --help-long` keeps the existing full reference —
    flags, decision rule, examples. The short form is the new default;
    the long form is the escape hatch."""
    import subprocess
    res = subprocess.run(
        ["graphify", "--help-long"],
        capture_output=True, text=True, timeout=10,
    )
    assert res.returncode == 0
    out = res.stdout
    # Verb-specific flags appear.
    for flag in ("--depth", "--include-inferred", "--bodies", "--node-kind"):
        assert flag in out, f"long help should include `{flag}`:\n{out[:500]}"
    # Decision rule is part of the long form (lap-21 sub-agent feedback).
    assert "When to use graphify vs Read" in out


def test_subcmd_help_is_short_by_default(tmp_path):
    """Lap-27 #5: `graphify peek --help` should print the signature line
    only, not the full flag block. Agents already know they want peek;
    they need the shape, not every option."""
    import subprocess
    res = subprocess.run(
        ["graphify", "peek", "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert res.returncode == 0
    out = res.stdout
    assert "peek <symbol>" in out
    # Flags should NOT appear in the short form.
    assert "--lines" not in out, (
        f"short subcmd help should omit flags:\n{out}"
    )
    assert "--no-docstring" not in out
    # Pointer to long form.
    assert "peek --help-long" in out


def test_subcmd_help_long_keeps_flags(tmp_path):
    """Lap-27 #5: `graphify peek --help-long` preserves the existing
    full flag block — discoverable for agents who actually need to
    pick a flag."""
    import subprocess
    res = subprocess.run(
        ["graphify", "peek", "--help-long"],
        capture_output=True, text=True, timeout=10,
    )
    assert res.returncode == 0
    out = res.stdout
    assert "peek <symbol>" in out
    assert "--lines" in out
    assert "--no-docstring" in out


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


def test_dependents_explicit_depth_overrides_default(tmp_path, monkeypatch):
    """Lap-27 #7: `dependents --depth=2` was silently clamped to 3 by
    `eff_depth = max(depth, 3)` — the agent passed --depth=2 and got
    depth≤3 in the output banner, walking deeper than asked. Fix: the
    explicit --depth N value is honored; the verb's documented default
    (3) only applies when no --depth was passed."""
    import json as _json
    from graphify.navigate import navigate
    # 4-hop chain so depth=2 vs depth=3 produces different listings.
    nodes = [
        {"id": "lvl0", "label": "lvl0()", "file_type": "code",
         "source_file": "a.py", "source_location": "L1"},
        {"id": "lvl1", "label": "lvl1()", "file_type": "code",
         "source_file": "a.py", "source_location": "L10"},
        {"id": "lvl2", "label": "lvl2()", "file_type": "code",
         "source_file": "a.py", "source_location": "L20"},
        {"id": "lvl3", "label": "lvl3()", "file_type": "code",
         "source_file": "a.py", "source_location": "L30"},
    ]
    links = [
        {"source": "lvl1", "target": "lvl0", "relation": "calls",
         "confidence": "EXTRACTED"},
        {"source": "lvl2", "target": "lvl1", "relation": "calls",
         "confidence": "EXTRACTED"},
        {"source": "lvl3", "target": "lvl2", "relation": "calls",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    # depth=2 → see lvl1, lvl2; not lvl3.
    out = navigate(["@lvl0", "dependents"], session=False, fmt="text",
                   depth=2)
    assert "depth≤2" in out, (
        f"explicit --depth=2 must surface in the banner, not be clamped to 3:\n{out}"
    )
    assert "lvl1()" in out and "lvl2()" in out, (
        f"two-hop walk should reach lvl1 and lvl2:\n{out}"
    )
    assert "lvl3()" not in out, (
        f"--depth=2 should NOT walk to lvl3 (3 hops away):\n{out}"
    )


def test_dependents_no_depth_uses_verb_default(tmp_path, monkeypatch):
    """Lap-27 #7 regression guard: when no --depth is passed, the
    `dependents`/`dependencies` sugar still defaults to 3 (its documented
    behavior — sugar for transitive callers). Sentinel-based fix must
    not regress this."""
    import json as _json
    from graphify.navigate import navigate
    nodes = [
        {"id": "lvl0", "label": "lvl0()", "file_type": "code",
         "source_file": "a.py", "source_location": "L1"},
        {"id": "lvl1", "label": "lvl1()", "file_type": "code",
         "source_file": "a.py", "source_location": "L10"},
        {"id": "lvl2", "label": "lvl2()", "file_type": "code",
         "source_file": "a.py", "source_location": "L20"},
    ]
    links = [
        {"source": "lvl1", "target": "lvl0", "relation": "calls",
         "confidence": "EXTRACTED"},
        {"source": "lvl2", "target": "lvl1", "relation": "calls",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    out = navigate(["@lvl0", "dependents"], session=False, fmt="text")
    assert "depth≤3" in out, f"default depth should be 3:\n{out}"
    assert "lvl1()" in out and "lvl2()" in out


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


def test_read_walker_handles_long_ts_fn_with_multiline_sig(tmp_path):
    """Lap-21 #1: TS-Claude reported `peek @compile` returning args-only
    (6 lines) for a 41-line function. Root cause: _signature_end tracked
    `{`/`}` along with `(`/`)`, so the body-opening `{` kept depth>0 deep
    into the body. With max_search=40, depth never balanced before the
    bound, sig_end fell back to start+1, and the body walker fixated
    body_indent on the param-column. Fix: paren-only depth.

    Test shape: 50-line TS function with multi-line param list + braces
    in the body (object literals, blocks). Without the fix, sig_end
    falls back, body_indent locks to 2 (the param column), and the first
    real body line at indent 2 is captured but the {} blocks at deeper
    indents aren't — and we cap at the FIRST dedent past 2."""
    src = (tmp_path / "compile.ts")
    body_lines = [
        "function compile(\n",
        "  spec: Spec,\n",
        "  opts: Opts,\n",
        "  flags: Flags,\n",
        "): Engine {\n",
    ]
    # 45 body lines with varying brace nesting, simulating real TS code.
    for i in range(45):
        if i % 5 == 0:
            body_lines.append(f"  const obj{i} = {{ key: {i} }};\n")
        elif i % 5 == 1:
            body_lines.append(f"  if (cond{i}) {{\n")
        elif i % 5 == 2:
            body_lines.append(f"    doStuff{i}();\n")
        elif i % 5 == 3:
            body_lines.append("  }\n")
        else:
            body_lines.append(f"  const x{i} = {i};\n")
    body_lines.append("  return engine;\n")
    body_lines.append("}\n")
    body_lines.append("\n")
    body_lines.append("function other() { return 0; }\n")
    src.write_text("".join(body_lines), encoding="utf-8")

    from graphify.navigate import _read_body_full, _read_body_preview
    body, ln, _ = _read_body_full(str(src), "L1", max_lines=200)
    text = "\n".join(body)
    assert "const obj0" in text, (
        f"long TS fn body never reached body lines (sig_end fallback):\n"
        f"{text[:500]}"
    )
    assert "return engine" in text, (
        f"long TS fn body truncated before return:\n{text[-500:]}"
    )
    assert "function other" not in text, (
        f"walker leaked past closing brace:\n{text[-500:]}"
    )

    # `peek @compile` => preview of n=3 should show actual body, not args.
    preview = _read_body_preview(str(src), "L1", n=8)
    preview_text = "\n".join(preview)
    assert "const obj0" in preview_text or "if (cond1)" in preview_text, (
        f"--bodies 3 / peek preview returned args-only for long TS fn:\n"
        f"{preview_text}"
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


def test_frontier_header_disambiguates_focus_from_community(tmp_path, monkeypatch):
    """Lap-13 field-report fix: `c3=DecodeEngine` reads like 'Atlas IS
    DecodeEngine'. Header now uses `c3 hub:DecodeEngine` so the cluster
    label is unambiguously the hub name, not the focused node."""
    from graphify.navigate import navigate
    # All distinct labels, no substring overlap. Atlas is the focus,
    # DecodeEngine is the auto-hub (highest in-degree in c3).
    nodes = [
        {"id": "n_atlas", "label": "Atlas", "file_type": "code",
         "source_file": "a.py", "source_location": "L1", "community": 3},
        {"id": "n_engine", "label": "DecodeEngine", "file_type": "code",
         "source_file": "b.py", "source_location": "L2", "community": 3},
        {"id": "n_uno", "label": "alpha", "file_type": "code",
         "source_file": "c.py", "source_location": "L3", "community": 3},
        {"id": "n_duo", "label": "beta", "file_type": "code",
         "source_file": "d.py", "source_location": "L4", "community": 3},
    ]
    links = [
        # DecodeEngine wins as hub via highest combined in/out degree.
        {"source": "n_uno", "target": "n_engine", "relation": "calls",
         "confidence": "EXTRACTED"},
        {"source": "n_duo", "target": "n_engine", "relation": "calls",
         "confidence": "EXTRACTED"},
        {"source": "n_engine", "target": "n_uno", "relation": "calls",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    out = navigate(["@Atlas"], session=False, fmt="text")
    # Header line carries the focus + community info.
    header = next((ln for ln in out.splitlines() if ln.startswith("@ ")), "")
    assert "hub:" in header, f"header should mark cluster label as `hub:`:\n{out}"
    assert "=" not in header.split("·")[1], (
        f"the cluster segment of the header should not use `=`:\n{out}"
    )


def test_summarize_emits_overview(tmp_path):
    """Lap-21 (Gemini #3): `graphify summarize` synthesizes a one-call
    architectural overview from data already on the graph: top
    communities (by hub), cross-file entry points, edge mix, language
    counts. No re-extraction; reuses the loaded graph."""
    import json as _json, subprocess
    nodes = [
        {"id": "fA", "label": "engine.py", "file_type": "code",
         "source_file": "engine.py", "source_location": "L1",
         "node_kind": "file", "community": 0},
        {"id": "fn", "label": "compile()", "file_type": "code",
         "source_file": "engine.py", "source_location": "L5",
         "node_kind": "function", "community": 0},
        {"id": "fU", "label": "user.py", "file_type": "code",
         "source_file": "user.py", "source_location": "L1",
         "node_kind": "file", "community": 1},
        {"id": "user", "label": "user_fn()", "file_type": "code",
         "source_file": "user.py", "source_location": "L1",
         "node_kind": "function", "community": 1},
    ]
    links = [
        {"source": "fA", "target": "fn", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "fU", "target": "user", "relation": "contains",
         "confidence": "EXTRACTED"},
        # Cross-file call → entry-point ranking.
        {"source": "user", "target": "fn", "relation": "calls",
         "confidence": "EXTRACTED"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False,
         "graph": {}, "nodes": nodes, "links": links}), encoding="utf-8")
    res = subprocess.run(
        ["graphify", "summarize",
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert res.returncode == 0, f"summarize failed:\n{res.stderr}"
    out = res.stdout
    # Top-line stats.
    assert "4 nodes" in out, f"missing node count:\n{out}"
    assert "2 files" in out, f"missing file count:\n{out}"
    # Entry points show the cross-file callee.
    assert "compile()" in out and "×1" in out, (
        f"entry-points should surface compile():\n{out}"
    )
    # Lap-24 redesign: entry-points line carries file:line so the agent
    # doesn't have to follow up with `locate`/`navigate` to find where
    # the function lives.
    assert "engine.py:5" in out, (
        f"entry-points should include file:line of compile():\n{out}"
    )
    # Edge mix surfaces relations.
    assert "calls" in out, f"edge mix missing:\n{out}"
    # Language count.
    assert ".py" in out, f"language mix missing:\n{out}"
    # Lap-24 redesign: "Suggested next" footer points the agent at the
    # top entry-point's file with a copy-pasteable shape command.
    assert "Suggested next:" in out, f"missing suggested-next footer:\n{out}"
    assert "graphify shape engine.py" in out, (
        f"suggested-next should reference the top entry point's file:\n{out}"
    )
    assert "compile()" in out, (
        f"suggested-next should name the top entry point:\n{out}"
    )


def test_summarize_skips_phantom_entry_for_suggested_next(tmp_path):
    """Lap-24 follow-up: a "phantom" entry is one where many same-named
    function nodes exist across the corpus (variants >= 10), with one
    of them absorbing all the cross-file calls via resolution-layer
    merging. Empirical case from zero-tvm: 145 `report()` functions in
    145 different experiment files; one node was funneled all 142
    cross-file callers and located in a config-only source_file.

    The "Suggested next" footer must skip phantom entries and land on
    a clean unique-named entry. Inline annotation
    `(N variants — likely phantom)` flags the bad entry visibly."""
    import json as _json, subprocess
    nodes = [
        {"id": "fA", "label": "phantom_file.py", "file_type": "code",
         "source_file": "phantom_file.py", "source_location": "L1",
         "node_kind": "file", "community": 0},
        {"id": "fB", "label": "real_engine.py", "file_type": "code",
         "source_file": "real_engine.py", "source_location": "L1",
         "node_kind": "file", "community": 1},
        {"id": "compile", "label": "compile()", "file_type": "code",
         "source_file": "real_engine.py", "source_location": "L5",
         "node_kind": "function", "community": 1},
        {"id": "fU", "label": "user.py", "file_type": "code",
         "source_file": "user.py", "source_location": "L1",
         "node_kind": "file", "community": 2},
        {"id": "user", "label": "user_fn()", "file_type": "code",
         "source_file": "user.py", "source_location": "L1",
         "node_kind": "function", "community": 2},
    ]
    # 12 same-named report() nodes — variant count 12 >= 10 threshold.
    # The first one absorbs all 5 cross-file calls (the phantom).
    for i in range(12):
        nodes.append({
            "id": f"report_{i}", "label": "report()",
            "file_type": "code",
            "source_file": "phantom_file.py" if i == 0 else f"variant_{i}.py",
            "source_location": "L1",
            "node_kind": "function",
            "community": 0})
    # 5 caller files calling report_0 (the phantom).
    for i in range(5):
        nodes.append({
            "id": f"caller_p{i}",
            "label": f"caller_p{i}.py", "file_type": "code",
            "source_file": f"caller_p{i}.py", "source_location": "L1",
            "node_kind": "file", "community": 3})
        nodes.append({
            "id": f"cp_fn_{i}",
            "label": f"cp_fn_{i}()", "file_type": "code",
            "source_file": f"caller_p{i}.py", "source_location": "L1",
            "node_kind": "function", "community": 3})
    links = [
        {"source": "fA", "target": "report_0", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "fB", "target": "compile", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "fU", "target": "user", "relation": "contains",
         "confidence": "EXTRACTED"},
    ]
    for i in range(5):
        links.append({"source": f"caller_p{i}", "target": f"cp_fn_{i}",
                      "relation": "contains", "confidence": "EXTRACTED"})
        # All 5 cross-file callers → phantom (report_0).
        links.append({"source": f"cp_fn_{i}", "target": "report_0",
                      "relation": "calls", "confidence": "EXTRACTED"})
    # 1 cross-file call → compile().
    links.append({"source": "user", "target": "compile",
                  "relation": "calls", "confidence": "EXTRACTED"})
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False,
         "graph": {}, "nodes": nodes, "links": links}), encoding="utf-8")
    res = subprocess.run(
        ["graphify", "summarize",
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert res.returncode == 0, f"summarize failed:\n{res.stderr}"
    out = res.stdout
    # report() shows up as a phantom entry — annotated, not silenced.
    assert "report()" in out, f"phantom entry should still list:\n{out}"
    assert "12 variants" in out and "phantom" in out, (
        f"phantom should be flagged with variant count:\n{out}"
    )
    # The "Suggested next" footer skips the phantom and lands on the
    # legitimate compile() entry.
    assert "Suggested next:" in out, f"footer missing:\n{out}"
    suggested_block = out.split("Suggested next:")[1]
    assert "real_engine.py" in suggested_block, (
        f"footer should skip phantom and pick compile() instead:\n{out}"
    )
    assert "phantom_file.py" not in suggested_block, (
        f"footer should NOT point at the phantom file:\n{out}"
    )


def test_summarize_falls_back_to_class_hub_when_no_clean_entry(tmp_path):
    """Lap-24 follow-up: when EVERY entry point is phantom or
    ambiguous, fall back to suggesting `summarize @<top-class-hub>`.
    Ensures the agent always gets a concrete next move when the
    repo has any class-shaped community."""
    import json as _json, subprocess
    # The only entry point with cross-file callers is `process()`, but
    # 12 same-named variants exist across the corpus — phantom flag
    # fires and the suggested-next loop skips it. No other entries
    # qualify, so the class-hub fallback should kick in and suggest
    # `summarize @Widget`.
    nodes = [
        {"id": "fB", "label": "widget.py", "file_type": "code",
         "source_file": "widget.py", "source_location": "L1",
         "node_kind": "file", "community": 1},
        {"id": "klass", "label": "Widget", "file_type": "code",
         "source_file": "widget.py", "source_location": "L5",
         "node_kind": "class", "community": 1},
        # Bulk up Widget's community so it ranks high.
        *[{"id": f"w{i}", "label": f"helper_{i}", "file_type": "code",
           "source_file": "widget.py", "source_location": f"L{20+i}",
           "node_kind": "function", "community": 1}
          for i in range(10)],
        {"id": "fU", "label": "user.py", "file_type": "code",
         "source_file": "user.py", "source_location": "L1",
         "node_kind": "file", "community": 2},
        {"id": "user", "label": "user_fn()", "file_type": "code",
         "source_file": "user.py", "source_location": "L1",
         "node_kind": "function", "community": 2},
    ]
    # 12 same-named process() nodes — all phantom-flagged.
    for i in range(12):
        nodes.append({
            "id": f"process_{i}", "label": "process()",
            "file_type": "code",
            "source_file": f"data_{i}.py", "source_location": "L1",
            "node_kind": "function", "community": 0})
    links = [
        {"source": "fB", "target": "klass", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "fU", "target": "user", "relation": "contains",
         "confidence": "EXTRACTED"},
        # user calls process_0 → ext_in for that node.
        {"source": "user", "target": "process_0", "relation": "calls",
         "confidence": "EXTRACTED"},
    ]
    for i in range(10):
        links.append({"source": "fB", "target": f"w{i}",
                      "relation": "contains", "confidence": "EXTRACTED"})
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False,
         "graph": {"community_labels": {"1": "Widget", "0": "process()",
                                         "2": "user_fn()"}},
         "nodes": nodes, "links": links}), encoding="utf-8")
    res = subprocess.run(
        ["graphify", "summarize",
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert res.returncode == 0, f"summarize failed:\n{res.stderr}"
    out = res.stdout
    assert "Suggested next:" in out, f"footer missing:\n{out}"
    # Falls back to summarize @Widget rather than shape on the
    # phantom data file.
    assert 'summarize "@Widget"' in out, (
        f"footer should fall back to top class hub when entries are phantom:\n{out}"
    )
    suggested_block = out.split("Suggested next:")[1]
    assert "data_" not in suggested_block, (
        f"footer should not point at the phantom data file:\n{out}"
    )


def test_summarize_no_entry_points_skips_suggested_next(tmp_path):
    """Lap-24 redesign: when the graph has zero cross-file callers the
    "Suggested next" footer should be silent. Otherwise we'd point the
    agent at a phantom entry point and waste their first call."""
    import json as _json, subprocess
    # Single-file graph: file + one self-contained function, no
    # cross-file edges.
    nodes = [
        {"id": "fA", "label": "lib.py", "file_type": "code",
         "source_file": "lib.py", "source_location": "L1",
         "node_kind": "file", "community": 0},
        {"id": "fn", "label": "helper()", "file_type": "code",
         "source_file": "lib.py", "source_location": "L5",
         "node_kind": "function", "community": 0},
    ]
    links = [
        {"source": "fA", "target": "fn", "relation": "contains",
         "confidence": "EXTRACTED"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False,
         "graph": {}, "nodes": nodes, "links": links}), encoding="utf-8")
    res = subprocess.run(
        ["graphify", "summarize",
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert res.returncode == 0, f"summarize failed:\n{res.stderr}"
    out = res.stdout
    assert "Entry points" not in out, (
        f"no entry points → entry-points block should be silent:\n{out}"
    )
    assert "Suggested next" not in out, (
        f"no entry points → no suggested-next footer:\n{out}"
    )


def test_files_glob_lists_matching_basenames(tmp_path, monkeypatch):
    """Lap-27 dispatch-corpus follow-up: `graphify files <glob>` lists
    source-file nodes by basename pattern. Closes the
    `find -name "*test*.py"` pattern (5+ separate calls observed in a
    real EGF Explore-agent transcript). Bare basename patterns (no
    `/`) match basename only; patterns with `/` match the full path.
    """
    import subprocess
    nodes = [
        {"id": "f1", "label": "test_alpha.py", "file_type": "code",
         "source_file": "tests/test_alpha.py", "source_location": "L1",
         "node_kind": "file"},
        {"id": "f2", "label": "test_beta.py", "file_type": "code",
         "source_file": "tests/test_beta.py", "source_location": "L1",
         "node_kind": "file"},
        {"id": "f3", "label": "main.py", "file_type": "code",
         "source_file": "src/main.py", "source_location": "L1",
         "node_kind": "file"},
        {"id": "f4", "label": "test_old.py", "file_type": "code",
         "source_file": "archive/test_old.py", "source_location": "L1",
         "node_kind": "file"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, [])
    monkeypatch.chdir(tmp_path)
    res = subprocess.run(
        ["python", "-m", "graphify", "files", "test_*.py"],
        capture_output=True, text=True, cwd=str(tmp_path), timeout=15,
    )
    assert res.returncode == 0, f"files failed: {res.stderr}"
    out = res.stdout
    assert "tests/test_alpha.py" in out
    assert "tests/test_beta.py" in out
    assert "archive/test_old.py" in out
    assert "src/main.py" not in out, (
        f"main.py shouldn't match `test_*.py`:\n{out}"
    )


def test_files_glob_path_pattern_restricts_directory(tmp_path, monkeypatch):
    """Lap-27: a glob containing `/` matches against the full source_file
    path, not just the basename. `tools/*.py` should land on files
    inside `tools/` only — not on a same-named file in `archive/`."""
    import subprocess
    nodes = [
        {"id": "f1", "label": "diagnostic.py", "file_type": "code",
         "source_file": "tools/diagnostic.py", "source_location": "L1",
         "node_kind": "file"},
        {"id": "f2", "label": "diagnostic.py", "file_type": "code",
         "source_file": "archive/diagnostic.py", "source_location": "L1",
         "node_kind": "file"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, [])
    monkeypatch.chdir(tmp_path)
    res = subprocess.run(
        ["python", "-m", "graphify", "files", "tools/*.py"],
        capture_output=True, text=True, cwd=str(tmp_path), timeout=15,
    )
    assert res.returncode == 0, f"files failed: {res.stderr}"
    out = res.stdout
    assert "tools/diagnostic.py" in out
    assert "archive/diagnostic.py" not in out, (
        f"path glob should not pull in archive/diagnostic.py:\n{out}"
    )


def test_files_glob_no_match_exits_one(tmp_path, monkeypatch):
    """Lap-27: like `find` returning empty, an empty match should exit
    non-zero so callers can distinguish "no files" from "ran successfully"."""
    import subprocess
    nodes = [
        {"id": "f1", "label": "main.py", "file_type": "code",
         "source_file": "src/main.py", "source_location": "L1",
         "node_kind": "file"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, [])
    monkeypatch.chdir(tmp_path)
    res = subprocess.run(
        ["python", "-m", "graphify", "files", "*.rs"],
        capture_output=True, text=True, cwd=str(tmp_path), timeout=15,
    )
    assert res.returncode == 1, f"empty match should exit 1: rc={res.returncode}"


def test_locate_resolves_multiple_symbols_in_one_call(tmp_path, monkeypatch):
    """Lap-21 (R3 sub-agent feedback): `graphify locate <s1> <s2> ...`
    returns file:line for many symbols in one call so an agent doesn't
    fan out to N peeks just to find where things live. R3-A's graphify
    agent ran 3 separate navigates for 3 GeometryAnalyzer methods —
    locate collapses that to one call.

    Resolution mirrors peek: full prefix/substring/fuzzy ladder + path
    qualifier. No bodies — just label + file:line."""
    import subprocess
    nodes = [
        {"id": "f", "label": "analyzer.py", "file_type": "code",
         "source_file": "analyzer.py", "source_location": "L1",
         "node_kind": "file"},
        {"id": "cls", "label": "GeometryAnalyzer", "file_type": "code",
         "source_file": "analyzer.py", "source_location": "L5-200",
         "node_kind": "class"},
        {"id": "m1", "label": ".analyze()", "file_type": "code",
         "source_file": "analyzer.py", "source_location": "L42-89",
         "node_kind": "impl_method"},
        {"id": "m2", "label": ".score()", "file_type": "code",
         "source_file": "analyzer.py", "source_location": "L91-105",
         "node_kind": "impl_method"},
        {"id": "m3", "label": ".report()", "file_type": "code",
         "source_file": "analyzer.py", "source_location": "L107-130",
         "node_kind": "impl_method"},
    ]
    links = [
        {"source": "f", "target": "cls", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "cls", "target": "m1", "relation": "method",
         "confidence": "EXTRACTED"},
        {"source": "cls", "target": "m2", "relation": "method",
         "confidence": "EXTRACTED"},
        {"source": "cls", "target": "m3", "relation": "method",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    res = subprocess.run(
        ["python", "-m", "graphify", "locate",
         "GeometryAnalyzer.analyze",
         "GeometryAnalyzer.score",
         "GeometryAnalyzer.report"],
        capture_output=True, text=True, cwd=str(tmp_path), timeout=15,
    )
    assert res.returncode == 0, f"locate failed: {res.stderr}"
    out = res.stdout
    # Each method appears with its file:line range.
    assert "analyzer.py:42-89" in out, (
        f"analyze line range missing:\n{out}"
    )
    assert "analyzer.py:91-105" in out, (
        f"score line range missing:\n{out}"
    )
    assert "analyzer.py:107-130" in out, (
        f"report line range missing:\n{out}"
    )


def test_locate_handles_misses_and_ambiguity_without_failing_the_batch(
        tmp_path, monkeypatch):
    """A locate batch is best-effort: a missed or ambiguous symbol
    surfaces a per-symbol note but doesn't poison hits for other args.
    Exit 0 if at least one symbol resolves; exit 1 only when all miss."""
    import subprocess
    nodes = [
        {"id": "fa", "label": "a.py", "file_type": "code",
         "source_file": "a.py", "source_location": "L1",
         "node_kind": "file"},
        {"id": "fb", "label": "b.py", "file_type": "code",
         "source_file": "b.py", "source_location": "L1",
         "node_kind": "file"},
        # Same-name fns in two files — ambiguous resolution.
        {"id": "pa", "label": "parse()", "file_type": "code",
         "source_file": "a.py", "source_location": "L10-20",
         "node_kind": "function"},
        {"id": "pb", "label": "parse()", "file_type": "code",
         "source_file": "b.py", "source_location": "L30-40",
         "node_kind": "function"},
        # Unique fn — clean resolution.
        {"id": "u", "label": "uniquely_named()", "file_type": "code",
         "source_file": "a.py", "source_location": "L50-60",
         "node_kind": "function"},
    ]
    links = [
        {"source": "fa", "target": "pa", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "fb", "target": "pb", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "fa", "target": "u", "relation": "contains",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    res = subprocess.run(
        ["python", "-m", "graphify", "locate",
         "uniquely_named",  # hits
         "parse",           # ambiguous
         "no_such_symbol"], # miss
        capture_output=True, text=True, cwd=str(tmp_path), timeout=15,
    )
    # Exit 0 because at least one symbol resolved.
    assert res.returncode == 0, f"locate failed: {res.stderr}"
    out = res.stdout
    # Hit line.
    assert "uniquely_named" in out and "a.py:50-60" in out, (
        f"clean hit missing:\n{out}"
    )
    # Ambiguous line names the cause + suggests qualifier.
    assert "parse" in out and "ambiguous" in out, (
        f"ambiguous symbol should be flagged:\n{out}"
    )
    # Miss line names the cause.
    assert "no_such_symbol" in out and "not found" in out, (
        f"miss should be flagged:\n{out}"
    )


def test_locate_exits_nonzero_when_all_symbols_miss(tmp_path, monkeypatch):
    """If every requested symbol fails to resolve, exit 1 — the batch
    was useless, signal that to a shell pipeline."""
    import subprocess
    nodes = [
        {"id": "f", "label": "a.py", "file_type": "code",
         "source_file": "a.py", "source_location": "L1",
         "node_kind": "file"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, [])
    monkeypatch.chdir(tmp_path)
    res = subprocess.run(
        ["python", "-m", "graphify", "locate", "ghost1", "ghost2"],
        capture_output=True, text=True, cwd=str(tmp_path), timeout=15,
    )
    assert res.returncode == 1, (
        f"locate with all-misses should exit 1; got {res.returncode}\n"
        f"stdout:{res.stdout}\nstderr:{res.stderr}"
    )


def test_blast_emits_callers_and_callees_in_one_call(tmp_path, monkeypatch):
    """Lap-22 (meta-harness friction corpus): `graphify blast <symbol>` is
    the one-shot blast-radius command. Two agents independently asked for
    this on the EGF blast-radius task. blast resolves the target, walks
    callers + callees with kind=calls, renders two markdown sections.
    Cursor-free; touches no session."""
    import subprocess
    nodes = [
        {"id": "f", "label": "lib.py", "file_type": "code",
         "source_file": "lib.py", "source_location": "L1",
         "node_kind": "file"},
        {"id": "caller_a", "label": "caller_a()", "file_type": "code",
         "source_file": "lib.py", "source_location": "L5-9",
         "node_kind": "function"},
        {"id": "caller_b", "label": "caller_b()", "file_type": "code",
         "source_file": "lib.py", "source_location": "L11-15",
         "node_kind": "function"},
        {"id": "target", "label": "target_fn()", "file_type": "code",
         "source_file": "lib.py", "source_location": "L20-30",
         "node_kind": "function"},
        {"id": "callee_x", "label": "callee_x()", "file_type": "code",
         "source_file": "lib.py", "source_location": "L40-44",
         "node_kind": "function"},
        {"id": "callee_y", "label": "callee_y()", "file_type": "code",
         "source_file": "lib.py", "source_location": "L46-50",
         "node_kind": "function"},
    ]
    links = [
        # File contains all symbols (structural).
        {"source": "f", "target": "caller_a", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "f", "target": "caller_b", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "f", "target": "target", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "f", "target": "callee_x", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "f", "target": "callee_y", "relation": "contains",
         "confidence": "EXTRACTED"},
        # Two callers → target.
        {"source": "caller_a", "target": "target", "relation": "calls",
         "confidence": "EXTRACTED"},
        {"source": "caller_b", "target": "target", "relation": "calls",
         "confidence": "EXTRACTED"},
        # Target → two callees.
        {"source": "target", "target": "callee_x", "relation": "calls",
         "confidence": "EXTRACTED"},
        {"source": "target", "target": "callee_y", "relation": "calls",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    res = subprocess.run(
        ["python", "-m", "graphify", "blast", "target_fn"],
        capture_output=True, text=True, cwd=str(tmp_path), timeout=15,
    )
    assert res.returncode == 0, f"blast failed: stderr={res.stderr}"
    out = res.stdout
    # Header counts.
    assert "blast @target_fn" in out, f"header missing target:\n{out}"
    assert "2 caller" in out and "2 callee" in out, (
        f"header counts wrong:\n{out}"
    )
    # Two-section structure.
    callers_idx = out.find("## Callers")
    callees_idx = out.find("## Callees")
    assert callers_idx >= 0 and callees_idx > callers_idx, (
        f"both sections must appear, callers before callees:\n{out}"
    )
    callers_block = out[callers_idx:callees_idx]
    callees_block = out[callees_idx:]
    assert "caller_a" in callers_block and "caller_b" in callers_block, (
        f"both callers should appear in callers section:\n{callers_block}"
    )
    assert "callee_x" in callees_block and "callee_y" in callees_block, (
        f"both callees should appear in callees section:\n{callees_block}"
    )
    # Callers shouldn't bleed into callees and vice versa.
    assert "caller_a" not in callees_block, (
        f"caller leaked into callees section:\n{callees_block}"
    )
    assert "callee_x" not in callers_block, (
        f"callee leaked into callers section:\n{callers_block}"
    )


def test_blast_emits_none_when_isolated_symbol(tmp_path, monkeypatch):
    """A symbol with no incoming or outgoing call edges should produce
    `(none)` placeholders for both sections rather than an empty pair of
    headings the agent has to interpret."""
    import subprocess
    nodes = [
        {"id": "f", "label": "lib.py", "file_type": "code",
         "source_file": "lib.py", "source_location": "L1",
         "node_kind": "file"},
        {"id": "lone", "label": "lone_fn()", "file_type": "code",
         "source_file": "lib.py", "source_location": "L5-10",
         "node_kind": "function"},
    ]
    links = [
        {"source": "f", "target": "lone", "relation": "contains",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    res = subprocess.run(
        ["python", "-m", "graphify", "blast", "lone_fn"],
        capture_output=True, text=True, cwd=str(tmp_path), timeout=15,
    )
    assert res.returncode == 0, f"blast failed: stderr={res.stderr}"
    out = res.stdout
    assert "0 callers" in out and "0 callees" in out, (
        f"isolated symbol header should report 0/0:\n{out}"
    )
    # `(none)` placeholder under each empty section.
    assert out.count("(none)") == 2, (
        f"isolated symbol should render two (none) placeholders, got:\n{out}"
    )


def test_dead_ends_pivot_lists_uncalled_methods(tmp_path, monkeypatch):
    """Lap-21 (Gemini #2): `@<focus> dead-ends` lists contained
    function/method children with 0 non-structural in-edges. Surfaces
    candidate dead code in one call."""
    from graphify.navigate import navigate
    nodes = [
        {"id": "f", "label": "lib.py", "file_type": "code",
         "source_file": "lib.py", "source_location": "L1"},
        {"id": "alive", "label": "alive_fn()", "file_type": "code",
         "source_file": "lib.py", "source_location": "L5",
         "node_kind": "function"},
        {"id": "dead1", "label": "orphan_one()", "file_type": "code",
         "source_file": "lib.py", "source_location": "L10",
         "node_kind": "function"},
        {"id": "dead2", "label": "orphan_two()",
         "file_type": "code",
         "source_file": "lib.py", "source_location": "L20",
         "node_kind": "function"},
        {"id": "caller", "label": "user()", "file_type": "code",
         "source_file": "other.py", "source_location": "L1",
         "node_kind": "function"},
    ]
    links = [
        {"source": "f", "target": "alive", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "f", "target": "dead1", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "f", "target": "dead2", "relation": "contains",
         "confidence": "EXTRACTED"},
        # caller calls `alive_fn`, leaving the other two as dead-ends.
        {"source": "caller", "target": "alive", "relation": "calls",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    out = navigate(["@lib.py", "dead-ends"], session=False, fmt="text")
    assert "orphan_one()" in out, f"first uncalled fn should appear:\n{out}"
    assert "orphan_two()" in out, f"second uncalled fn should appear:\n{out}"
    assert "alive_fn()" not in out, (
        f"called fn should be excluded:\n{out}"
    )


def test_peek_class_emits_curated_dump(tmp_path):
    """Lap-21 #2 (sub-agent head-to-head): `peek <Class>` (no method)
    used to dump the entire class body. With many methods that can be
    hundreds of lines and forces the agent to do follow-up `peek
    Class.method_X` calls. Curated dump: class header + each method's
    sig + N body lines, all in one call."""
    import json as _json, subprocess
    nodes = [
        {"id": "cls", "label": "Worker", "file_type": "code",
         "source_file": "worker.py", "source_location": "L1",
         "node_kind": "class"},
        {"id": "m_init", "label": "__init__()", "file_type": "code",
         "source_file": "worker.py", "source_location": "L2-6",
         "node_kind": "method"},
        {"id": "m_run", "label": "run()", "file_type": "code",
         "source_file": "worker.py", "source_location": "L8-12",
         "node_kind": "method"},
        {"id": "m_stop", "label": "stop()", "file_type": "code",
         "source_file": "worker.py", "source_location": "L14-17",
         "node_kind": "method"},
    ]
    links = [
        {"source": "cls", "target": "m_init", "relation": "method",
         "confidence": "EXTRACTED"},
        {"source": "cls", "target": "m_run", "relation": "method",
         "confidence": "EXTRACTED"},
        {"source": "cls", "target": "m_stop", "relation": "method",
         "confidence": "EXTRACTED"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False,
         "graph": {}, "nodes": nodes, "links": links}), encoding="utf-8")
    (tmp_path / "worker.py").write_text(
        "class Worker:\n"                       # L1
        "    def __init__(self, n):\n"          # L2
        "        self.n = n\n"                  # L3
        "        self.queue = []\n"             # L4
        "        self.running = False\n"        # L5
        "        self.cb = None\n"              # L6
        "\n"                                    # L7
        "    def run(self):\n"                  # L8
        "        self.running = True\n"         # L9
        "        for item in self.queue:\n"     # L10
        "            self.process(item)\n"      # L11
        "        return self.n\n"               # L12
        "\n"                                    # L13
        "    def stop(self):\n"                 # L14
        "        self.running = False\n"        # L15
        "        self.queue.clear()\n"          # L16
        "        return None\n"                 # L17
    )
    res = subprocess.run(
        ["graphify", "peek", "Worker",
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert res.returncode == 0, f"peek failed:\n{res.stderr}"
    out = res.stdout
    # Header announces it's a class with method count.
    assert "peek class @Worker" in out and "3 method(s)" in out, (
        f"missing curated header:\n{out}"
    )
    # Class declaration line shown.
    assert "class Worker:" in out, f"missing class header line:\n{out}"
    # All three methods listed.
    assert "__init__()" in out, out
    assert "run()" in out, out
    assert "stop()" in out, out
    # Method bodies sampled — at least one signature + body line per
    # method should appear.
    assert "self.n = n" in out, f"missing __init__ body sample:\n{out}"
    assert "self.running = True" in out, f"missing run body sample:\n{out}"
    # Sort order is by start_line — __init__ before run before stop.
    init_idx = out.index("__init__()")
    run_idx = out.index("run()")
    stop_idx = out.index("stop()")
    assert init_idx < run_idx < stop_idx, (
        f"methods should appear in source order:\n{out}"
    )


def test_expand_brace_multi_target_helper():
    """Lap-25 helper: `prefix{a,b,c}suffix` → N targets. Pattern is
    a peek shortcut for `peek @Class.{m1,m2,m3}`. Only triggers on
    a balanced brace pair containing a comma."""
    from graphify.__main__ import _expand_brace_multi_target
    # Standard class.method expansion.
    assert _expand_brace_multi_target("Worker.{run,stop}") == [
        "Worker.run", "Worker.stop",
    ]
    # @ prefix preserved on each.
    assert _expand_brace_multi_target("@C.{a,b,c}") == ["@C.a", "@C.b", "@C.c"]
    # Whitespace inside braces stripped.
    assert _expand_brace_multi_target("@C.{a, b , c}") == ["@C.a", "@C.b", "@C.c"]
    # No braces — passthrough.
    assert _expand_brace_multi_target("Worker.run") == ["Worker.run"]
    # No comma in braces — passthrough (preserves labels with literal
    # `{var}` template parts).
    assert _expand_brace_multi_target("Worker.{run}") == ["Worker.{run}"]
    # Unbalanced — passthrough.
    assert _expand_brace_multi_target("Worker.{run") == ["Worker.{run"]
    # Empty parts (trailing comma) dropped.
    assert _expand_brace_multi_target("@C.{a,b,}") == ["@C.a", "@C.b"]
    # Suffix preserved.
    assert _expand_brace_multi_target("{a,b}.foo") == ["a.foo", "b.foo"]
    # Nested braces — punt, passthrough.
    assert _expand_brace_multi_target("{a,{b,c}}") == ["{a,{b,c}}"]


def test_peek_brace_expands_to_multi_method(tmp_path):
    """Lap-25: `peek @Class.{m1,m2}` resolves and dumps each method
    body in one call. V2 trial 1 transcript pattern (peek __init__,
    peek add_all_geometries — 2 calls) collapses to 1."""
    import json as _json, subprocess
    nodes = [
        {"id": "cls", "label": "Worker", "file_type": "code",
         "source_file": "worker.py", "source_location": "L1",
         "node_kind": "class"},
        {"id": "m_run", "label": "run()", "file_type": "code",
         "source_file": "worker.py", "source_location": "L2-5",
         "node_kind": "method"},
        {"id": "m_stop", "label": "stop()", "file_type": "code",
         "source_file": "worker.py", "source_location": "L7-10",
         "node_kind": "method"},
        # qualified labels too, for `@Worker.run` resolution.
        {"id": "qm_run", "label": ".run()", "file_type": "code",
         "source_file": "worker.py", "source_location": "L2-5",
         "node_kind": "method"},
        {"id": "qm_stop", "label": ".stop()", "file_type": "code",
         "source_file": "worker.py", "source_location": "L7-10",
         "node_kind": "method"},
    ]
    links = [
        {"source": "cls", "target": "qm_run", "relation": "method",
         "confidence": "EXTRACTED"},
        {"source": "cls", "target": "qm_stop", "relation": "method",
         "confidence": "EXTRACTED"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False, "graph": {},
         "nodes": nodes, "links": links}), encoding="utf-8")
    (tmp_path / "worker.py").write_text(
        "class Worker:\n"                       # L1
        "    def run(self):\n"                  # L2
        "        self.running = True\n"         # L3
        "        return self.process()\n"       # L4
        "    \n"                                # L5
        "\n"                                    # L6
        "    def stop(self):\n"                 # L7
        "        self.running = False\n"        # L8
        "        self.queue.clear()\n"          # L9
        "        return None\n"                 # L10
    )
    res = subprocess.run(
        ["graphify", "peek", "Worker.{run,stop}",
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert res.returncode == 0, f"multi-peek failed:\n{res.stderr}\n{res.stdout}"
    out = res.stdout
    # Multi-peek banner naming target count.
    assert "multi-peek: 2 targets" in out, f"missing multi-peek banner:\n{out}"
    # Section markers for each target.
    assert "[1/2] Worker.run" in out, f"missing [1/2] header:\n{out}"
    assert "[2/2] Worker.stop" in out, f"missing [2/2] header:\n{out}"
    # Both bodies dumped.
    assert "self.running = True" in out, f"missing run body:\n{out}"
    assert "self.running = False" in out, f"missing stop body:\n{out}"


def test_peek_brace_partial_miss_continues(tmp_path):
    """Lap-25: in multi-peek mode, a missing target prints an inline
    error but doesn't abort. The successful targets render, exit 0.
    Single-peek mode keeps fail-fast `sys.exit(1)` semantics — only
    multi mode soft-fails."""
    import json as _json, subprocess
    nodes = [
        {"id": "cls", "label": "Worker", "file_type": "code",
         "source_file": "worker.py", "source_location": "L1",
         "node_kind": "class"},
        {"id": "qm_run", "label": ".run()", "file_type": "code",
         "source_file": "worker.py", "source_location": "L2-4",
         "node_kind": "method"},
    ]
    links = [
        {"source": "cls", "target": "qm_run", "relation": "method",
         "confidence": "EXTRACTED"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False, "graph": {},
         "nodes": nodes, "links": links}), encoding="utf-8")
    (tmp_path / "worker.py").write_text(
        "class Worker:\n"
        "    def run(self):\n"
        "        self.running = True\n"
        "        return None\n"
    )
    # `zqqqzzz_nonexistent` is far enough from any label that the
    # resolver's fuzzy fallback won't pull it onto Worker — gives a
    # clean miss to test the inline error path.
    res = subprocess.run(
        ["graphify", "peek", "{run,zqqqzzz_nonexistent}",
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    # Multi mode: at least one target resolved → exit 0.
    assert res.returncode == 0, (
        f"multi-peek with one valid target should exit 0, got rc={res.returncode}\n"
        f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    # Valid one renders.
    assert "self.running = True" in res.stdout, (
        f"valid target should render:\n{res.stdout}"
    )
    # Missing one names itself in the error so the agent can fix the typo.
    assert "no node matches `zqqqzzz_nonexistent`" in res.stderr, (
        f"missing target should print named error:\n{res.stderr}"
    )


def test_brace_expand_member_miss_helper():
    """Lap-26 helper: detect when a brace-expanded `@Class.member`
    target resolved to its parent class instead of the actual member.
    Returns `(parent, member)` on miss, `None` otherwise.

    The bug it guards against: in the graphify-on-graphify dogfood,
    `peek "@Cursor.{load,save,read}"` resolved `@Cursor.load` and
    `@Cursor.read` to class `Cursor` itself via the fuzzy step
    (similarity ~0.86 because `load`/`read` are short relative to
    the class name). Then the curated class-dump path fired three
    times — one real method body plus two duplicate whole-class
    dumps. With the guard, the misses print per-member miss lines."""
    from graphify.__main__ import _brace_expand_member_miss
    # Bug case: target was `@Cursor.load`, fuzzy resolved to class `Cursor`.
    assert _brace_expand_member_miss("@Cursor.load", "Cursor") == ("Cursor", "load")
    # Same without @ prefix.
    assert _brace_expand_member_miss("Cursor.load", "Cursor") == ("Cursor", "load")
    # Case-insensitive comparison: `cursor.load` against `Cursor` still a miss.
    assert _brace_expand_member_miss("cursor.load", "Cursor") == ("cursor", "load")
    # OK case: chosen IS the actual member (label like `.save()` or `save()`).
    # Trailing parens / leading `.` are decoration the helper strips.
    assert _brace_expand_member_miss("@Cursor.save", "save()") is None
    assert _brace_expand_member_miss("@Cursor.save", ".save()") is None
    assert _brace_expand_member_miss("@Cursor.save", ".save") is None
    # No-dot target — not member-style, never a miss (single-symbol target).
    assert _brace_expand_member_miss("Cursor", "Cursor") is None
    assert _brace_expand_member_miss("@navigate", "navigate") is None
    # Path-qualified — skip (handled by the path-qualifier branch in
    # resolve_focus, not the fuzzy fall-back).
    assert _brace_expand_member_miss("graphify/navigate.py/Cursor", "Cursor") is None
    # Empty parts.
    assert _brace_expand_member_miss(".load", "Cursor") is None
    assert _brace_expand_member_miss("Cursor.", "Cursor") is None


def test_peek_brace_member_fuzzy_fallback_to_class_is_miss(tmp_path):
    """Lap-26 regression: brace-expand `@Worker.{run,q}` where `q` is
    not a member of Worker. The resolver's fuzzy step (cutoff 0.7)
    matches `worker.q` against label `Worker` (similarity ≈ 0.86
    because `q` is one char relative to the class name) and returns
    the class. Without the brace-expand guard, the curated class-dump
    path then fires for the missing member, rendering the WHOLE class
    body instead of a miss line — N missing members produce N
    duplicate class dumps. With the guard, missing members produce a
    per-member `no member \\`q\\` on Worker.` line on stderr and the
    [2/2] stdout section stays empty (no whole-class dump)."""
    import json as _json, subprocess
    nodes = [
        {"id": "cls", "label": "Worker", "file_type": "code",
         "source_file": "worker.py", "source_location": "L1",
         "node_kind": "class"},
        {"id": "qm_run", "label": ".run()", "file_type": "code",
         "source_file": "worker.py", "source_location": "L2-3",
         "node_kind": "method"},
    ]
    links = [
        {"source": "cls", "target": "qm_run", "relation": "method",
         "confidence": "EXTRACTED"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False, "graph": {},
         "nodes": nodes, "links": links}), encoding="utf-8")
    (tmp_path / "worker.py").write_text(
        "class Worker:\n"
        "    def run(self):\n"
        "        return None\n"
    )
    res = subprocess.run(
        ["graphify", "peek", "Worker.{run,q}",
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    # Multi mode with ≥1 valid target → exit 0.
    assert res.returncode == 0, (
        f"multi-peek with one valid + one missing should exit 0:"
        f"\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    # Valid `run` renders.
    assert "return None" in res.stdout, (
        f"valid target should render:\n{res.stdout}"
    )
    # Missing `q` produces a per-member miss line on stderr.
    assert "no member `q` on Worker" in res.stderr, (
        f"missing member should print per-member miss line:\n{res.stderr}"
    )
    # The [2/2] stdout section must NOT contain a whole-class dump.
    # If the guard regresses, "peek class @Worker" would appear under
    # the missing member's [2/2] header.
    after_two = res.stdout.split("[2/2] Worker.q", 1)
    if len(after_two) == 2:
        assert "peek class @Worker" not in after_two[1], (
            f"missing-member [2/2] section must not render whole-class dump:"
            f"\n{after_two[1]}"
        )


def test_blast_brace_expands_to_multi_symbol(tmp_path):
    """Lap-25: `blast @Class.{m1,m2}` runs callers+callees per target
    in one call. Mirrors multi-peek shape — same `# multi-blast: N`
    banner + `# [i/N]` section headers per target."""
    import json as _json, subprocess
    nodes = [
        {"id": "cls", "label": "Worker", "file_type": "code",
         "source_file": "worker.py", "source_location": "L1",
         "node_kind": "class"},
        {"id": "qm_run", "label": ".run()", "file_type": "code",
         "source_file": "worker.py", "source_location": "L2-3",
         "node_kind": "method"},
        {"id": "qm_stop", "label": ".stop()", "file_type": "code",
         "source_file": "worker.py", "source_location": "L5-6",
         "node_kind": "method"},
        # External callers — one for run, one for stop.
        {"id": "caller_a", "label": "start()", "file_type": "code",
         "source_file": "main.py", "source_location": "L1",
         "node_kind": "function"},
        {"id": "caller_b", "label": "shutdown()", "file_type": "code",
         "source_file": "main.py", "source_location": "L8",
         "node_kind": "function"},
    ]
    links = [
        {"source": "cls", "target": "qm_run", "relation": "method",
         "confidence": "EXTRACTED"},
        {"source": "cls", "target": "qm_stop", "relation": "method",
         "confidence": "EXTRACTED"},
        {"source": "caller_a", "target": "qm_run", "relation": "calls",
         "confidence": "EXTRACTED"},
        {"source": "caller_b", "target": "qm_stop", "relation": "calls",
         "confidence": "EXTRACTED"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False, "graph": {},
         "nodes": nodes, "links": links}), encoding="utf-8")
    res = subprocess.run(
        ["graphify", "blast", "Worker.{run,stop}",
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert res.returncode == 0, f"multi-blast failed:\n{res.stderr}\n{res.stdout}"
    out = res.stdout
    assert "multi-blast: 2 targets" in out, f"missing multi-blast banner:\n{out}"
    assert "[1/2] Worker.run" in out, f"missing [1/2] header:\n{out}"
    assert "[2/2] Worker.stop" in out, f"missing [2/2] header:\n{out}"
    # Each target's distinct caller surfaces — proves the per-target
    # pivot ran rather than echoing the same listing.
    assert "start()" in out, f"missing run's caller `start()`:\n{out}"
    assert "shutdown()" in out, f"missing stop's caller `shutdown()`:\n{out}"


def test_peek_brace_all_miss_exits_one(tmp_path):
    """Lap-25: if every target in a multi-peek misses, exit 1 so the
    agent's caller treats it as failure. Mirror locate's batch
    semantics (best-effort, fail only when nothing landed)."""
    import json as _json, subprocess
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False, "graph": {},
         "nodes": [{"id": "x", "label": "x()", "file_type": "code",
                    "source_file": "x.py", "source_location": "L1"}],
         "links": []}), encoding="utf-8")
    (tmp_path / "x.py").write_text("def x():\n    pass\n")
    # Both names are far from `x()` so fuzzy fallback won't catch.
    res = subprocess.run(
        ["graphify", "peek", "{zqqqzzz_a,zqqqzzz_b}",
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert res.returncode == 1, (
        f"all-miss multi-peek should exit 1, got rc={res.returncode}\n"
        f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )


def test_peek_tail_returns_last_n_body_lines(tmp_path):
    """Lap-25 cluster-B: `peek <method> --tail N` returns last N lines of
    the body so an agent inspecting return values / cleanup of a large fn
    doesn't have to walk the whole body. Recovery without --tail is
    `read_file` with a computed offset — three calls minimum."""
    import json as _json, subprocess
    src = (
        "def big_fn():\n"           # L1: header
        "    a = 1\n"                # L2
        "    b = 2\n"                # L3
        "    c = 3\n"                # L4
        "    d = 4\n"                # L5
        "    return a + b + c + d\n" # L6
    )
    (tmp_path / "big.py").write_text(src)
    nodes = [
        {"id": "f", "label": "big_fn()", "file_type": "code",
         "source_file": "big.py", "source_location": "L1-6",
         "node_kind": "function"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False, "graph": {},
         "nodes": nodes, "links": []}), encoding="utf-8")
    res = subprocess.run(
        ["graphify", "peek", "big_fn", "--tail", "2",
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert res.returncode == 0, f"peek --tail failed:\n{res.stderr}\n{res.stdout}"
    out = res.stdout
    # body extracted is 6 lines (L1-6 plus the trailing blank-tolerant
    # walker may include 1 trailing blank — assert structurally on the
    # presence of the return-statement line and the absence of L1's def).
    assert "return a + b + c + d" in out, f"--tail dropped the return:\n{out}"
    # The header line `def big_fn():` should NOT appear when --tail 2 is
    # used (we asked for last 2 lines).
    assert "def big_fn():" not in out, f"--tail 2 should drop header:\n{out}"


def test_peek_range_returns_file_absolute_window(tmp_path):
    """Lap-25 cluster-B: `peek <fn> --range A-B` returns lines A..B
    (file-absolute, inclusive) of the body. Pairs with the `L<x>-<y>`
    line ranges shape/navigate already surface — agent reads the range
    off shape, dumps the slice in one call."""
    import json as _json, subprocess
    src = (
        "def big_fn():\n"           # L1
        "    a = 1\n"                # L2
        "    b = 2\n"                # L3
        "    c = 3\n"                # L4
        "    return a + b + c\n"    # L5
    )
    (tmp_path / "big.py").write_text(src)
    nodes = [
        {"id": "f", "label": "big_fn()", "file_type": "code",
         "source_file": "big.py", "source_location": "L1-5",
         "node_kind": "function"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False, "graph": {},
         "nodes": nodes, "links": []}), encoding="utf-8")
    res = subprocess.run(
        ["graphify", "peek", "big_fn", "--range", "3-4",
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert res.returncode == 0, f"peek --range failed:\n{res.stderr}\n{res.stdout}"
    out = res.stdout
    assert "b = 2" in out, f"--range 3-4 should include line 3:\n{out}"
    assert "c = 3" in out, f"--range 3-4 should include line 4:\n{out}"
    # Lines outside the range must NOT appear.
    assert "a = 1" not in out, f"--range 3-4 leaked line 2:\n{out}"
    assert "return a + b + c" not in out, f"--range 3-4 leaked line 5:\n{out}"


def test_peek_range_out_of_bounds_errors(tmp_path):
    """Lap-25 cluster-B: --range that misses the body prints a named
    error showing the actual body line range so the agent can re-issue
    with the right window. Falls back to soft-fail in multi mode."""
    import json as _json, subprocess
    src = "def small():\n    return 1\n"  # body L1-2
    (tmp_path / "s.py").write_text(src)
    nodes = [
        {"id": "f", "label": "small()", "file_type": "code",
         "source_file": "s.py", "source_location": "L1-2",
         "node_kind": "function"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False, "graph": {},
         "nodes": nodes, "links": []}), encoding="utf-8")
    res = subprocess.run(
        ["graphify", "peek", "small", "--range", "100-200",
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert res.returncode == 1, (
        f"--range outside body should exit 1 in single mode, got "
        f"rc={res.returncode}\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    # Diagnostic names the actual body window so agent can pick valid range.
    assert "falls outside body" in res.stdout, (
        f"missing diagnostic on out-of-range:\n{res.stdout}"
    )


def test_peek_tail_and_range_mutually_exclusive(tmp_path):
    """Lap-25 cluster-B: --tail and --range together is incoherent;
    error fast rather than silently picking one."""
    import json as _json, subprocess
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False, "graph": {},
         "nodes": [{"id": "x", "label": "x()", "file_type": "code",
                    "source_file": "x.py", "source_location": "L1"}],
         "links": []}), encoding="utf-8")
    res = subprocess.run(
        ["graphify", "peek", "x", "--tail", "3", "--range", "1-2",
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert res.returncode == 1
    assert "mutually exclusive" in res.stderr, res.stderr


def test_doc_brace_expands_to_multi_method(tmp_path):
    """Lap-25: `doc @Class.{m1,m2}` dumps signature + rationale for each
    method in one call. Mirrors multi-peek/multi-blast pattern.
    Verb-fusion family complete (bodies / callers+callees / sig+docs)."""
    import json as _json, subprocess
    nodes = [
        {"id": "cls", "label": "Worker", "file_type": "code",
         "source_file": "worker.py", "source_location": "L1",
         "node_kind": "class"},
        {"id": "qm_run", "label": ".run()", "file_type": "code",
         "source_file": "worker.py", "source_location": "L2-3",
         "node_kind": "method"},
        {"id": "qm_stop", "label": ".stop()", "file_type": "code",
         "source_file": "worker.py", "source_location": "L5-6",
         "node_kind": "method"},
    ]
    links = [
        {"source": "cls", "target": "qm_run", "relation": "method",
         "confidence": "EXTRACTED"},
        {"source": "cls", "target": "qm_stop", "relation": "method",
         "confidence": "EXTRACTED"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False, "graph": {},
         "nodes": nodes, "links": links}), encoding="utf-8")
    res = subprocess.run(
        ["graphify", "doc", "Worker.{run,stop}",
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert res.returncode == 0, f"multi-doc failed:\n{res.stderr}\n{res.stdout}"
    out = res.stdout
    assert "multi-doc: 2 targets" in out, f"missing multi-doc banner:\n{out}"
    assert "[1/2] Worker.run" in out, f"missing [1/2] header:\n{out}"
    assert "[2/2] Worker.stop" in out, f"missing [2/2] header:\n{out}"
    # Both methods' labels surface — not just one repeated.
    assert ".run()" in out and ".stop()" in out, (
        f"missing per-target sig output:\n{out}"
    )


def test_doc_brace_partial_miss_continues(tmp_path):
    """Lap-25: multi-doc soft-fails on a missing target — prints the
    miss inline and continues to the next. Mirrors multi-peek's
    batch semantics. Single-target doc still hard-exits on miss."""
    import json as _json, subprocess
    nodes = [
        {"id": "cls", "label": "Worker", "file_type": "code",
         "source_file": "worker.py", "source_location": "L1",
         "node_kind": "class"},
        {"id": "qm_run", "label": ".run()", "file_type": "code",
         "source_file": "worker.py", "source_location": "L2-3",
         "node_kind": "method"},
    ]
    links = [
        {"source": "cls", "target": "qm_run", "relation": "method",
         "confidence": "EXTRACTED"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False, "graph": {},
         "nodes": nodes, "links": links}), encoding="utf-8")
    # Missing target uses zqqqzzz_* so fuzzy can't match it to Worker.
    res = subprocess.run(
        ["graphify", "doc", "Worker.{run,zqqqzzz_nonexistent}",
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert res.returncode == 0, (
        f"multi-doc with one valid target should exit 0, got "
        f"rc={res.returncode}\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    assert ".run()" in res.stdout, f"valid target should render:\n{res.stdout}"
    assert "no node matches `Worker.zqqqzzz_nonexistent`" in res.stderr, (
        f"missing target should print named error:\n{res.stderr}"
    )


def test_doc_brace_json_incompatible(tmp_path):
    """Lap-25: --json + brace-expanded multi-doc is incoherent (multiple
    JSON objects with no separator), so error fast. Single-target --json
    still works."""
    import json as _json, subprocess
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False, "graph": {},
         "nodes": [{"id": "x", "label": "x()", "file_type": "code",
                    "source_file": "x.py", "source_location": "L1",
                    "node_kind": "function"}],
         "links": []}), encoding="utf-8")
    res = subprocess.run(
        ["graphify", "doc", "{a,b}", "--json",
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert res.returncode == 1
    assert "incompatible" in res.stderr, res.stderr


def test_peek_method_default_uses_indent_walker(tmp_path):
    """Lap-25 cluster-B incidental: peek of a method (label `.foo()`)
    with default flags returns the method's body, not a flat dump that
    bleeds into the next sibling. Pre-fix, peek used `_is_file_node`
    (returns True for any `.method()`) to choose flat mode — invisible
    at the 200-line cap, would burst on any `--tail`/`--range`."""
    import json as _json, subprocess
    src = (
        "class C:\n"                 # L1
        "    def first(self):\n"     # L2: header
        "        a = 1\n"            # L3
        "        return a\n"         # L4
        "\n"                          # L5
        "    def second(self):\n"    # L6 — must NOT bleed in
        "        return 99\n"        # L7
    )
    (tmp_path / "c.py").write_text(src)
    nodes = [
        {"id": "cls", "label": "C", "file_type": "code",
         "source_file": "c.py", "source_location": "L1",
         "node_kind": "class"},
        {"id": "m1", "label": ".first()", "file_type": "code",
         "source_file": "c.py", "source_location": "L2-4",
         "node_kind": "impl_method"},
        {"id": "m2", "label": ".second()", "file_type": "code",
         "source_file": "c.py", "source_location": "L6-7",
         "node_kind": "impl_method"},
    ]
    links = [
        {"source": "cls", "target": "m1", "relation": "method",
         "confidence": "EXTRACTED"},
        {"source": "cls", "target": "m2", "relation": "method",
         "confidence": "EXTRACTED"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False, "graph": {},
         "nodes": nodes, "links": links}), encoding="utf-8")
    res = subprocess.run(
        ["graphify", "peek", "C.first",
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert res.returncode == 0, f"peek failed:\n{res.stderr}\n{res.stdout}"
    out = res.stdout
    assert "def first(self):" in out, f"missing method header:\n{out}"
    assert "return a" in out, f"missing first's return:\n{out}"
    # Pre-fix this was the bug: peek bled into the next method's body.
    assert "def second" not in out, (
        f"peek of `C.first` bled into the next method's body:\n{out}"
    )
    assert "return 99" not in out, (
        f"peek of `C.first` bled into `second`'s return:\n{out}"
    )


def test_method_listings_attribute_owning_class(tmp_path, monkeypatch):
    """Lap-21 #1 (sub-agent head-to-head): rows for `.method()`-shape
    nodes show `Class.method()` instead of bare `.method()`. The agent
    can disambiguate `Cursor.pop` from `CursorMock.pop` without
    walking the `parent` pivot."""
    from graphify.navigate import navigate
    nodes = [
        {"id": "ca", "label": "Cursor", "file_type": "code",
         "source_file": "a.py", "source_location": "L1",
         "node_kind": "class"},
        {"id": "cb", "label": "CursorMock", "file_type": "code",
         "source_file": "a.py", "source_location": "L40",
         "node_kind": "class"},
        {"id": "ma", "label": ".pop()", "file_type": "code",
         "source_file": "a.py", "source_location": "L5",
         "node_kind": "impl_method"},
        {"id": "mb", "label": ".pop()", "file_type": "code",
         "source_file": "a.py", "source_location": "L45",
         "node_kind": "impl_method"},
        # Free function — should NOT get owner-class prefix.
        {"id": "free", "label": "compute()", "file_type": "code",
         "source_file": "b.py", "source_location": "L1",
         "node_kind": "function"},
    ]
    links = [
        {"source": "ca", "target": "ma", "relation": "method",
         "confidence": "EXTRACTED"},
        {"source": "cb", "target": "mb", "relation": "method",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)

    # `@.pop()` triggers a substring listing — both methods show owner.
    out_disambig = navigate(["@.pop()"], session=False, fmt="text")
    assert "Cursor.pop()" in out_disambig and "CursorMock.pop()" in out_disambig, (
        f"both methods should show owner-class prefix:\n{out_disambig}"
    )

    # Methods pivot on a class — list rows show owner.
    out_methods = navigate(["@Cursor", "methods"], session=False, fmt="text")
    assert "Cursor.pop()" in out_methods, (
        f"methods listing should show owner:\n{out_methods}"
    )

    # Frontier on a method shows owner in the focus header.
    out_focus = navigate(["@Cursor.pop"], session=False, fmt="text")
    header = next((ln for ln in out_focus.splitlines() if ln.startswith("@ ")), "")
    assert "Cursor.pop()" in header, (
        f"focus header should carry owner:\n{out_focus}"
    )

    # Free function (no leading dot) is NOT prefixed.
    out_free = navigate(["@compute"], session=False, fmt="text")
    assert "compute()" in out_free and "@ .compute()" not in out_free, (
        f"free function should not get owner-class prefix:\n{out_free}"
    )


def test_path_loads_digraph_so_dotted_class_method_resolves(tmp_path):
    """Lap-21 regression: `graphify path` was loading via
    `json_graph.node_link_graph` which respects the `directed` field on
    the JSON. Production graph.json carries `directed: False` even
    though the data is directionally tagged via _src/_tgt; resolve_focus's
    lap-20c dotted-Class.method walk crashed with `'Graph' has no
    attribute 'successors'`. Fix: load via build_from_json(directed=True)."""
    import json as _json, subprocess
    nodes = [
        {"id": "cls", "label": "Cursor", "file_type": "code",
         "source_file": "a.py", "source_location": "L1",
         "node_kind": "class"},
        {"id": "m", "label": ".pop()", "file_type": "code",
         "source_file": "a.py", "source_location": "L5",
         "node_kind": "method"},
        {"id": "fn", "label": "user()", "file_type": "code",
         "source_file": "b.py", "source_location": "L1",
         "node_kind": "function"},
    ]
    links = [
        {"source": "cls", "target": "m", "relation": "method",
         "confidence": "EXTRACTED"},
        {"source": "fn", "target": "m", "relation": "calls",
         "confidence": "EXTRACTED"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    # Production graph.json has `directed: False` because export uses
    # nx.Graph. The path command must rebuild as DiGraph regardless.
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": False, "multigraph": False,
         "graph": {}, "nodes": nodes, "links": links}), encoding="utf-8")
    res = subprocess.run(
        ["graphify", "path", "user()", "Cursor.pop",
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert res.returncode == 0, (
        f"path crashed (likely AttributeError on .successors):\n"
        f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    out = res.stdout + res.stderr
    assert "Shortest path" in out, f"path should resolve:\n{out}"


def test_listing_header_surfaces_transitive_depth_tag(tmp_path, monkeypatch):
    """Lap-21: `out --depth=3` lands on a transitive walk via
    `_transitive_walk`, but the listing header used to look identical
    to the depth=1 case. Surface `[depth≤N]` annotation on the pivot
    header so the agent knows the walk fanned out."""
    from graphify.navigate import navigate
    nodes = [
        {"id": "a", "label": "a()", "file_type": "code",
         "source_file": "x.py", "source_location": "L1"},
        {"id": "b", "label": "b()", "file_type": "code",
         "source_file": "x.py", "source_location": "L5"},
        {"id": "c", "label": "c()", "file_type": "code",
         "source_file": "x.py", "source_location": "L10"},
    ]
    links = [
        {"source": "a", "target": "b", "relation": "calls",
         "confidence": "EXTRACTED"},
        {"source": "b", "target": "c", "relation": "calls",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)

    # Default depth: no tag.
    out_default = navigate(["@a", "out"], session=False, fmt="text",
                            kinds={"calls"}, depth=1)
    pivot_line = next((ln for ln in out_default.splitlines()
                        if "↘out" in ln), "")
    assert "[depth" not in pivot_line, (
        f"depth=1 should not show depth tag:\n{out_default}"
    )

    # depth=3: tag present.
    out_walk = navigate(["@a", "out"], session=False, fmt="text",
                         kinds={"calls"}, depth=3)
    pivot_line2 = next((ln for ln in out_walk.splitlines()
                         if "↘out" in ln), "")
    assert "[depth" in pivot_line2, (
        f"depth>1 should show depth tag:\n{out_walk}"
    )
    # Walk also surfaces the additional callee.
    assert "c()" in out_walk, (
        f"depth=3 walk should reach c() through b():\n{out_walk}"
    )


def test_chain_summary_drops_redundant_resolution_arrow(tmp_path, monkeypatch):
    """Lap-21 polish: chain trace `@Cursor→@Cursor → methods(3)` repeats
    itself when the input matches the resolved label. Drop the
    resolution arrow (`@Cursor`) but keep the original input visible.
    Distinct labels (e.g. `@Cursor.pop` → `.pop()`) keep the arrow."""
    from graphify.navigate import navigate
    nodes = [
        {"id": "n_cls", "label": "Cursor", "file_type": "code",
         "source_file": "a.py", "source_location": "L1",
         "node_kind": "class"},
        {"id": "n_pop", "label": ".pop()", "file_type": "code",
         "source_file": "a.py", "source_location": "L5",
         "node_kind": "impl_method"},
    ]
    links = [
        {"source": "n_cls", "target": "n_pop", "relation": "method",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    # Identical input/label: redundant arrow should be dropped.
    out_same = navigate(["@Cursor", "methods"], session=False, fmt="text")
    chain = next((ln for ln in out_same.splitlines()
                   if ln.lstrip().startswith("chain:")), "")
    assert "@Cursor → methods" in chain, f"chain should show condensed arrow:\n{out_same}"
    assert "@Cursor→@Cursor" not in chain, (
        f"redundant resolution arrow should be dropped:\n{out_same}"
    )

    # Class.method form: input differs from resolved label, arrow stays.
    out_diff = navigate(["@Cursor.pop", "in"], session=False, fmt="text")
    chain2 = next((ln for ln in out_diff.splitlines()
                    if ln.lstrip().startswith("chain:")), "")
    assert "@Cursor.pop→@" in chain2, (
        f"resolution arrow should be kept when input differs from label:\n{out_diff}"
    )


def test_method_no_callers_hints_at_wu(tmp_path, monkeypatch):
    """Lap-21 #2: when a function/method has 0 EXTRACTED callers and 0
    INFERRED to widen to, but lives under a class that itself has
    callers, hint at `wu` for name-mention discovery — the canonical
    typed-receiver-dispatch case (TS-Claude's
    `engine.embeddingGenerate(...)` from 18 files)."""
    from graphify.navigate import navigate
    nodes = [
        {"id": "engine_class", "label": "Engine", "file_type": "code",
         "source_file": "engine.ts", "source_location": "L1",
         "node_kind": "class"},
        {"id": "method", "label": "embeddingGenerate()", "file_type": "code",
         "source_file": "engine.ts", "source_location": "L5",
         "node_kind": "method"},
        # Caller files import Engine but no calls edge lands on the method.
        {"id": "f1", "label": "exp1.ts", "file_type": "code",
         "source_file": "exp1.ts", "source_location": "L1",
         "node_kind": "file"},
        {"id": "f2", "label": "exp2.ts", "file_type": "code",
         "source_file": "exp2.ts", "source_location": "L1",
         "node_kind": "file"},
    ]
    links = [
        # The method belongs to the class (structural; doesn't count as `in`).
        {"source": "engine_class", "target": "method", "relation": "method",
         "confidence": "EXTRACTED"},
        # Caller files import the class — gives the class non-zero in,
        # making the method's parent "referenced" so the hint fires.
        {"source": "f1", "target": "engine_class", "relation": "imports",
         "confidence": "EXTRACTED"},
        {"source": "f2", "target": "engine_class", "relation": "imports",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    out = navigate(["@embeddingGenerate"], session=False, fmt="text")
    assert "0 direct callers" in out and "wu" in out, (
        f"method with 0 callers under a referenced parent should hint "
        f"at wu:\n{out}"
    )


def test_changed_since_commit_flag(tmp_path):
    """Lap-21 polish: `graphify changed --since-commit <ref>` is the
    discoverable form of the positional `changed <ref>` alias. Both
    should produce the same `vs <ref>` output prefix and route through
    the git-diff path rather than the mtime path."""
    import json as _json, subprocess
    # Real git repo so `git diff <ref>` resolves.
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(tmp_path),
                    check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(tmp_path),
                    check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(tmp_path),
                    check=True)
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(tmp_path),
                    check=True)
    sha_res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(tmp_path),
                              capture_output=True, text=True, check=True)
    sha = sha_res.stdout.strip()
    # Modify the file post-commit.
    (tmp_path / "a.py").write_text("def f():\n    return 2\n")
    # Minimal graph.json so changed has somewhere to compare.
    nodes = [{"id": "fn", "label": "f()", "file_type": "code",
              "source_file": "a.py", "source_location": "L1"}]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False,
         "graph": {}, "nodes": nodes, "links": []}), encoding="utf-8")

    # Both the positional ref and --since-commit should resolve to the
    # same git-diff path.
    pos = subprocess.run(
        ["graphify", "changed", sha,
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    flag = subprocess.run(
        ["graphify", "changed", "--since-commit", sha,
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    short = subprocess.run(
        ["graphify", "changed", "--since", sha,
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert pos.returncode == 0, f"positional failed:\n{pos.stderr}"
    assert flag.returncode == 0, f"--since-commit failed:\n{flag.stderr}"
    assert short.returncode == 0, f"--since failed:\n{short.stderr}"
    assert pos.stdout == flag.stdout == short.stdout, (
        f"all three forms should produce identical output;\n"
        f"pos:\n{pos.stdout}\nflag:\n{flag.stdout}\nshort:\n{short.stdout}"
    )
    # And the output should use the `vs <ref>` label, not the mtime label.
    assert f"vs {sha}" in flag.stdout, (
        f"output should reference the git ref:\n{flag.stdout}"
    )


def test_navigate_node_kind_filter_on_substring_disambig(tmp_path, monkeypatch):
    """Lap-21 polish: `@compile` substring listing returned 37 rows
    where the top 3 (the actual functions) were what the user wanted.
    `--node-kind=function` lops file/iface/external rows out of the
    disambig listing."""
    import json as _json, subprocess
    nodes = [
        # The intended targets: functions named compile_*.
        {"id": "fn1", "label": "compile_v1()", "file_type": "code",
         "source_file": "src/v1.ts", "source_location": "L1",
         "node_kind": "function"},
        {"id": "fn2", "label": "compile_v2()", "file_type": "code",
         "source_file": "src/v2.ts", "source_location": "L1",
         "node_kind": "function"},
        # Noise that would otherwise crowd the listing.
        {"id": "iface", "label": "compileSpec", "file_type": "code",
         "source_file": "src/iface.ts", "source_location": "L1",
         "node_kind": "interface"},
        {"id": "type1", "label": "compileResult", "file_type": "code",
         "source_file": "src/types.ts", "source_location": "L1",
         "node_kind": "type_alias"},
        {"id": "file1", "label": "compile_helpers.ts", "file_type": "code",
         "source_file": "src/compile_helpers.ts", "source_location": "L1",
         "node_kind": "file"},
        {"id": "ext1", "label": "compile_external", "file_type": "external",
         "node_kind": "external_module",
         "source_file": "", "source_location": ""},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False,
         "graph": {}, "nodes": nodes, "links": []}), encoding="utf-8")

    res = subprocess.run(
        ["graphify", "navigate", "@compile", "--node-kind=function",
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    out = res.stdout + res.stderr
    assert "compile_v1" in out, f"functions should remain:\n{out}"
    assert "compile_v2" in out, f"functions should remain:\n{out}"
    assert "compileSpec" not in out, f"interface should be filtered:\n{out}"
    assert "compileResult" not in out, f"type_alias should be filtered:\n{out}"
    assert "compile_helpers" not in out, f"file should be filtered:\n{out}"
    assert "node-kind" in out and "hidden" in out, (
        f"hidden-count should be surfaced:\n{out}"
    )


def test_search_by_symbol_collapses_same_symbol_hits(tmp_path, monkeypatch):
    """Lap-21 #7: TS-Claude reported `search 'stagedDecode'` returning 4
    hits all attributed to the same `stagedDecode()` function (different
    line numbers inside its body). With --by-symbol, those collapse to
    one row carrying `×4 (lines: a,b,c,d)`."""
    from graphify.navigate import search_bodies, load_graph
    nodes = [
        {"id": "f", "label": "engine.ts", "file_type": "code",
         "source_file": str(tmp_path / "engine.ts"), "source_location": "L1",
         "community": 0},
        {"id": "fn", "label": "stagedDecode()", "file_type": "code",
         "source_file": str(tmp_path / "engine.ts"), "source_location": "L1",
         "community": 0},
    ]
    links = [{"source": "f", "target": "fn", "relation": "contains",
              "confidence": "EXTRACTED"}]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    (tmp_path / "engine.ts").write_text(
        "function stagedDecode() {\n"
        "  const stagedDecode_a = 1;\n"
        "  const stagedDecode_b = 2;\n"
        "  console.log('stagedDecode running');\n"
        "  return stagedDecode_a + stagedDecode_b;\n"
        "}\n"
    )
    monkeypatch.chdir(tmp_path)
    G, _ = load_graph(tmp_path / "graphify-out" / "graph.json")
    # Default: per-line. Should produce >1 hit.
    flat = search_bodies(G, "stagedDecode")
    assert flat["total"] >= 4, f"per-line should produce ≥4 hits: {flat}"
    # --by-symbol: one row per containing node, with match_count carrying
    # the multiplicity.
    grouped = search_bodies(G, "stagedDecode", by_symbol=True)
    assert grouped["total"] == 1, (
        f"--by-symbol should collapse to one symbol: {grouped}"
    )
    assert grouped["hits"][0]["match_count"] >= 4, (
        f"match_count should reflect underlying line hits: {grouped}"
    )
    assert len(grouped["hits"][0]["match_lines"]) >= 4, (
        f"match_lines should list the line numbers: {grouped}"
    )
    assert grouped["grouped"] == flat["total"] - 1, (
        f"grouped count should equal collapsed delta: {grouped}"
    )


def test_listing_hides_external_nodes_by_default(tmp_path, monkeypatch):
    """Lap-21 #5: external_module nodes (`numpy`, `kg_compile_v2_compile`)
    are stubs from unresolved imports. They should be hidden from
    listings in default/AST mode and surfaced via --include-inferred,
    with the hidden-count called out."""
    import json as _json, subprocess
    nodes = [
        {"id": "fn", "label": "compute()", "file_type": "code",
         "source_file": "src/a.ts", "source_location": "L1",
         "node_kind": "function"},
        {"id": "real_helper", "label": "renderEngine()", "file_type": "code",
         "source_file": "src/b.ts", "source_location": "L1",
         "node_kind": "function"},
        # External stubs — what TS-Claude saw in zero-tvm.
        {"id": "ext1", "label": "kg_compile_v2_compile",
         "file_type": "external", "node_kind": "external_module",
         "source_file": "", "source_location": ""},
        {"id": "ext2", "label": "zero_tvm_engine_core_triggerhooks",
         "file_type": "external", "node_kind": "external_module",
         "source_file": "", "source_location": ""},
    ]
    links = [
        {"source": "fn", "target": "real_helper", "relation": "calls",
         "confidence": "EXTRACTED"},
        {"source": "fn", "target": "ext1", "relation": "imports_from",
         "confidence": "EXTRACTED"},
        {"source": "fn", "target": "ext2", "relation": "imports_from",
         "confidence": "EXTRACTED"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False,
         "graph": {}, "nodes": nodes, "links": links}), encoding="utf-8")

    res = subprocess.run(
        ["graphify", "navigate", "@compute", "out",
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    out = res.stdout + res.stderr
    assert "renderEngine()" in out, f"real symbol should appear:\n{out}"
    assert "kg_compile_v2_compile" not in out, (
        f"external stub should be hidden by default:\n{out}"
    )
    assert "zero_tvm_engine_core_triggerhooks" not in out, (
        f"external stub should be hidden by default:\n{out}"
    )
    assert "external" in out and "hidden" in out, (
        f"hidden-count should be surfaced:\n{out}"
    )

    # --include-inferred should reveal them.
    res2 = subprocess.run(
        ["graphify", "navigate", "@compute", "out", "--include-inferred",
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    out2 = res2.stdout + res2.stderr
    assert "kg_compile_v2_compile" in out2, (
        f"--include-inferred should surface externals:\n{out2}"
    )


def test_transitive_noop_notice_on_file_with_direct_out(tmp_path, monkeypatch):
    """Lap-21 #6: --transitive silently no-ops when the file already has
    direct out-edges. Surface a notice so the agent knows the flag did
    nothing."""
    import json as _json, subprocess
    nodes = [
        {"id": "fileA", "label": "a.ts", "file_type": "code",
         "source_file": "a.ts", "source_location": "L1"},
        {"id": "fileB", "label": "b.ts", "file_type": "code",
         "source_file": "b.ts", "source_location": "L1"},
        {"id": "child", "label": "child()", "file_type": "code",
         "source_file": "a.ts", "source_location": "L5"},
    ]
    links = [
        # fileA imports fileB → direct out-edge.
        {"source": "fileA", "target": "fileB", "relation": "imports_from",
         "confidence": "EXTRACTED"},
        {"source": "fileA", "target": "child", "relation": "contains",
         "confidence": "EXTRACTED"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False,
         "graph": {}, "nodes": nodes, "links": links}), encoding="utf-8")
    res = subprocess.run(
        ["graphify", "navigate", "@a.ts", "out", "--transitive",
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    out = res.stdout + res.stderr
    assert "--transitive: not applied" in out, (
        f"--transitive on file with direct out should emit notice:\n{out}"
    )


def test_transitive_noop_notice_on_symbol_node(tmp_path, monkeypatch):
    """Lap-21 #6: --transitive on a non-file focus is a no-op too."""
    import json as _json, subprocess
    nodes = [
        {"id": "fn", "label": "compute()", "file_type": "code",
         "source_file": "a.ts", "source_location": "L1",
         "node_kind": "function"},
        {"id": "callee", "label": "helper()", "file_type": "code",
         "source_file": "b.ts", "source_location": "L1"},
    ]
    links = [
        {"source": "fn", "target": "callee", "relation": "calls",
         "confidence": "EXTRACTED"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False,
         "graph": {}, "nodes": nodes, "links": links}), encoding="utf-8")
    res = subprocess.run(
        ["graphify", "navigate", "@compute", "out", "--transitive",
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    out = res.stdout + res.stderr
    assert "--transitive: only applies to file nodes" in out, (
        f"--transitive on symbol focus should emit notice:\n{out}"
    )


def test_frontier_header_drops_hub_marker_when_focus_is_hub(tmp_path, monkeypatch):
    """Lap-21 polish: when the focused node IS the community's hub,
    `c53 hub:buildDecodeEngine()` restates the focus's own label.
    Suppress to `c53` in that case."""
    from graphify.navigate import navigate
    nodes = [
        {"id": "n_engine", "label": "DecodeEngine", "file_type": "code",
         "source_file": "b.py", "source_location": "L2", "community": 3},
        {"id": "n_uno", "label": "alpha", "file_type": "code",
         "source_file": "c.py", "source_location": "L3", "community": 3},
        {"id": "n_duo", "label": "beta", "file_type": "code",
         "source_file": "d.py", "source_location": "L4", "community": 3},
    ]
    links = [
        {"source": "n_uno", "target": "n_engine", "relation": "calls",
         "confidence": "EXTRACTED"},
        {"source": "n_duo", "target": "n_engine", "relation": "calls",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    out = navigate(["@DecodeEngine"], session=False, fmt="text")
    header = next((ln for ln in out.splitlines() if ln.startswith("@ ")), "")
    assert "DecodeEngine" in header, f"focus label should appear:\n{out}"
    assert "hub:" not in header, (
        f"focusing on the hub itself should suppress `hub:` repeat:\n{out}"
    )


def test_disambig_listing_suppresses_files_table(tmp_path, monkeypatch):
    """Lap-13 field-report fix: on @-disambig listings the `[a-h]` file
    letters and `[1-N]` pick numbers stack confusingly. Each row shows
    its path inline; the table is just visual noise on disambig."""
    from graphify.navigate import navigate
    # Three same-label nodes across different files — hits the
    # `len(items) >= 3` condition that would trigger the files-table.
    nodes = []
    for i, fp in enumerate(["a.py", "b.py", "c.py"]):
        nodes.append({
            "id": f"compile_{i}",
            "label": "compile",
            "file_type": "code",
            "source_file": fp,
            "source_location": f"L{10+i}",
        })
    _write_graph(tmp_path / "graphify-out", nodes, [])
    monkeypatch.chdir(tmp_path)
    out = navigate(["@compile"], session=False, fmt="text")
    # Disambig fired (3 substring matches).
    assert "ambiguous" in out, f"expected disambig listing:\n{out}"
    # The files-table opens with `  files:` and lists `[a]` `[b]` etc.
    # Neither should appear on a disambig listing.
    assert "files:" not in out, (
        f"files-table should not render on @-disambig:\n{out}"
    )
    # And the `[a]` / `[b]` letter prefixes shouldn't appear as table
    # entries (they could legitimately appear in path text, but never
    # as `    [a] <file>` table rows).
    table_rows = [ln for ln in out.splitlines()
                  if ln.lstrip().startswith(("[a] ", "[b] ", "[c] "))]
    assert not table_rows, (
        f"no `[letter] <file>` table rows on disambig:\n{out}"
    )


def test_one_shot_focus_persists_cursor(tmp_path, monkeypatch):
    """Lap-22 meta-harness field-report (reverses lap-13): a one-shot
    focus DOES write a cursor and DOES print the session id, so an
    agent who wants to chain on the next CLI invocation has an id to
    pass to `--session`. _sweep_stale handles cleanup of orphans."""
    from graphify.navigate import navigate
    nodes = [
        {"id": "f", "label": "foo()", "file_type": "code",
         "source_file": "a.py", "source_location": "L1"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, [])
    monkeypatch.chdir(tmp_path)
    out = navigate(["@foo()"], fmt="text")  # default session=True
    # Session id IS printed so the agent can pass it to --session.
    assert "session:" in out, (
        f"one-shot focus should print session id for chain-resumption:\n{out}"
    )
    # Cursor file IS written.
    nav_dir = tmp_path / "graphify-out" / ".navigate"
    assert nav_dir.exists(), f"navigate dir missing: {nav_dir}"
    files = list(nav_dir.glob("*.json"))
    assert files, "expected at least one persisted cursor"


def test_label_index_dedups_same_label_and_nid(tmp_path):
    """Regression: when a node's id and label normalize to the same key
    (e.g. id='atlas', label='Atlas'), `label_index` used to append the
    nid twice — turning `@Atlas` into a spurious disambig. The index
    now tracks per-key membership."""
    import networkx as nx
    from graphify.resolve import label_index
    G = nx.DiGraph()
    G.add_node("atlas", label="Atlas")
    G.add_node("engine", label="DecodeEngine")
    idx = label_index(G)
    # `_norm("Atlas")` == `_norm("atlas")` → both keys land on the same
    # bucket. The bucket should contain "atlas" exactly once.
    bucket = idx.get("atlas") or []
    assert bucket.count("atlas") == 1, (
        f"label_index should dedup same-label/same-nid entries, "
        f"got {bucket}"
    )


def test_chained_walk_does_persist_cursor(tmp_path, monkeypatch):
    """Counterpart to the one-shot test: a chain that walks 2 steps
    (focus → pivot) sets `cursor.history` and SHOULD persist the cursor
    so the agent can follow up via --session."""
    from graphify.navigate import navigate
    nodes = [
        {"id": "f", "label": "Foo", "file_type": "code",
         "source_file": "a.py", "source_location": "L1",
         "node_kind": "class"},
        {"id": "m", "label": ".bar()", "file_type": "code",
         "source_file": "a.py", "source_location": "L5",
         "node_kind": "impl_method"},
    ]
    links = [{"source": "f", "target": "m", "relation": "method",
              "confidence": "EXTRACTED"}]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    # @Foo → methods → [1] is a 2-step walk: focus (push), then pick (push).
    out = navigate(["@Foo", "methods", "[1]"], fmt="text")
    assert "session:" in out, (
        f"multi-step walk should print session id:\n{out}"
    )
    nav_dir = tmp_path / "graphify-out" / ".navigate"
    files = list(nav_dir.glob("*.json")) if nav_dir.exists() else []
    assert files, (
        f"multi-step walk should persist cursor, got nothing in {nav_dir}"
    )


def test_auto_widen_in_when_extracted_zero_inferred_present(tmp_path, monkeypatch):
    """When `in` returns 0 EXTRACTED edges but ≥1 INFERRED edge is hidden,
    auto-widen so the agent sees the data instead of "+N INFERRED hidden"
    + a forced retry. Header surfaces `(auto-widened)`; a note line below
    explains the contract; rows render with [inf@<score>] tags."""
    from graphify.navigate import navigate
    nodes = [
        {"id": "tgt", "label": "doStuff()", "file_type": "code",
         "source_file": "a.ts", "source_location": "L10"},
        {"id": "caller1", "label": "callerA()", "file_type": "code",
         "source_file": "b.ts", "source_location": "L5"},
        {"id": "caller2", "label": "callerB()", "file_type": "code",
         "source_file": "b.ts", "source_location": "L15"},
    ]
    links = [
        # No EXTRACTED in-edges to tgt; only INFERRED.
        {"source": "caller1", "target": "tgt", "relation": "calls",
         "confidence": "INFERRED", "confidence_score": 0.8},
        {"source": "caller2", "target": "tgt", "relation": "calls",
         "confidence": "INFERRED", "confidence_score": 0.8},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    # Default extracted_only=True. Listing should auto-widen.
    out = navigate(["@doStuff", "in"], session=False, fmt="text")
    assert "auto-widened" in out, f"expected (auto-widened) tag:\n{out}"
    # The two inferred callers should show up
    assert "callerA()" in out and "callerB()" in out, (
        f"both inferred callers should be in widened listing:\n{out}"
    )
    # Per-row inferred tag is the trust signal
    assert "[calls/inf@" in out, (
        f"rows should be tagged [calls/inf@<score>]:\n{out}"
    )
    # The auto-widen note should explain what happened
    assert "0 extracted; auto-widened" in out, (
        f"explanatory note missing:\n{out}"
    )


def test_auto_widen_does_not_fire_when_extracted_present(tmp_path, monkeypatch):
    """When at least one EXTRACTED in-edge exists, return only those —
    don't widen, even though INFERRED edges exist too. Auto-widen is for
    the empty-extracted case only."""
    from graphify.navigate import navigate
    nodes = [
        {"id": "tgt", "label": "doStuff()", "file_type": "code",
         "source_file": "a.ts", "source_location": "L10"},
        {"id": "real", "label": "realCaller()", "file_type": "code",
         "source_file": "b.ts", "source_location": "L5"},
        {"id": "guess", "label": "guessedCaller()", "file_type": "code",
         "source_file": "c.ts", "source_location": "L1"},
    ]
    links = [
        {"source": "real", "target": "tgt", "relation": "calls",
         "confidence": "EXTRACTED"},
        {"source": "guess", "target": "tgt", "relation": "calls",
         "confidence": "INFERRED", "confidence_score": 0.8},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    out = navigate(["@doStuff", "in"], session=False, fmt="text")
    assert "auto-widened" not in out, (
        f"should NOT widen when EXTRACTED edges exist:\n{out}"
    )
    assert "realCaller()" in out, f"missing real caller:\n{out}"
    # The inferred edge should be filtered (not widened in), so the +1 INFERRED
    # hidden drop count surfaces instead.
    assert "guessedCaller()" not in out, (
        f"inferred edge should be hidden, not auto-widened:\n{out}"
    )
    assert "1 INFERRED hidden" in out, (
        f"expected normal +1 INFERRED hidden tag:\n{out}"
    )


def test_quiet_hints_suppresses_all_hint_lines(tmp_path, monkeypatch):
    """`--quiet-hints` (or `quiet_hints=True`) suppresses every `hint:`
    line. The user's bro flagged hint repetition as the most common
    noise — `--quiet-hints` is the full-silence escape hatch."""
    from graphify.navigate import navigate
    nodes = [
        # An empty class triggers the protocol/marker hint.
        {"id": "p", "label": "Pet", "file_type": "code",
         "source_file": "p.py", "source_location": "L1",
         "node_kind": "class"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, [])
    monkeypatch.chdir(tmp_path)
    # Default: hint should appear.
    out_default = navigate(["@Pet"], session=False, fmt="text")
    assert "hint:" in out_default, (
        f"baseline: hint should appear by default:\n{out_default}"
    )
    # With --quiet-hints: no hint anywhere in output.
    out_quiet = navigate(["@Pet"], session=False, fmt="text",
                         quiet_hints=True)
    assert "hint:" not in out_quiet, (
        f"--quiet-hints should suppress all hint lines:\n{out_quiet}"
    )


def test_hint_dedup_per_named_session(tmp_path, monkeypatch):
    """Each hint kind shows once per `--session <id>` session. A second
    call with the same focus should not re-emit the same hint."""
    from graphify.navigate import navigate
    nodes = [
        {"id": "p", "label": "Pet", "file_type": "code",
         "source_file": "p.py", "source_location": "L1",
         "node_kind": "class"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, [])
    monkeypatch.chdir(tmp_path)
    out1 = navigate(["@Pet"], session="ded1", fmt="text")
    assert "hint: class with no methods/contains" in out1, (
        f"first call should emit the hint:\n{out1}"
    )
    # Same session, same focus — hint already shown, should not repeat.
    out2 = navigate([], session="ded1", fmt="text")
    assert "hint: class with no methods/contains" not in out2, (
        f"per-session dedup: same hint should not re-emit:\n{out2}"
    )


def test_hint_dedup_does_not_apply_to_ephemeral(tmp_path, monkeypatch):
    """Ephemeral (default session=True) calls don't dedup across calls
    because each is a fresh cursor. The agent gets the hint on every
    call until they commit to `--session <id>`."""
    from graphify.navigate import navigate
    nodes = [
        {"id": "p", "label": "Pet", "file_type": "code",
         "source_file": "p.py", "source_location": "L1",
         "node_kind": "class"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, [])
    monkeypatch.chdir(tmp_path)
    # Two ephemeral calls — both should show the hint (no carry-over).
    out1 = navigate(["@Pet"], session=False, fmt="text")
    out2 = navigate(["@Pet"], session=False, fmt="text")
    assert "hint: class with no methods/contains" in out1, out1
    assert "hint: class with no methods/contains" in out2, (
        f"ephemeral mode should re-emit hints (no session means no dedup):\n{out2}"
    )


def test_where_used_combines_edges_and_text_mentions(tmp_path, monkeypatch):
    """`where-used` (alias `wu`) returns: (a) AST-edge callers via `in`,
    plus (b) text mentions of the symbol's name in code bodies — combined
    in one listing. Edge-discovered rows are listed first; text-only rows
    follow with `[mentions L<line>]` tags so the agent sees the discovery
    source. Solves the dynamic-dispatch / string-keyed-lookup gap where
    AST callers are 0 but the symbol IS used."""
    from graphify.navigate import navigate
    sf_caller = str(tmp_path / "caller.py")
    sf_target = str(tmp_path / "target.py")
    sf_mention = str(tmp_path / "registry.py")
    nodes = [
        {"id": "tgt", "label": "spectral_coherence", "file_type": "code",
         "source_file": sf_target, "source_location": "L1"},
        # Edge-caller: real `calls` edge into tgt.
        {"id": "ec", "label": "edge_caller()", "file_type": "code",
         "source_file": sf_caller, "source_location": "L1"},
        # Text-mention: separate node whose body contains the literal name.
        {"id": "reg", "label": "build_registry()", "file_type": "code",
         "source_file": sf_mention, "source_location": "L1"},
    ]
    links = [
        {"source": "ec", "target": "tgt", "relation": "calls",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    (tmp_path / "caller.py").write_text("def edge_caller():\n    pass\n")
    (tmp_path / "target.py").write_text("def spectral_coherence():\n    pass\n")
    (tmp_path / "registry.py").write_text(
        "def build_registry():\n"
        "    return {'spectral_coherence': lookup}\n"
    )
    monkeypatch.chdir(tmp_path)
    out = navigate(["@spectral_coherence", "where-used"], session=False, fmt="text")
    assert "edge_caller()" in out, (
        f"edge-discovered caller missing:\n{out}"
    )
    assert "build_registry()" in out, (
        f"text-mentioned node missing:\n{out}"
    )
    assert "[mentions L" in out, (
        f"text-only rows should carry [mentions L<n>] tag:\n{out}"
    )
    assert "via edges" in out and "text-only" in out, (
        f"header breakdown should show edge vs text counts:\n{out}"
    )


def test_where_used_alias_wu_works(tmp_path, monkeypatch):
    """`wu` is the short alias. Both should route to the same dispatch."""
    from graphify.navigate import navigate
    nodes = [
        {"id": "tgt", "label": "X", "file_type": "code",
         "source_file": str(tmp_path / "t.py"), "source_location": "L1"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, [])
    (tmp_path / "t.py").write_text("X = 1\n")
    monkeypatch.chdir(tmp_path)
    out = navigate(["@X", "wu"], session=False, fmt="text")
    assert "where-used" in out, (
        f"`wu` alias should dispatch to where-used:\n{out}"
    )


def test_shape_file_returns_counts_and_longest_fn(tmp_path, monkeypatch):
    """`shape_file` summarizes a file's structure: N classes / M fns /
    K consts / X imports / longest fn (line span). Saves a `contains`
    pivot when the agent just wants orientation."""
    from graphify.navigate import shape_file, load_graph
    sf = str(tmp_path / "lib.py")
    nodes = [
        {"id": "f", "label": "lib.py", "file_type": "code",
         "source_file": sf, "source_location": "L1"},
        # 2 classes
        {"id": "ca", "label": "Alpha", "file_type": "code",
         "source_file": sf, "source_location": "L5",
         "node_kind": "class"},
        {"id": "cb", "label": "Beta", "file_type": "code",
         "source_file": sf, "source_location": "L40",
         "node_kind": "class"},
        # 3 functions; one big one in the middle
        {"id": "fn1", "label": "small()", "file_type": "code",
         "source_file": sf, "source_location": "L70"},
        {"id": "fn2", "label": "huge()", "file_type": "code",
         "source_file": sf, "source_location": "L75"},
        {"id": "fn3", "label": "tiny()", "file_type": "code",
         "source_file": sf, "source_location": "L155"},
        # 1 const
        {"id": "k", "label": "MAX_RETRIES", "file_type": "code",
         "source_file": sf, "source_location": "L160"},
        # 1 imported module
        {"id": "ext", "label": "math", "file_type": "code",
         "source_file": "math.py", "source_location": "L1"},
    ]
    links = [
        {"source": "f", "target": "ca", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "f", "target": "cb", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "f", "target": "fn1", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "f", "target": "fn2", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "f", "target": "fn3", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "f", "target": "k", "relation": "contains",
         "confidence": "EXTRACTED"},
        # 1 import
        {"source": "f", "target": "ext", "relation": "imports",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    # File needs to be N lines so total_lines is real
    (tmp_path / "lib.py").write_text("# line\n" * 200)
    monkeypatch.chdir(tmp_path)
    G, _comm = load_graph(tmp_path / "graphify-out" / "graph.json")
    data = shape_file(G, "f")
    assert data["classes"] == 2, data
    assert data["fns"] == 3, data
    assert data["consts"] == 1, data
    # The longest fn is `huge()` spanning L75..L154 = 80 lines.
    assert data["longest_fn"] is not None
    assert data["longest_fn"]["label"] == "huge()", data["longest_fn"]
    assert data["longest_fn"]["lines"] == 80, data["longest_fn"]
    assert data["total_lines"] == 200


def test_shape_file_surfaces_fn_line_ranges(tmp_path, monkeypatch):
    """Lap-21 #3 (sub-agent head-to-head): shape's `fns:` line lists
    `f1(), f2()` (names only), forcing agents that fall back to
    Read --offset/--limit to do an extra navigate call to recover the
    range. Surface `name L<start>-<end>` per fn so the range is
    available in the same shape call."""
    from graphify.navigate import shape_file, _render_shape_text, load_graph
    sf = str(tmp_path / "lib.py")
    nodes = [
        {"id": "f", "label": "lib.py", "file_type": "code",
         "source_file": sf, "source_location": "L1"},
        {"id": "fn1", "label": "alpha()", "file_type": "code",
         "source_file": sf, "source_location": "L10-25",
         "node_kind": "function"},
        {"id": "fn2", "label": "beta()", "file_type": "code",
         "source_file": sf, "source_location": "L30-50",
         "node_kind": "function"},
    ]
    links = [
        {"source": "f", "target": "fn1", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "f", "target": "fn2", "relation": "contains",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    (tmp_path / "lib.py").write_text("# line\n" * 100)
    monkeypatch.chdir(tmp_path)
    G, _comm = load_graph(tmp_path / "graphify-out" / "graph.json")
    data = shape_file(G, "f")
    entries = data.get("fn_entries") or []
    assert len(entries) == 2, entries
    by_label = {e["label"]: e for e in entries}
    assert by_label["alpha()"]["start_line"] == 10
    assert by_label["alpha()"]["end_line"] == 25
    assert by_label["beta()"]["start_line"] == 30
    rendered = _render_shape_text(data)
    assert "alpha() L10-25" in rendered, (
        f"shape should surface line ranges:\n{rendered}"
    )
    assert "beta() L30-50" in rendered, rendered


def test_shape_file_surfaces_entry_points(tmp_path, monkeypatch):
    """Lap-21: shape lists fns ranked by external in-edges so an agent
    landing on a multi-thousand-line file sees the API surface, not
    just the longest fn."""
    from graphify.navigate import shape_file, _render_shape_text, load_graph
    sf = str(tmp_path / "engine.py")
    other_sf = str(tmp_path / "consumer.py")
    nodes = [
        {"id": "f", "label": "engine.py", "file_type": "code",
         "source_file": sf, "source_location": "L1"},
        {"id": "fn1", "label": "popular()", "file_type": "code",
         "source_file": sf, "source_location": "L5"},
        {"id": "fn2", "label": "less_popular()", "file_type": "code",
         "source_file": sf, "source_location": "L20"},
        {"id": "fn3", "label": "internal()", "file_type": "code",
         "source_file": sf, "source_location": "L40"},
        # External callers from a different file.
        *[{"id": f"caller{i}", "label": f"caller{i}()", "file_type": "code",
           "source_file": other_sf, "source_location": f"L{i}"}
          for i in range(5)],
        # An internal-only call (same file) — should not count toward
        # entry-point rank for fn3.
        {"id": "same_file_caller", "label": "same_file_caller()",
         "file_type": "code",
         "source_file": sf, "source_location": "L80"},
    ]
    links = [
        {"source": "f", "target": "fn1", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "f", "target": "fn2", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "f", "target": "fn3", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "f", "target": "same_file_caller",
         "relation": "contains", "confidence": "EXTRACTED"},
        # 4 cross-file calls into popular.
        *[{"source": f"caller{i}", "target": "fn1", "relation": "calls",
           "confidence": "EXTRACTED"} for i in range(4)],
        # 1 cross-file call into less_popular.
        {"source": "caller4", "target": "fn2", "relation": "calls",
         "confidence": "EXTRACTED"},
        # 1 internal call into internal — should NOT count.
        {"source": "same_file_caller", "target": "fn3", "relation": "calls",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    G, _comm = load_graph(tmp_path / "graphify-out" / "graph.json")
    data = shape_file(G, "f")
    eps = data.get("entry_points") or []
    assert eps, f"shape should surface entry points: {data}"
    labels = [e["label"] for e in eps]
    assert labels[0] == "popular()", f"highest-ext-in fn should top: {eps}"
    assert "less_popular()" in labels, f"single-caller fn should appear: {eps}"
    assert "internal()" not in labels, (
        f"internal-only fn should not appear (no cross-file callers): {eps}"
    )
    rendered = _render_shape_text(data)
    assert "entry points:" in rendered, (
        f"renderer should print entry-points line:\n{rendered}"
    )
    assert "popular() (×4)" in rendered, (
        f"entry point should carry ext-in count:\n{rendered}"
    )


def test_shape_file_surfaces_top_of_file_docstring(tmp_path, monkeypatch):
    """Lap-24 follow-up: shape inlines up to ~5 lines of the file's
    leading docstring or comment block. Empirical from session-benchmark
    t0 transcript: agent ran `shape` then chased a `read_file <file>`
    just to read the orientation paragraph at the top. Surfacing it in
    the same shape call saves the follow-up.

    Covers both Python triple-quoted module docstrings and line-comment
    leaders (`#`, `//`)."""
    from graphify.navigate import shape_file, _render_shape_text, load_graph
    # Python triple-quoted.
    py_sf = tmp_path / "py_module.py"
    py_sf.write_text(
        '"""Top-line summary of the module.\n'
        '\n'
        'Second paragraph mentions a key concept.\n'
        '"""\n'
        '\n'
        'def foo():\n'
        '    return 1\n'
    )
    nodes = [
        {"id": "f", "label": "py_module.py", "file_type": "code",
         "source_file": str(py_sf), "source_location": "L1",
         "node_kind": "file"},
        {"id": "fn", "label": "foo()", "file_type": "code",
         "source_file": str(py_sf), "source_location": "L6",
         "node_kind": "function"},
    ]
    links = [{"source": "f", "target": "fn", "relation": "contains",
              "confidence": "EXTRACTED"}]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    G, _comm = load_graph(tmp_path / "graphify-out" / "graph.json")
    data = shape_file(G, "f")
    docstring = data.get("docstring") or []
    assert docstring, f"docstring should be captured: {data}"
    assert "Top-line summary of the module." in docstring, docstring
    assert "Second paragraph mentions a key concept." in docstring, docstring
    rendered = _render_shape_text(data)
    assert "> Top-line summary of the module." in rendered, (
        f"docstring should render with `> ` prefix:\n{rendered}"
    )

    # Line-comment leader.
    cm_sf = tmp_path / "comment_module.py"
    cm_sf.write_text(
        "# Module that does the thing.\n"
        "# Stores X and Y, returns Z.\n"
        "\n"
        "def bar():\n"
        "    return 2\n"
    )
    nodes2 = [
        {"id": "f2", "label": "comment_module.py", "file_type": "code",
         "source_file": str(cm_sf), "source_location": "L1",
         "node_kind": "file"},
        {"id": "fn2", "label": "bar()", "file_type": "code",
         "source_file": str(cm_sf), "source_location": "L4",
         "node_kind": "function"},
    ]
    links2 = [{"source": "f2", "target": "fn2", "relation": "contains",
               "confidence": "EXTRACTED"}]
    _write_graph(tmp_path / "graphify-out", nodes2, links2)
    G2, _ = load_graph(tmp_path / "graphify-out" / "graph.json")
    data2 = shape_file(G2, "f2")
    docstring2 = data2.get("docstring") or []
    assert "Module that does the thing." in docstring2, docstring2
    assert "Stores X and Y, returns Z." in docstring2, docstring2
    # Blank line ends the comment block — fn body shouldn't leak in.
    assert not any("def bar" in d for d in docstring2), docstring2


def test_shape_file_jsdoc_block_no_empty_leading_line(tmp_path, monkeypatch):
    """Lap-24 follow-up bugfix: a JSDoc-style `/**` opener was leaving
    an empty leading line in the docstring (rendered as a bare `> `
    with nothing after it). The opener line `/**` strips to `*` then
    to `""`; the extractor was appending the empty string. Only
    real content should land in the docstring list."""
    from graphify.navigate import shape_file, _render_shape_text, load_graph
    sf = tmp_path / "doc.ts"
    sf.write_text(
        "/**\n"
        " * WINDOWED + STACKED OVERRIDES (Exp 70)\n"
        " *\n"
        " * Builds on Exp 69's finding to tackle pipeline issues.\n"
        " */\n"
        "\n"
        "export const config = {}\n"
    )
    nodes = [
        {"id": "f", "label": "doc.ts", "file_type": "code",
         "source_file": str(sf), "source_location": "L1",
         "node_kind": "file"},
    ]
    links = []
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    G, _comm = load_graph(tmp_path / "graphify-out" / "graph.json")
    data = shape_file(G, "f")
    docstring = data.get("docstring") or []
    assert docstring, f"JSDoc block should be captured: {data}"
    # First captured line is real content, not empty.
    assert docstring[0] == "WINDOWED + STACKED OVERRIDES (Exp 70)", (
        f"first docstring line should be content, not empty:\n{docstring}"
    )
    rendered = _render_shape_text(data)
    # No bare `>` lines (`>` immediately followed by newline or
    # whitespace-only).
    for line in rendered.split("\n"):
        if line.strip() == ">":
            assert False, f"empty `>` line in render:\n{rendered}"


def test_shape_file_no_docstring_when_file_starts_with_code(tmp_path, monkeypatch):
    """No leading docstring/comment → empty docstring list, no `>`
    line in rendered output. Avoids inventing fake context for files
    that just start with imports."""
    from graphify.navigate import shape_file, _render_shape_text, load_graph
    sf = tmp_path / "code_only.py"
    sf.write_text(
        "import os\n"
        "import sys\n"
        "\n"
        "def baz():\n"
        "    return 3\n"
    )
    nodes = [
        {"id": "f", "label": "code_only.py", "file_type": "code",
         "source_file": str(sf), "source_location": "L1",
         "node_kind": "file"},
        {"id": "fn", "label": "baz()", "file_type": "code",
         "source_file": str(sf), "source_location": "L4",
         "node_kind": "function"},
    ]
    links = [{"source": "f", "target": "fn", "relation": "contains",
              "confidence": "EXTRACTED"}]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    G, _comm = load_graph(tmp_path / "graphify-out" / "graph.json")
    data = shape_file(G, "f")
    assert data.get("docstring") == [], (
        f"no leading docstring → empty list:\n{data}"
    )
    rendered = _render_shape_text(data)
    assert "> " not in rendered, (
        f"no docstring → no `> ` lines in render:\n{rendered}"
    )


def test_shape_file_promotes_entry_point_fns_past_limit(tmp_path, monkeypatch):
    """Lap-24: shape's fns list now stamps `×N` for cross-file callers
    AND pins entry-point fns into the truncated listing even when they
    fall past `limit`. Without this, the file's actual API surface is
    hidden inside the `+N more` tail whenever the public function lives
    past the first 8 definitions.

    Empirical case: EGF tools/dynamical_fingerprint.py has classify()
    as the 16th fn (L542). Old default-limit-8 listing showed 8
    internal helpers + `+13 more` and never named classify in the fns
    line — agents had to follow up with navigate/grep to find which
    fn was the export. The entry-points line below names it but the
    fns list itself shouldn't undersell the API surface."""
    from graphify.navigate import shape_file, _render_shape_text, load_graph
    sf = str(tmp_path / "engine.py")
    other_sf = str(tmp_path / "consumer.py")
    # 12 internal helpers (no cross-file callers), then 1 public fn at
    # the end. Default limit is 8 so the public fn falls past it.
    nodes = [
        {"id": "f", "label": "engine.py", "file_type": "code",
         "source_file": sf, "source_location": "L1"},
    ]
    for i in range(12):
        nodes.append({
            "id": f"h{i}", "label": f"helper{i:02d}()", "file_type": "code",
            "source_file": sf, "source_location": f"L{(i + 1) * 10}",
            "node_kind": "function",
        })
    nodes.append({
        "id": "pub", "label": "publish()", "file_type": "code",
        "source_file": sf, "source_location": "L500",
        "node_kind": "function",
    })
    nodes.append({
        "id": "caller", "label": "caller()", "file_type": "code",
        "source_file": other_sf, "source_location": "L1",
        "node_kind": "function",
    })
    links = [
        {"source": "f", "target": f"h{i}", "relation": "contains",
         "confidence": "EXTRACTED"} for i in range(12)
    ]
    links.append({"source": "f", "target": "pub", "relation": "contains",
                  "confidence": "EXTRACTED"})
    # Cross-file caller into pub.
    links.append({"source": "caller", "target": "pub", "relation": "calls",
                  "confidence": "EXTRACTED"})
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    G, _comm = load_graph(tmp_path / "graphify-out" / "graph.json")
    data = shape_file(G, "f")  # default limit=8
    fn_labels = [e["label"] for e in data.get("fn_entries") or []]
    # All 8 helpers in source order ARE in the listing, AND publish is
    # promoted into the listing despite being the 13th fn by source.
    assert "publish()" in fn_labels, (
        f"entry-point fn should be promoted past --limit: {fn_labels}"
    )
    pub_entry = next(e for e in data["fn_entries"] if e["label"] == "publish()")
    assert pub_entry["ext_in"] == 1, pub_entry
    rendered = _render_shape_text(data)
    # Marker shows next to publish() in the rendered fns line.
    assert "publish() ×1" in rendered, (
        f"renderer should stamp ×N on entry-point fns:\n{rendered}"
    )
    # Helpers without cross-file callers must not get a `×N` suffix.
    assert "helper00() ×" not in rendered, (
        f"helpers without ext callers must not get ×N:\n{rendered}"
    )
    assert "helper07() ×" not in rendered, rendered
    # `+N more` count should reflect the un-promoted tail (4 helpers
    # left, not 5 — publish was promoted out of the tail).
    assert "+4 more" in rendered, (
        f"promoted entry-point fn should not double-count in `+N more`:\n{rendered}"
    )


def test_shape_file_limit_and_all(tmp_path, monkeypatch):
    """`shape_file(..., limit=N)` truncates class_labels/fn_labels to N;
    `limit=None` returns the full lists. `+N more` is the renderer's job."""
    from graphify.navigate import shape_file, load_graph
    sf = str(tmp_path / "big.py")
    nodes = [{"id": "f", "label": "big.py", "file_type": "code",
              "source_file": sf, "source_location": "L1"}]
    for i in range(20):
        nodes.append({
            "id": f"c{i}", "label": f"Cls{i}", "file_type": "code",
            "source_file": sf, "source_location": f"L{i*5+5}",
            "node_kind": "class",
        })
    links = [{"source": "f", "target": f"c{i}", "relation": "contains",
              "confidence": "EXTRACTED"} for i in range(20)]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    (tmp_path / "big.py").write_text("# line\n" * 200)
    monkeypatch.chdir(tmp_path)
    G, _comm = load_graph(tmp_path / "graphify-out" / "graph.json")
    default_limit = shape_file(G, "f")
    assert len(default_limit["class_labels"]) == 8, default_limit
    raised = shape_file(G, "f", limit=15)
    assert len(raised["class_labels"]) == 15, raised
    full = shape_file(G, "f", limit=None)
    assert len(full["class_labels"]) == 20, full


def test_doc_node_emits_signature_and_rationale(tmp_path, monkeypatch):
    """`doc_node` walks rationale_for edges and emits sig + docstring(s).
    Reporter wish: skip the manual symbol → method → docstring pivot."""
    from graphify.navigate import doc_node, _render_doc_text, load_graph
    sf = str(tmp_path / "metrics.py")
    nodes = [
        {"id": "fn", "label": "compute_metrics()", "file_type": "code",
         "source_file": sf, "source_location": "L1",
         "node_kind": "function"},
        {"id": "rat1", "label": "Compute the metrics for a corpus.",
         "file_type": "rationale",
         "source_file": sf, "source_location": "L2"},
    ]
    links = [
        {"source": "rat1", "target": "fn", "relation": "rationale_for",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    (tmp_path / "metrics.py").write_text(
        'def compute_metrics(data):\n'
        '    """Compute the metrics for a corpus.\n'
        '\n'
        '    Returns a dict.\n'
        '    """\n'
        '    return {}\n'
    )
    monkeypatch.chdir(tmp_path)
    G, _comm = load_graph(tmp_path / "graphify-out" / "graph.json")
    data = doc_node(G, "fn")
    assert data["label"] == "compute_metrics()"
    assert data["header"].startswith("def compute_metrics"), data["header"]
    assert len(data["rationale"]) == 1, data["rationale"]
    rendered = _render_doc_text(data)
    assert "compute_metrics()" in rendered
    assert "sig: def compute_metrics" in rendered
    # No-rationale case prints a hint instead of an empty section.
    G2, _ = load_graph(tmp_path / "graphify-out" / "graph.json")
    G2.remove_node("rat1")
    data2 = doc_node(G2, "fn")
    rendered2 = _render_doc_text(data2)
    assert "no rationale attached" in rendered2


def test_doc_node_collapses_multiline_ts_signature(tmp_path, monkeypatch):
    """Lap-21 #4: `doc @compile` truncated at the first newline of the
    signature, printing `sig: export async function compile(` on a
    multi-line TS sig. Fix: read the full signature and join with
    spaces."""
    from graphify.navigate import doc_node, _render_doc_text, load_graph
    sf = str(tmp_path / "compile.ts")
    nodes = [
        {"id": "compile_fn", "label": "compile()", "file_type": "code",
         "source_file": sf, "source_location": "L1",
         "node_kind": "function"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, [])
    (tmp_path / "compile.ts").write_text(
        "export async function compile(\n"
        "  spec: Spec,\n"
        "  opts: Opts,\n"
        "): Promise<Engine> {\n"
        "  return engine;\n"
        "}\n"
    )
    monkeypatch.chdir(tmp_path)
    G, _comm = load_graph(tmp_path / "graphify-out" / "graph.json")
    data = doc_node(G, "compile_fn")
    rendered = _render_doc_text(data)
    assert "spec: Spec" in rendered, (
        f"sig should include params, got:\n{rendered}"
    )
    assert "Promise<Engine>" in rendered, (
        f"sig should include return type, got:\n{rendered}"
    )
    # And the sig line should be a SINGLE line — no embedded newlines.
    sig_lines = [ln for ln in rendered.splitlines() if ln.strip().startswith("sig:")]
    assert len(sig_lines) == 1, f"expected one sig line, got {sig_lines}"


def test_doc_rationale_does_not_leak_function_body(tmp_path, monkeypatch):
    """Lap-26 field-report fix: Python rationale extractor emits docstring
    nodes with `source_location = L<start>` (no end line). The doc verb's
    contract is "signature + docstring/rationale dump — without pulling
    the implementation," but the prior flat reader walked the next 40
    lines from the docstring start, sweeping in body code. The bug
    surfaced as ~30 body lines under each docstring."""
    from graphify.navigate import doc_node, _render_doc_text, load_graph
    sf = str(tmp_path / "leaky.py")
    nodes = [
        {"id": "fn", "label": "build_axis()", "file_type": "code",
         "source_file": sf, "source_location": "L1",
         "node_kind": "function"},
        {"id": "rat", "label": "Build the axis from a regime spec.",
         "file_type": "rationale",
         "source_file": sf, "source_location": "L2"},
    ]
    links = [
        {"source": "rat", "target": "fn", "relation": "rationale_for",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    (tmp_path / "leaky.py").write_text(
        'def build_axis(spec):\n'
        '    """Build the axis from a regime spec.\n'
        '\n'
        '    Returns the axis array.\n'
        '    """\n'
        '    body_line_one = compute_one()\n'
        '    body_line_two = compute_two()\n'
        '    body_line_three = compute_three()\n'
        '    DOCSTRING_BLEED_SENTINEL = True  # if this leaks, doc is broken\n'
        '    return body_line_one\n'
    )
    monkeypatch.chdir(tmp_path)
    G, _comm = load_graph(tmp_path / "graphify-out" / "graph.json")
    data = doc_node(G, "fn")
    assert len(data["rationale"]) == 1
    rendered = _render_doc_text(data)
    assert "DOCSTRING_BLEED_SENTINEL" not in rendered, (
        f"doc leaked function body. rendered:\n{rendered}"
    )
    assert "compute_one" not in rendered, (
        f"doc leaked function body. rendered:\n{rendered}"
    )
    # And the docstring itself is intact.
    assert "Build the axis from a regime spec" in rendered
    assert "Returns the axis array" in rendered


def test_find_leading_docstring_range_python_multiline():
    """Lap-26: peek --no-docstring elides the leading triple-quoted
    block via the renderer (preserving file-absolute line numbers)
    rather than mutating the line list. Helper returns the half-open
    index range to elide."""
    from graphify.navigate import _find_leading_docstring_range
    lines = [
        'def foo(x):',                                     # idx 0 — sig (kept)
        '    """Build the axis from a regime spec.',       # idx 1 — ds open
        '',                                                # idx 2 — ds blank
        '    Returns the axis array.',                     # idx 3 — ds body
        '    """',                                         # idx 4 — ds close
        '    return compute(x)',                           # idx 5 — body
    ]
    lo, hi = _find_leading_docstring_range(lines)
    assert (lo, hi) == (1, 5), (lo, hi)
    # Half-open: 4 lines elided (indices 1..4).
    assert hi - lo == 4


def test_find_leading_docstring_range_python_single_line():
    """Single-line `\"\"\"foo\"\"\"` docstring resolves to a 1-line range."""
    from graphify.navigate import _find_leading_docstring_range
    lines = [
        'def foo(x):',
        '    """One-liner."""',
        '    return x',
    ]
    lo, hi = _find_leading_docstring_range(lines)
    assert (lo, hi) == (1, 2), (lo, hi)


def test_find_leading_docstring_range_jsdoc():
    """JSDoc `/** */` block recognized."""
    from graphify.navigate import _find_leading_docstring_range
    lines = [
        'function foo(x) {',
        '  /**',
        '   * Build the axis.',
        '   */',
        '  return compute(x);',
        '}',
    ]
    lo, hi = _find_leading_docstring_range(lines)
    assert (lo, hi) == (1, 4), (lo, hi)


def test_find_leading_docstring_range_no_docstring_returns_zero():
    """Body without a leading docstring returns (0, 0) — renderer treats
    as "nothing to elide"."""
    from graphify.navigate import _find_leading_docstring_range
    lines = [
        'def foo(x):',
        '    return x + 1',
    ]
    assert _find_leading_docstring_range(lines) == (0, 0)


def test_render_body_text_elides_docstring_range_keeps_line_numbers():
    """The renderer skips lines inside the elision range, prints a
    marker, and keeps file-absolute line numbers correct on either
    side of the gap."""
    from graphify.navigate import _render_body_text
    body = [
        'def foo(x):',
        '    """One-liner."""',
        '    return x',
    ]
    rendered = _render_body_text({
        "type": "body",
        "label": "foo()",
        "source_file": "f.py",
        "source_location": "L100",
        "lines": body,
        "start_line": 100,
        "truncated": False,
        "docstring_range": (1, 2),
    })
    # Sig stays at line 100.
    assert "100  def foo(x):" in rendered, rendered
    # Body resumes at line 102 (NOT 101 — line 101 is the elided docstring).
    assert "102      return x" in rendered, rendered
    # Marker is printed.
    assert "1 docstring lines elided" in rendered or "elided" in rendered, rendered
    # Header reports the elision count.
    assert "−1 docstring" in rendered, rendered


def test_expand_identifier_casings_emits_all_five_forms():
    """Lap-26 field-report fix: agent had to manually OR
    `modal-complexity|modal_complexity` for a rename audit. `--idents`
    expands one canonical identifier to all 5 casings + word boundaries."""
    from graphify.navigate import _expand_identifier_casings
    rgx, casings = _expand_identifier_casings("modal_complexity")
    assert "modal_complexity" in casings, casings
    assert "modal-complexity" in casings, casings
    assert "modalComplexity" in casings, casings
    assert "ModalComplexity" in casings, casings
    assert "MODAL_COMPLEXITY" in casings, casings
    # Regex word-boundary anchored so it doesn't hit `modal_complexity_v2`.
    import re
    assert re.search(rgx, "x = modal_complexity()"), rgx
    assert re.search(rgx, "x = modalComplexity()"), rgx
    assert re.search(rgx, "x = MODAL_COMPLEXITY"), rgx
    assert not re.search(rgx, "modal_complexity_v2"), (
        f"\\b boundary should fail on suffix-extended ident, regex was {rgx}"
    )


def test_expand_identifier_casings_handles_camel_input():
    """Input may already be camelCase or PascalCase — tokenizer splits on
    upper-boundary so we still emit all 5 forms."""
    from graphify.navigate import _expand_identifier_casings
    _, casings = _expand_identifier_casings("modalComplexity")
    assert "modal_complexity" in casings, casings
    assert "modal-complexity" in casings, casings


def test_expand_identifier_casings_single_token_no_op():
    """A single-token identifier has no boundaries to differ on. Don't
    emit a fake regex that matches every word starting with `f`."""
    from graphify.navigate import _expand_identifier_casings
    _, casings = _expand_identifier_casings("foo")
    # Casings collapse for a single token. snake/kebab/camel all = "foo";
    # pascal = "Foo"; screaming = "FOO". After dedupe: 3 unique.
    assert "foo" in casings
    assert "Foo" in casings
    assert "FOO" in casings
    assert len(casings) == 3, casings


def test_doc_rationale_handles_single_line_comment(tmp_path, monkeypatch):
    """Comment-style rationale (`# NOTE: …`) is single-line. The reader
    must NOT walk past it into surrounding code."""
    from graphify.navigate import doc_node, _render_doc_text, load_graph
    sf = str(tmp_path / "comment_ratio.py")
    nodes = [
        {"id": "fn", "label": "compute()", "file_type": "code",
         "source_file": sf, "source_location": "L4",
         "node_kind": "function"},
        {"id": "rat", "label": "# NOTE: this is the rationale comment.",
         "file_type": "rationale",
         "source_file": sf, "source_location": "L1"},
    ]
    links = [
        {"source": "rat", "target": "fn", "relation": "rationale_for",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    (tmp_path / "comment_ratio.py").write_text(
        '# NOTE: this is the rationale comment.\n'
        'BODY_AFTER_COMMENT = "should not appear in doc"\n'
        '\n'
        'def compute():\n'
        '    return 1\n'
    )
    monkeypatch.chdir(tmp_path)
    G, _comm = load_graph(tmp_path / "graphify-out" / "graph.json")
    data = doc_node(G, "fn")
    rendered = _render_doc_text(data)
    assert "BODY_AFTER_COMMENT" not in rendered, (
        f"comment-rationale leaked next line. rendered:\n{rendered}"
    )


def test_search_bodies_returns_hits_with_symbol_context(tmp_path, monkeypatch):
    """`search_bodies` greps each node's source file and attaches symbol
    context (label, file:line, community, degree) to each match. The
    contract: an agent searching for `spectral_coherence` should never
    have to drop to bare grep."""
    from graphify.navigate import search_bodies, load_graph
    nodes = [
        {"id": "f1", "label": "metrics.py", "file_type": "code",
         "source_file": str(tmp_path / "metrics.py"), "source_location": "L1",
         "community": 0},
        {"id": "fn", "label": "compute_metrics()", "file_type": "code",
         "source_file": str(tmp_path / "metrics.py"), "source_location": "L5",
         "community": 0},
    ]
    links = [{"source": "f1", "target": "fn", "relation": "contains",
              "confidence": "EXTRACTED"}]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    (tmp_path / "metrics.py").write_text(
        "import math\n"
        "import statistics\n"
        "\n"
        "\n"
        "def compute_metrics(data):\n"
        "    spectral_coherence = compute_spectral(data)\n"
        "    peakedness = compute_peak(data)\n"
        "    return {'spectral_coherence': spectral_coherence}\n"
    )
    monkeypatch.chdir(tmp_path)
    G, _comm = load_graph(tmp_path / "graphify-out" / "graph.json")
    res = search_bodies(G, "spectral_coherence")
    assert res["total"] >= 2, f"expected ≥2 hits, got {res['total']}: {res}"
    labels = {h["label"] for h in res["hits"]}
    # All hits should attribute to the function (deepest enclosing decl).
    assert "compute_metrics()" in labels, (
        f"deepest-enclosing-decl attribution failed: {res['hits']}"
    )
    # Match line numbers should land on actual hit lines.
    match_lines = sorted(h["match_line"] for h in res["hits"])
    assert match_lines == [6, 8], (
        f"expected match lines [6, 8], got {match_lines}"
    )


def test_search_files_only_collapses_to_one_row_per_file(tmp_path, monkeypatch):
    """Lap-27: `search --files-only` is the grep -l analog — one row
    per file with match count, no per-line content. Closes the
    `grep -l "X" tests/*.py` pattern (agent wants 'which files
    contain X' before deciding which to read).
    """
    from graphify.navigate import search_bodies, load_graph
    (tmp_path / "a.py").write_text(
        "def foo():\n    return spectral_coherence(1)\n"
        "    spectral_coherence = 5\n"
    )
    (tmp_path / "b.py").write_text(
        "spectral_coherence = 1\n"
    )
    (tmp_path / "c.py").write_text("nothing here\n")
    nodes = [
        {"id": "fa", "label": "a.py", "file_type": "code",
         "source_file": str(tmp_path / "a.py"), "source_location": "L1",
         "node_kind": "file"},
        {"id": "fb", "label": "b.py", "file_type": "code",
         "source_file": str(tmp_path / "b.py"), "source_location": "L1",
         "node_kind": "file"},
        {"id": "fc", "label": "c.py", "file_type": "code",
         "source_file": str(tmp_path / "c.py"), "source_location": "L1",
         "node_kind": "file"},
        {"id": "foo", "label": "foo()", "file_type": "code",
         "source_file": str(tmp_path / "a.py"), "source_location": "L1"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, [
        {"source": "fa", "target": "foo", "relation": "contains",
         "confidence": "EXTRACTED"},
    ])
    monkeypatch.chdir(tmp_path)
    G, _comm = load_graph(tmp_path / "graphify-out" / "graph.json")
    res = search_bodies(G, "spectral_coherence", files_only=True)
    # One row per file, not per match line.
    files = {h["source_file"] for h in res["hits"]}
    assert len(res["hits"]) == 2, (
        f"expected 2 file rows, got {len(res['hits'])}: {res['hits']}"
    )
    # a.py has 2 matches (lines 2, 3); b.py has 1.
    by_file = {h["source_file"]: h for h in res["hits"]}
    a_path = str(tmp_path / "a.py")
    b_path = str(tmp_path / "b.py")
    assert by_file[a_path]["match_count"] == 2
    assert by_file[b_path]["match_count"] == 1


def test_search_in_files_glob_filters_results(tmp_path, monkeypatch):
    """Lap-27: `search --in-files <glob>` restricts results to source_files
    matching the glob. Closes the `grep -r "X" --include="*test*.py"`
    pattern."""
    from graphify.navigate import search_bodies, load_graph
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("magic_token = 1\n")
    (tmp_path / "src.py").write_text("magic_token = 2\n")
    nodes = [
        {"id": "ft", "label": "test_x.py", "file_type": "code",
         "source_file": str(tmp_path / "tests" / "test_x.py"),
         "source_location": "L1", "node_kind": "file"},
        {"id": "fs", "label": "src.py", "file_type": "code",
         "source_file": str(tmp_path / "src.py"), "source_location": "L1",
         "node_kind": "file"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, [])
    monkeypatch.chdir(tmp_path)
    G, _comm = load_graph(tmp_path / "graphify-out" / "graph.json")
    # Without filter: 2 hits. With filter: 1.
    full = search_bodies(G, "magic_token")
    assert full["total"] == 2, f"expected 2 unfiltered: {full['hits']}"
    filtered = search_bodies(G, "magic_token", in_files="*test*.py")
    assert filtered["total"] == 1
    assert "test_x.py" in filtered["hits"][0]["source_file"]


def test_search_bodies_substring_fallback_on_bad_regex(tmp_path, monkeypatch):
    """A pattern that fails `re.compile` (unbalanced paren etc.) should
    fall back to literal substring search and surface mode='substring'
    so the agent knows their `(` wasn't read as regex."""
    from graphify.navigate import search_bodies, load_graph
    nodes = [
        {"id": "fn", "label": "f()", "file_type": "code",
         "source_file": str(tmp_path / "f.py"), "source_location": "L1"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, [])
    (tmp_path / "f.py").write_text("def f(x):\n    return foo(x)\n")
    monkeypatch.chdir(tmp_path)
    G, _comm = load_graph(tmp_path / "graphify-out" / "graph.json")
    res = search_bodies(G, "foo(")  # unclosed group → re.error
    assert res["mode"] == "substring", f"expected substring fallback: {res}"
    assert res["total"] >= 1


def test_search_bodies_skips_archived_by_default(tmp_path, monkeypatch):
    """Archive heuristic (`frozen|legacy|deprecated|archive(d)/...`)
    should drop archived hits unless --archived-only / --all-archived
    is set. Default: active code only."""
    from graphify.navigate import search_bodies, load_graph
    legacy_dir = tmp_path / "legacy"
    active_dir = tmp_path / "src"
    legacy_dir.mkdir()
    active_dir.mkdir()
    (legacy_dir / "old.py").write_text("# uses NEEDLE in legacy\n")
    (active_dir / "new.py").write_text("# uses NEEDLE in active\n")
    nodes = [
        {"id": "old", "label": "old.py", "file_type": "code",
         "source_file": "legacy/old.py", "source_location": "L1"},
        {"id": "new", "label": "new.py", "file_type": "code",
         "source_file": "src/new.py", "source_location": "L1"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, [])
    monkeypatch.chdir(tmp_path)
    G, _comm = load_graph(tmp_path / "graphify-out" / "graph.json")
    res = search_bodies(G, "NEEDLE", archived_mode="no")
    files = {h["source_file"] for h in res["hits"]}
    assert "src/new.py" in files
    assert "legacy/old.py" not in files, (
        f"archived path should be excluded by default: {res['hits']}"
    )


def test_class_dot_method_resolves_to_method_node(tmp_path, monkeypatch):
    """Dotted Class.method form should resolve directly to the method node.
    Lap-15 (consumer-Claude friction): `peek "Runner.__init__"` returned
    "no node matches" — natural Python/JS/TS dotted form should just work."""
    from graphify.navigate import navigate
    nodes = [
        {"id": "cls", "label": "Runner", "file_type": "code",
         "source_file": "r.py", "source_location": "L1",
         "node_kind": "class"},
        {"id": "init", "label": ".__init__()", "file_type": "code",
         "source_file": "r.py", "source_location": "L5",
         "node_kind": "impl_method"},
        {"id": "run", "label": ".run()", "file_type": "code",
         "source_file": "r.py", "source_location": "L20",
         "node_kind": "impl_method"},
    ]
    links = [
        {"source": "cls", "target": "init", "relation": "method",
         "confidence": "EXTRACTED"},
        {"source": "cls", "target": "run", "relation": "method",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    out = navigate(["@Runner.__init__"], session=False, fmt="text")
    assert ".__init__()" in out, (
        f"Class.method should resolve to method node:\n{out}"
    )
    # Also test bare `name.method` (no underscore prefix on method).
    out2 = navigate(["@Runner.run"], session=False, fmt="text")
    assert ".run()" in out2, f"Class.method (no underscore):\n{out2}"
    # And the parenthesised form should work too.
    out3 = navigate(["@Runner.run()"], session=False, fmt="text")
    assert ".run()" in out3, f"Class.method() with parens:\n{out3}"


def test_class_dot_method_via_peek_subcommand(tmp_path, monkeypatch):
    """peek `<Class>.<method>` should resolve via the same shared resolver,
    so the natural dotted form works for one-shot body reads too."""
    import subprocess, sys
    nodes = [
        {"id": "cls", "label": "Runner", "file_type": "code",
         "source_file": "r.py", "source_location": "L1",
         "node_kind": "class"},
        {"id": "init", "label": ".__init__()", "file_type": "code",
         "source_file": "r.py", "source_location": "L5",
         "node_kind": "impl_method"},
    ]
    links = [{"source": "cls", "target": "init", "relation": "method",
              "confidence": "EXTRACTED"}]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    # Have to write a stub source file for the body-read to land somewhere.
    (tmp_path / "r.py").write_text("class Runner:\n    def __init__(self):\n        pass\n")
    monkeypatch.chdir(tmp_path)
    res = subprocess.run(
        [sys.executable, "-m", "graphify", "peek", "Runner.__init__"],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert res.returncode == 0, (
        f"peek Runner.__init__ should resolve, got rc={res.returncode}; "
        f"stderr={res.stderr!r}; stdout={res.stdout!r}"
    )
    assert ".__init__()" in res.stdout, (
        f"peek output should include resolved label:\n{res.stdout}"
    )


def test_hint_empty_class_points_at_inheritance_and_read(tmp_path, monkeypatch):
    """A class node with 0 methods + 0 contains is likely a Protocol/ABC
    or marker. The hint should name the shape and point at `inh` /
    `in --kind=inherits` / `read` instead of leaving the agent staring
    at empty pivots."""
    from graphify.navigate import navigate
    nodes = [
        {"id": "p", "label": "Pet", "file_type": "code",
         "source_file": "p.py", "source_location": "L1",
         "node_kind": "class"},
        {"id": "anim", "label": "Animal", "file_type": "code",
         "source_file": "anim.py", "source_location": "L1",
         "node_kind": "class"},
    ]
    links = [
        # Pet inherits from Animal; Pet has no methods or contains.
        {"source": "p", "target": "anim", "relation": "inherits",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    out = navigate(["@Pet"], session=False, fmt="text")
    assert "protocol/abstract/marker class" in out, (
        f"empty-class hint missing:\n{out}"
    )
    assert "inh" in out and "read" in out, (
        f"hint should name `inh` and `read`:\n{out}"
    )


def test_hint_file_with_only_rationale_points_at_read(tmp_path, monkeypatch):
    """When a code-file hub's only contains-children are rationale
    fragments (docs/comments), surface that so the agent doesn't waste
    a `contains` pivot reading doc rows masquerading as decls."""
    from graphify.navigate import navigate
    nodes = [
        {"id": "f", "label": "doc.py", "file_type": "code",
         "source_file": "doc.py", "source_location": "L1"},
        {"id": "r1", "label": "module-doc", "file_type": "rationale",
         "source_file": "doc.py", "source_location": "L1"},
        {"id": "r2", "label": "fn-doc", "file_type": "rationale",
         "source_file": "doc.py", "source_location": "L20"},
        {"id": "r3", "label": "class-doc", "file_type": "rationale",
         "source_file": "doc.py", "source_location": "L40"},
    ]
    links = [
        {"source": "f", "target": "r1", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "f", "target": "r2", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "f", "target": "r3", "relation": "contains",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    out = navigate(["@doc.py"], session=False, fmt="text")
    assert "rationale fragments" in out, (
        f"docs-mostly file hint missing:\n{out}"
    )
    assert "read" in out, f"hint should point at `read`:\n{out}"


def test_hint_loose_orphan_with_parent_points_at_coc_and_parent(tmp_path, monkeypatch):
    """A function with no in/out/contains/methods but a parent and
    community membership should suggest `parent` (climb up) and `coc`
    (cluster context). Hard-orphan hint requires literal emptiness; this
    looser case is more common (uncalled helpers under a parent class)."""
    from graphify.navigate import navigate
    nodes = [
        # Parent file containing helper, plus 2 community siblings (so coc>0).
        {"id": "f", "label": "tools.py", "file_type": "code",
         "source_file": "tools.py", "source_location": "L1",
         "community": 0},
        {"id": "h", "label": "helper()", "file_type": "code",
         "source_file": "tools.py", "source_location": "L10",
         "community": 0},
        {"id": "s1", "label": "sib1()", "file_type": "code",
         "source_file": "tools.py", "source_location": "L20",
         "community": 0},
        {"id": "s2", "label": "sib2()", "file_type": "code",
         "source_file": "tools.py", "source_location": "L30",
         "community": 0},
    ]
    links = [
        # f contains h (parent>0 for h), and contains the siblings (so
        # coc community has members).
        {"source": "f", "target": "h", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "f", "target": "s1", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "f", "target": "s2", "relation": "contains",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    out = navigate(["@helper"], session=False, fmt="text")
    assert "lives in a community" in out, (
        f"loose-orphan-in-cluster hint missing:\n{out}"
    )
    assert "`parent`" in out and "`coc`" in out, (
        f"hint should point at parent and coc:\n{out}"
    )


def test_back_reset_widgets_hidden_on_ephemeral_session(tmp_path, monkeypatch):
    """Backlog #9: with default ephemeral session (session=True), the cursor
    can be persisted (when chained ≥1 step) and the id is printed, but
    `back`/`reset`/`↺(N)` UI should not appear because the agent has no
    way to act on history without --session <id> first. Show those
    widgets only on resumed sessions."""
    from graphify.navigate import navigate
    nodes = [
        {"id": "f", "label": "Foo", "file_type": "code",
         "source_file": "a.py", "source_location": "L1",
         "node_kind": "class"},
        {"id": "m", "label": ".bar()", "file_type": "code",
         "source_file": "a.py", "source_location": "L5",
         "node_kind": "impl_method"},
    ]
    links = [{"source": "f", "target": "m", "relation": "method",
              "confidence": "EXTRACTED"}]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    # Walk 2 steps so cursor.history is non-empty under default ephemeral session.
    out = navigate(["@Foo", "methods", "[1]"], session=True, fmt="text",
                   show_ops_hint=True)
    assert "↺(" not in out, (
        f"history-depth widget should hide on ephemeral sessions:\n{out}"
    )
    assert "back | reset" not in out, (
        f"back/reset ops should hide on ephemeral sessions:\n{out}"
    )


def test_back_reset_widgets_visible_on_named_session(tmp_path, monkeypatch):
    """Counterpart: when --session <id> is explicitly passed, the agent
    has committed to a resumable session and back/reset are meaningful.
    The widgets should appear."""
    from graphify.navigate import navigate
    nodes = [
        {"id": "f", "label": "Foo", "file_type": "code",
         "source_file": "a.py", "source_location": "L1",
         "node_kind": "class"},
        {"id": "m", "label": ".bar()", "file_type": "code",
         "source_file": "a.py", "source_location": "L5",
         "node_kind": "impl_method"},
    ]
    links = [{"source": "f", "target": "m", "relation": "method",
              "confidence": "EXTRACTED"}]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    out = navigate(["@Foo", "methods", "[1]"], session="explicit_id",
                   fmt="text", show_ops_hint=True)
    assert "↺(" in out, (
        f"history widget should show on named session:\n{out}"
    )
    assert "back | reset" in out, (
        f"back/reset ops should show on named session:\n{out}"
    )


def test_show_session_renders_cursor_without_mutation(tmp_path, monkeypatch):
    """`--show-session <id>` reads the saved cursor, renders the frontier,
    and exits without persisting. The session file should be unchanged
    after the call (modulo nothing — peek is byte-for-byte read-only)."""
    from graphify.navigate import navigate
    nodes = [
        {"id": "f", "label": "Foo", "file_type": "code",
         "source_file": "a.py", "source_location": "L1",
         "node_kind": "class"},
        {"id": "m", "label": ".bar()", "file_type": "code",
         "source_file": "a.py", "source_location": "L5",
         "node_kind": "impl_method"},
    ]
    links = [{"source": "f", "target": "m", "relation": "method",
              "confidence": "EXTRACTED"}]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    # Create a session by walking 2 steps so it persists.
    navigate(["@Foo", "methods", "[1]"], session="peek1", fmt="text")
    cpath = tmp_path / "graphify-out" / ".navigate" / "peek1.json"
    assert cpath.exists(), "precondition: session file should exist"
    before = cpath.read_bytes()
    # Peek with --show-session: render cursor (which is on .bar() after the pick).
    out = navigate([], show_session="peek1", fmt="text")
    after = cpath.read_bytes()
    assert before == after, (
        f"--show-session must not mutate the cursor file"
    )
    assert "show-session: peek1" in out, f"missing peek header:\n{out}"
    assert ".bar()" in out, f"frontier should show current node .bar():\n{out}"


def test_show_session_missing_id_returns_error(tmp_path, monkeypatch):
    """Asking for a session id that doesn't exist returns a clean error,
    not an empty render."""
    from graphify.navigate import navigate
    nodes = [{"id": "n", "label": "x", "file_type": "code",
              "source_file": "a.py", "source_location": "L1"}]
    _write_graph(tmp_path / "graphify-out", nodes, [])
    monkeypatch.chdir(tmp_path)
    out = navigate([], show_session="does_not_exist", fmt="text")
    assert "error" in out and "does_not_exist" in out, (
        f"missing-session should produce an actionable error:\n{out}"
    )


def test_auto_widen_not_triggered_with_kinds_filter(tmp_path, monkeypatch):
    """When `--kind=...` is passed the user is filtering on purpose. An
    empty result + inferred-hidden under the kind filter should NOT
    auto-widen — the user's filter intent wins."""
    from graphify.navigate import navigate
    nodes = [
        {"id": "tgt", "label": "doStuff()", "file_type": "code",
         "source_file": "a.ts", "source_location": "L10"},
        {"id": "c1", "label": "caller1()", "file_type": "code",
         "source_file": "b.ts", "source_location": "L5"},
    ]
    links = [
        {"source": "c1", "target": "tgt", "relation": "calls",
         "confidence": "INFERRED", "confidence_score": 0.8},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    out = navigate(["@doStuff", "in"], session=False, fmt="text",
                   kinds={"uses"})
    assert "auto-widened" not in out, (
        f"--kind filter should suppress auto-widen:\n{out}"
    )


def test_include_inferred_silent_emits_no_inferred_to_add_frontier(tmp_path, monkeypatch):
    """Lap-20 field-report fix #5: when `--include-inferred` is on but no
    pivot has hidden inferred edges, the frontier render must say so. Without
    this confirmation the flag looks broken — output is byte-identical to
    the AST-only default."""
    from graphify.navigate import navigate
    nodes = [
        {"id": "tgt", "label": "f()", "file_type": "code",
         "source_file": "a.py", "source_location": "L10"},
        {"id": "c1", "label": "caller()", "file_type": "code",
         "source_file": "a.py", "source_location": "L1"},
    ]
    # Only EXTRACTED edges — no inferred edges to add.
    links = [
        {"source": "c1", "target": "tgt", "relation": "calls",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    out = navigate(["@f"], session=False, fmt="text", extracted_only=False)
    assert "no inferred edges to add" in out, (
        f"frontier should confirm --include-inferred is honored "
        f"when nothing extra applies:\n{out}"
    )


def test_include_inferred_silent_emits_no_inferred_to_add_listing(tmp_path, monkeypatch):
    """Lap-20 field-report fix #5: same symmetric confirmation on a non-empty
    listing with no inferred drops. Listing pivot output should mark
    `--include-inferred` as honored even when the rendered rows would have
    been the same under AST-only."""
    from graphify.navigate import navigate
    nodes = [
        {"id": "tgt", "label": "f()", "file_type": "code",
         "source_file": "a.py", "source_location": "L10"},
        {"id": "c1", "label": "caller()", "file_type": "code",
         "source_file": "a.py", "source_location": "L1"},
    ]
    links = [
        {"source": "c1", "target": "tgt", "relation": "calls",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    out = navigate(["@f", "in"], session=False, fmt="text", extracted_only=False)
    assert "no inferred edges to add" in out, (
        f"listing should confirm --include-inferred is honored when no "
        f"inferred drops exist:\n{out}"
    )


def test_include_inferred_default_off_silent_no_marker(tmp_path, monkeypatch):
    """Sanity: the symmetric confirmation must NOT fire when
    `--include-inferred` was NOT passed. Default extracted-only output
    stays unchanged."""
    from graphify.navigate import navigate
    nodes = [
        {"id": "tgt", "label": "f()", "file_type": "code",
         "source_file": "a.py", "source_location": "L10"},
        {"id": "c1", "label": "caller()", "file_type": "code",
         "source_file": "a.py", "source_location": "L1"},
    ]
    links = [
        {"source": "c1", "target": "tgt", "relation": "calls",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    out = navigate(["@f"], session=False, fmt="text")  # extracted_only=True default
    assert "no inferred edges to add" not in out, (
        f"confirmation must not appear under default extracted-only:\n{out}"
    )



def test_search_truncation_promotes_top_banner(tmp_path, monkeypatch):
    """Lap-20 field-report fix #6: when search truncation hides >50% of
    results, a top-of-output banner with the ⚠ glyph reframes the listing
    as truncated. Without this the trailing `+M more` is easy to miss when
    scanning a `8 hit(s)` header."""
    from graphify.navigate import search_bodies, _render_search_text
    src = tmp_path / "big.py"
    # 30 NEEDLE lines; we'll cap at 5 visible → 25 truncated → 25 > 5 (>50%)
    src.write_text("\n".join(f"x = NEEDLE  # line {i}" for i in range(30)) + "\n",
                   encoding="utf-8")
    nodes = [
        {"id": "f", "label": "big.py", "file_type": "code",
         "source_file": str(src), "source_location": "L1"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, [])
    monkeypatch.chdir(tmp_path)
    import networkx as nx
    G = nx.DiGraph()
    for n in nodes:
        G.add_node(n["id"], **{k: v for k, v in n.items() if k != "id"})
    data = search_bodies(G, "NEEDLE", limit=5)
    out = _render_search_text(data)
    assert "⚠" in out, f"banner glyph missing on heavy truncation:\n{out}"
    assert "showing 5 of 30" in out, (
        f"banner should name visible/grand-total ratio:\n{out}"
    )
    assert "narrow the regex" in out, (
        f"banner should suggest narrowing or raising --limit:\n{out}"
    )
    # Footer fallback still present (not exclusive).
    assert "+25 more" in out, (
        f"footer should remain as fallback:\n{out}"
    )


def test_search_truncation_mild_keeps_only_footer(tmp_path, monkeypatch):
    """Mild truncation (≤50% AND ≤100 hidden) should keep the
    existing footer-only behavior — no banner promotion."""
    from graphify.navigate import search_bodies, _render_search_text
    src = tmp_path / "mid.py"
    # 12 hits, limit 10 → 2 truncated → both thresholds (>50%, >100) miss.
    src.write_text("\n".join(f"x = NEEDLE  # line {i}" for i in range(12)) + "\n",
                   encoding="utf-8")
    nodes = [
        {"id": "f", "label": "mid.py", "file_type": "code",
         "source_file": str(src), "source_location": "L1"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, [])
    monkeypatch.chdir(tmp_path)
    import networkx as nx
    G = nx.DiGraph()
    for n in nodes:
        G.add_node(n["id"], **{k: v for k, v in n.items() if k != "id"})
    data = search_bodies(G, "NEEDLE", limit=10)
    out = _render_search_text(data)
    assert "⚠" not in out, f"banner should NOT fire on mild truncation:\n{out}"
    assert "+2 more" in out, f"footer should still surface:\n{out}"


def test_search_truncation_absolute_threshold_fires(tmp_path, monkeypatch):
    """Even when truncation hides <50%, an absolute hidden count >100 should
    promote the banner — large `+M more` numbers warrant the up-front signal
    even if the visible slice is the majority."""
    from graphify.navigate import search_bodies, _render_search_text
    src = tmp_path / "big.py"
    # 250 hits, limit 200 → 50 visible? No — limit applies to hits returned.
    # Need >100 truncated AND truncated <= total. So total=200, truncated=101
    # ⇒ grand_total=301, visible=200. truncated (101) is < total (200) but
    # >100 abs → banner fires.
    src.write_text("\n".join(f"x = NEEDLE  # line {i}" for i in range(301)) + "\n",
                   encoding="utf-8")
    nodes = [
        {"id": "f", "label": "big.py", "file_type": "code",
         "source_file": str(src), "source_location": "L1"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, [])
    monkeypatch.chdir(tmp_path)
    import networkx as nx
    G = nx.DiGraph()
    for n in nodes:
        G.add_node(n["id"], **{k: v for k, v in n.items() if k != "id"})
    data = search_bodies(G, "NEEDLE", limit=200)
    out = _render_search_text(data)
    assert "⚠" in out, (
        f"banner should fire on absolute hidden count >100:\n{out}"
    )
    assert "showing 200 of 301" in out, (
        f"banner should name visible/grand-total ratio:\n{out}"
    )



def test_path_all_imports_emits_calls_hint(tmp_path):
    """Lap-20 field-report fix #4: when `path` returns a chain entirely of
    `imports`/`imports_from` edges, append a `--edges calls` hint. The
    chain is graph-connected but reads misleadingly as a call path; the
    hint redirects without changing the default."""
    import json as _json, subprocess
    nodes = [
        {"id": "fA", "label": "A.ts", "file_type": "code",
         "source_file": "A.ts", "source_location": "L1"},
        {"id": "fB", "label": "B.ts", "file_type": "code",
         "source_file": "B.ts", "source_location": "L1"},
        {"id": "fC", "label": "C.ts", "file_type": "code",
         "source_file": "C.ts", "source_location": "L1"},
    ]
    # All-imports chain: A imports B imports C. Reach default keeps these.
    links = [
        {"source": "fA", "target": "fB", "relation": "imports",
         "confidence": "EXTRACTED"},
        {"source": "fB", "target": "fC", "relation": "imports_from",
         "confidence": "EXTRACTED"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False,
         "graph": {}, "nodes": nodes, "links": links}), encoding="utf-8")
    res = subprocess.run(
        ["graphify", "path", "A.ts", "C.ts",
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    out = res.stdout + res.stderr
    assert "Shortest path" in out, f"path should resolve:\n{out}"
    assert "--edges calls" in out, (
        f"all-imports path should hint at --edges calls:\n{out}"
    )
    assert "file-level imports" in out, (
        f"hint should name the cause:\n{out}"
    )


def test_path_mixed_relations_no_imports_hint(tmp_path):
    """Sanity: a path with at least one non-imports edge (e.g. a `contains`
    or `calls`) must NOT trigger the --edges-calls hint. The hint is
    specifically for the all-imports degenerate case."""
    import json as _json, subprocess
    nodes = [
        {"id": "fA", "label": "A.ts", "file_type": "code",
         "source_file": "A.ts", "source_location": "L1"},
        {"id": "fB", "label": "B.ts", "file_type": "code",
         "source_file": "B.ts", "source_location": "L1"},
        {"id": "sB", "label": "useB()", "file_type": "code",
         "source_file": "B.ts", "source_location": "L5"},
    ]
    # A imports B; B contains useB(). path A.ts → useB() routes
    # through `imports` then `contains`.
    links = [
        {"source": "fA", "target": "fB", "relation": "imports",
         "confidence": "EXTRACTED"},
        {"source": "fB", "target": "sB", "relation": "contains",
         "confidence": "EXTRACTED"},
    ]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False,
         "graph": {}, "nodes": nodes, "links": links}), encoding="utf-8")
    res = subprocess.run(
        ["graphify", "path", "A.ts", "useB()",
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    out = res.stdout + res.stderr
    assert "Shortest path" in out, f"path should resolve:\n{out}"
    assert "--edges calls" not in out or "current path is all" not in out, (
        f"mixed-relation path should NOT trigger imports-only hint:\n{out}"
    )


def test_load_graph_emits_stale_banner(tmp_path, capsys):
    """Lap-20d (TS-Claude #1, "highest leverage"): when any indexed
    source file is newer than graph.json, load_graph emits a stale
    banner to stderr. Prevents the silently-stale failure mode where
    a re-run looks like a no-op even when the user pulled new code."""
    import json as _json, os, time as _time
    from graphify.navigate import load_graph
    src_file = tmp_path / "src.py"
    src_file.write_text("def x(): pass\n", encoding="utf-8")
    nodes = [{"id": "x", "label": "x()", "file_type": "code",
              "source_file": str(src_file), "source_location": "L1"}]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    graph_file = graph_dir / "graph.json"
    graph_file.write_text(_json.dumps(
        {"directed": True, "multigraph": False,
         "graph": {}, "nodes": nodes, "links": []}), encoding="utf-8")
    # Make graph "old" by setting its mtime back, then touch source.
    old = _time.time() - 600  # 10 minutes ago
    os.utime(graph_file, (old, old))
    src_file.touch()  # newer than graph
    capsys.readouterr()  # clear pre-existing
    G, _ = load_graph(graph_file)
    captured = capsys.readouterr()
    assert "stale" in captured.err, (
        f"stale banner should appear on stderr, got: stderr={captured.err!r}"
    )
    assert G.graph.get("_freshness_banner"), "banner should be stamped on G"


def test_load_graph_no_banner_when_fresh(tmp_path, capsys):
    """Lap-20d: when no indexed source file is newer than graph.json,
    load_graph stays silent."""
    import json as _json, os, time as _time
    from graphify.navigate import load_graph
    src_file = tmp_path / "src.py"
    src_file.write_text("def x(): pass\n", encoding="utf-8")
    nodes = [{"id": "x", "label": "x()", "file_type": "code",
              "source_file": str(src_file), "source_location": "L1"}]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    graph_file = graph_dir / "graph.json"
    graph_file.write_text(_json.dumps(
        {"directed": True, "multigraph": False,
         "graph": {}, "nodes": nodes, "links": []}), encoding="utf-8")
    # Make graph clearly newer than any source.
    future = _time.time() + 60
    os.utime(graph_file, (future, future))
    capsys.readouterr()
    G, _ = load_graph(graph_file)
    captured = capsys.readouterr()
    assert "stale" not in captured.err, (
        f"fresh graph should not emit banner, got: stderr={captured.err!r}"
    )
    assert not G.graph.get("_freshness_banner")


def test_navigate_dominant_match_suppresses_loud_warning(tmp_path):
    """Lap-21 #3 (extended from lap-20d): when match_type=="prefix" with
    a single chosen, alternatives are fuzzy near-misses by construction.
    Suppress the `⚠ ambiguous:` glyph and the "also near" line. Lap-20d
    used a degree-dominance heuristic; lap-21 generalized to "prefix
    branch always suppresses" because alternatives there are never
    plausible same-kind matches."""
    import json as _json, subprocess
    nodes = [
        # Dominant target: prefix match with high degree.
        {"id": "main", "label": "analyzePanel()", "file_type": "code",
         "source_file": "src/main.ts", "source_location": "L1"},
        # Many neighbors of main → high degree.
        *[{"id": f"caller{i}", "label": f"caller{i}()",
           "file_type": "code",
           "source_file": "src/caller.ts",
           "source_location": f"L{i}"} for i in range(8)],
        # Fuzzy near-miss with low degree → should not trigger loud warning.
        {"id": "alt", "label": "analyzeFoo()", "file_type": "code",
         "source_file": "src/alt.ts", "source_location": "L1"},
    ]
    links = [{"source": f"caller{i}", "target": "main",
              "relation": "calls", "confidence": "EXTRACTED"}
             for i in range(8)]
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False,
         "graph": {}, "nodes": nodes, "links": links}), encoding="utf-8")
    res = subprocess.run(
        ["graphify", "navigate", "@analyzePanel",
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    out = res.stdout + res.stderr
    assert "analyzePanel()" in out, f"should match the dominant target:\n{out}"
    assert "⚠ ambiguous" not in out, (
        f"unique prefix match should not emit ⚠ ambiguous:\n{out}"
    )
    assert "also near:" not in out, (
        f"unique prefix match should suppress 'also near' line:\n{out}"
    )


def test_navigate_unique_prefix_suppresses_warning_at_similar_degree(tmp_path):
    """Lap-21 #3: TS-Claude reported `@buildDecodeEngine` (deg=46)
    emitting `⚠ ambiguous:` with "also near: DecodeEngine, buildOurEngine,
    DecodedEdge" — none of which start with `buildDecodeEngine`. The
    lap-20d degree-dominance heuristic missed this because DecodeEngine
    (a class) had comparable degree. Fix: in the prefix branch the
    resolver guarantees alternatives are non-prefix fuzzies, so always
    suppress regardless of degree."""
    import json as _json, subprocess
    nodes = [
        # Chosen: unique full-token prefix match.
        {"id": "build_fn", "label": "buildDecodeEngine()", "file_type": "code",
         "source_file": "src/build.ts", "source_location": "L1"},
        # Many neighbors → high degree to mirror the field report.
        *[{"id": f"caller{i}", "label": f"caller{i}()", "file_type": "code",
           "source_file": f"src/caller{i}.ts", "source_location": "L1"}
          for i in range(20)],
        # Alternative with similar (high) degree but NOT a prefix match.
        {"id": "decode_class", "label": "DecodeEngine", "file_type": "code",
         "source_file": "src/decode.ts", "source_location": "L1",
         "node_kind": "class"},
        *[{"id": f"engcaller{i}", "label": f"engcaller{i}()",
           "file_type": "code",
           "source_file": f"src/engcaller{i}.ts", "source_location": "L1"}
          for i in range(18)],
        # Other fuzzy near-misses.
        {"id": "build_our", "label": "buildOurEngine()", "file_type": "code",
         "source_file": "src/our.ts", "source_location": "L1"},
        {"id": "decoded_edge", "label": "DecodedEdge", "file_type": "code",
         "source_file": "src/edge.ts", "source_location": "L1",
         "node_kind": "class"},
    ]
    links = (
        [{"source": f"caller{i}", "target": "build_fn",
          "relation": "calls", "confidence": "EXTRACTED"}
         for i in range(20)]
        + [{"source": f"engcaller{i}", "target": "decode_class",
            "relation": "calls", "confidence": "EXTRACTED"}
           for i in range(18)]
    )
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(_json.dumps(
        {"directed": True, "multigraph": False,
         "graph": {}, "nodes": nodes, "links": links}), encoding="utf-8")
    res = subprocess.run(
        ["graphify", "navigate", "@buildDecodeEngine",
         "--graph", str(graph_dir / "graph.json")],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    out = res.stdout + res.stderr
    assert "buildDecodeEngine()" in out, f"should pick the prefix match:\n{out}"
    assert "⚠ ambiguous" not in out, (
        f"unique prefix should suppress warning even when alt has similar "
        f"degree:\n{out}"
    )
    assert "also near:" not in out, (
        f"unique prefix should suppress 'also near' even at similar "
        f"degree:\n{out}"
    )


def test_summarize_class_emits_sig_methods_callers_inheritance(tmp_path, monkeypatch):
    """Lap-23 (meta-harness task_004/005 cluster-B fix): `graphify summarize
    @<Class>` is the one-shot class summary — fuses class signature,
    method list, cross-file callers (used-by), and inheritance into a
    single call. Targets the bimodal failure mode where agents fall back
    to read_file because no graphify verb gave them class-level context."""
    import subprocess
    nodes = [
        {"id": "f1", "label": "lib.py", "file_type": "code",
         "source_file": "lib.py", "source_location": "L1",
         "node_kind": "file"},
        {"id": "f2", "label": "user.py", "file_type": "code",
         "source_file": "user.py", "source_location": "L1",
         "node_kind": "file"},
        {"id": "parent", "label": "BaseShape", "file_type": "code",
         "source_file": "lib.py", "source_location": "L1-9",
         "node_kind": "class"},
        {"id": "klass", "label": "KleinBottle", "file_type": "code",
         "source_file": "lib.py", "source_location": "L10-40",
         "node_kind": "class"},
        {"id": "sib", "label": "Torus", "file_type": "code",
         "source_file": "lib.py", "source_location": "L42-60",
         "node_kind": "class"},
        {"id": "child", "label": "WeirdKlein", "file_type": "code",
         "source_file": "lib.py", "source_location": "L62-70",
         "node_kind": "class"},
        {"id": "m1", "label": ".__init__()", "file_type": "code",
         "source_file": "lib.py", "source_location": "L11-14",
         "node_kind": "method"},
        {"id": "m2", "label": ".embed()", "file_type": "code",
         "source_file": "lib.py", "source_location": "L16-22",
         "node_kind": "method"},
        {"id": "m3", "label": ".compute_metrics()", "file_type": "code",
         "source_file": "lib.py", "source_location": "L24-38",
         "node_kind": "method"},
        {"id": "caller", "label": "make_klein()", "file_type": "code",
         "source_file": "user.py", "source_location": "L5-8",
         "node_kind": "function"},
    ]
    links = [
        # Structural: file contains classes; classes contain methods.
        {"source": "f1", "target": "parent", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "f1", "target": "klass", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "f1", "target": "sib", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "f1", "target": "child", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "f2", "target": "caller", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "klass", "target": "m1", "relation": "method",
         "confidence": "EXTRACTED"},
        {"source": "klass", "target": "m2", "relation": "method",
         "confidence": "EXTRACTED"},
        {"source": "klass", "target": "m3", "relation": "method",
         "confidence": "EXTRACTED"},
        # Inheritance: KleinBottle extends BaseShape; Torus too (sibling);
        # WeirdKlein extends KleinBottle (child).
        {"source": "klass", "target": "parent", "relation": "inherits",
         "confidence": "EXTRACTED"},
        {"source": "sib", "target": "parent", "relation": "inherits",
         "confidence": "EXTRACTED"},
        {"source": "child", "target": "klass", "relation": "inherits",
         "confidence": "EXTRACTED"},
        # Cross-file caller instantiates the class.
        {"source": "caller", "target": "klass", "relation": "calls",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    # Write actual source so the Signature section can read the class header.
    (tmp_path / "lib.py").write_text(
        "class BaseShape:\n    pass\n\n\n"  # L1-9 (with blank lines)
        "\n\n\n\n\n"
        "class KleinBottle(BaseShape):\n"  # L10
        "    def __init__(self):\n        self.x = 0\n        return\n\n"  # L11-14
        "    def embed(self):\n        pass\n        pass\n        pass\n"
        "        pass\n        pass\n        pass\n\n"  # L16-22
        "    def compute_metrics(self):\n        return {}\n",  # L24-38
        encoding="utf-8",
    )
    (tmp_path / "user.py").write_text(
        "from lib import KleinBottle\n\n\n\n\n"
        "def make_klein():\n    return KleinBottle()\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    res = subprocess.run(
        ["python", "-m", "graphify", "summarize", "KleinBottle"],
        capture_output=True, text=True, cwd=str(tmp_path), timeout=15,
    )
    assert res.returncode == 0, f"summarize failed: stderr={res.stderr}"
    out = res.stdout
    # Header announces class + counts + inheritance.
    assert "summarize class @KleinBottle" in out, (
        f"header missing class name:\n{out}"
    )
    assert "3 methods" in out, f"method count missing:\n{out}"
    assert "1 caller" in out, f"caller count missing:\n{out}"
    assert "BaseShape" in out, f"parent inheritance missing from header:\n{out}"
    assert "1 subclass" in out, f"child count missing:\n{out}"
    # Four labeled sections in source order.
    sig_idx = out.find("## Signature")
    methods_idx = out.find("## Methods")
    used_idx = out.find("## Used by")
    inh_idx = out.find("## Inheritance")
    assert 0 <= sig_idx < methods_idx < used_idx < inh_idx, (
        f"expected sections in order Signature, Methods, Used by, "
        f"Inheritance:\n{out}"
    )
    # Signature block contains the class declaration line from source.
    sig_block = out[sig_idx:methods_idx]
    assert "class KleinBottle" in sig_block, (
        f"Signature block should contain the class declaration line:\n{sig_block}"
    )
    # Methods listed in source order (start-line ascending).
    methods_block = out[methods_idx:used_idx]
    init_pos = methods_block.find("__init__")
    embed_pos = methods_block.find("embed")
    metrics_pos = methods_block.find("compute_metrics")
    assert 0 <= init_pos < embed_pos < metrics_pos, (
        f"methods should be sorted by source line:\n{methods_block}"
    )
    # Used-by names the cross-file caller with file:line.
    used_block = out[used_idx:inh_idx]
    assert "make_klein" in used_block and "user.py" in used_block, (
        f"used-by should name cross-file caller with location:\n{used_block}"
    )
    # Inheritance section names parent, sibling, and child.
    inh_block = out[inh_idx:]
    assert "BaseShape" in inh_block, f"parent missing in Inheritance:\n{inh_block}"
    assert "Torus" in inh_block, f"sibling missing:\n{inh_block}"
    assert "WeirdKlein" in inh_block, f"child missing:\n{inh_block}"


def test_summarize_class_handles_no_callers_no_inheritance(tmp_path, monkeypatch):
    """A standalone class with no cross-file callers and no inheritance
    should still emit the class header + methods, but skip the
    Inheritance section entirely (don't waste tokens on a 'no parents'
    line)."""
    import subprocess
    nodes = [
        {"id": "f", "label": "lib.py", "file_type": "code",
         "source_file": "lib.py", "source_location": "L1",
         "node_kind": "file"},
        {"id": "klass", "label": "Solo", "file_type": "code",
         "source_file": "lib.py", "source_location": "L5-15",
         "node_kind": "class"},
        {"id": "m", "label": ".do_thing()", "file_type": "code",
         "source_file": "lib.py", "source_location": "L7-12",
         "node_kind": "method"},
    ]
    links = [
        {"source": "f", "target": "klass", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "klass", "target": "m", "relation": "method",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    res = subprocess.run(
        ["python", "-m", "graphify", "summarize", "Solo"],
        capture_output=True, text=True, cwd=str(tmp_path), timeout=15,
    )
    assert res.returncode == 0, f"summarize failed: stderr={res.stderr}"
    out = res.stdout
    assert "summarize class @Solo" in out
    # Inheritance section should be omitted when there's nothing to say.
    assert "## Inheritance" not in out, (
        f"empty Inheritance section should be skipped to save tokens:\n{out}"
    )
    # Used-by emits "(none)" so the agent doesn't wonder if the section
    # was dropped.
    assert "## Used by" in out and "(none" in out, (
        f"Used by section should appear with (none) marker:\n{out}"
    )


def test_summarize_class_auto_picks_unique_non_archived(tmp_path, monkeypatch):
    """Lap-26 field-report fix: `summarize @SymplecticGeometry` matched 4
    nodes — 1 main + 3 in legacy/ subdir generations. Disambig is exactly
    when archived noise hurts. With `--no-archived` as default, the
    1-main / N-archived case auto-picks instead of forcing path-qualify."""
    import subprocess
    nodes = [
        {"id": "f1", "label": "lib.py", "file_type": "code",
         "source_file": "lib.py", "source_location": "L1",
         "node_kind": "file"},
        {"id": "f2", "label": "lib.py", "file_type": "code",
         "source_file": "legacy/v1/lib.py", "source_location": "L1",
         "node_kind": "file"},
        {"id": "f3", "label": "lib.py", "file_type": "code",
         "source_file": "legacy/v2/lib.py", "source_location": "L1",
         "node_kind": "file"},
        {"id": "main_cls", "label": "Worker", "file_type": "code",
         "source_file": "lib.py", "source_location": "L5-15",
         "node_kind": "class"},
        {"id": "leg_cls_1", "label": "Worker", "file_type": "code",
         "source_file": "legacy/v1/lib.py", "source_location": "L5-15",
         "node_kind": "class"},
        {"id": "leg_cls_2", "label": "Worker", "file_type": "code",
         "source_file": "legacy/v2/lib.py", "source_location": "L5-15",
         "node_kind": "class"},
        {"id": "main_m", "label": ".run()", "file_type": "code",
         "source_file": "lib.py", "source_location": "L7-12",
         "node_kind": "method"},
    ]
    links = [
        {"source": "f1", "target": "main_cls", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "main_cls", "target": "main_m", "relation": "method",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    res = subprocess.run(
        ["python", "-m", "graphify", "summarize", "Worker"],
        capture_output=True, text=True, cwd=str(tmp_path), timeout=15,
    )
    assert res.returncode == 0, (
        f"--no-archived should auto-pick when 1 non-archived candidate "
        f"remains: stderr={res.stderr}"
    )
    out = res.stdout
    assert "summarize class @Worker" in out, (
        f"should land on the main Worker:\n{out}"
    )
    assert "auto-picked" in out, (
        f"footer should name the auto-pick + archived hidden count:\n{out}"
    )
    assert "+2 archived hidden" in out, (
        f"hidden count must surface (omission counts rule):\n{out}"
    )


def test_summarize_class_all_archived_shows_disambig(tmp_path, monkeypatch):
    """`--all-archived` opts back into the disambig listing including the
    archived candidates — useful when the agent actually wants to inspect
    legacy code."""
    import subprocess
    nodes = [
        {"id": "main_cls", "label": "Worker", "file_type": "code",
         "source_file": "lib.py", "source_location": "L5-15",
         "node_kind": "class"},
        {"id": "leg_cls_1", "label": "Worker", "file_type": "code",
         "source_file": "legacy/v1/lib.py", "source_location": "L5-15",
         "node_kind": "class"},
        {"id": "leg_cls_2", "label": "Worker", "file_type": "code",
         "source_file": "legacy/v2/lib.py", "source_location": "L5-15",
         "node_kind": "class"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, [])
    monkeypatch.chdir(tmp_path)
    res = subprocess.run(
        ["python", "-m", "graphify", "summarize", "Worker", "--all-archived"],
        capture_output=True, text=True, cwd=str(tmp_path), timeout=15,
    )
    assert res.returncode == 1, "ambiguous with --all-archived should fail loud"
    err = res.stderr
    assert "ambiguous" in err
    assert "legacy/v1/lib.py" in err, (
        f"--all-archived should keep archived candidates visible:\n{err}"
    )


def test_summarize_rejects_function_target_with_redirect(tmp_path, monkeypatch):
    """Class summary is class-shaped; aiming `summarize` at a function
    should fail loudly and name the right verb (peek/blast) so the agent
    doesn't guess."""
    import subprocess
    nodes = [
        {"id": "f", "label": "lib.py", "file_type": "code",
         "source_file": "lib.py", "source_location": "L1",
         "node_kind": "file"},
        {"id": "fn", "label": "do_work()", "file_type": "code",
         "source_file": "lib.py", "source_location": "L5-10",
         "node_kind": "function"},
    ]
    links = [
        {"source": "f", "target": "fn", "relation": "contains",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    res = subprocess.run(
        ["python", "-m", "graphify", "summarize", "do_work"],
        capture_output=True, text=True, cwd=str(tmp_path), timeout=15,
    )
    assert res.returncode == 1, "should exit non-zero on wrong target shape"
    err = res.stderr
    assert "not a class" in err, f"error should name the shape mismatch:\n{err}"
    assert "peek" in err, f"error should redirect to peek:\n{err}"


def test_summarize_file_target_falls_through_to_shape(tmp_path, monkeypatch):
    """Lap-24: `graphify summarize <file>` falls through to `shape <file>`
    instead of erroring out. Empirical case: a session-benchmark agent
    ran `summarize <file>`, got "Try graphify shape" redirect, then
    re-ran shape — one wasted call. Agent intent is clear ("summarize
    this file"); honor it. Header note tells the agent the redirect
    happened so they can go straight to shape next time."""
    import subprocess
    nodes = [
        {"id": "f", "label": "lib.py", "file_type": "code",
         "source_file": "lib.py", "source_location": "L1",
         "node_kind": "file"},
        {"id": "fn", "label": "do_work()", "file_type": "code",
         "source_file": "lib.py", "source_location": "L5-10",
         "node_kind": "function"},
    ]
    links = [
        {"source": "f", "target": "fn", "relation": "contains",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    res = subprocess.run(
        ["python", "-m", "graphify", "summarize", "lib.py"],
        capture_output=True, text=True, cwd=str(tmp_path), timeout=15,
    )
    assert res.returncode == 0, (
        f"file target should succeed via shape fall-through: stderr={res.stderr}"
    )
    out = res.stdout
    assert "redirecting to `graphify shape`" in out, (
        f"redirect note should fire so agent learns the right verb:\n{out}"
    )
    # Shape output is rendered (not an error message).
    assert "shape @lib.py" in out, (
        f"file fall-through should emit shape output:\n{out}"
    )
    assert "do_work()" in out, (
        f"shape output should list the file's fns:\n{out}"
    )


def test_summarize_no_target_keeps_repo_overview(tmp_path, monkeypatch):
    """Regression check: bare `graphify summarize` still emits the
    repo-wide architectural overview (top communities, edge mix, etc.).
    Lap-23 added @<Class> mode but must not break the no-arg path."""
    import subprocess
    nodes = [
        {"id": "f", "label": "lib.py", "file_type": "code",
         "source_file": "lib.py", "source_location": "L1",
         "node_kind": "file"},
        {"id": "fn", "label": "do_work()", "file_type": "code",
         "source_file": "lib.py", "source_location": "L5-10",
         "node_kind": "function"},
    ]
    links = [
        {"source": "f", "target": "fn", "relation": "contains",
         "confidence": "EXTRACTED"},
    ]
    _write_graph(tmp_path / "graphify-out", nodes, links)
    monkeypatch.chdir(tmp_path)
    res = subprocess.run(
        ["python", "-m", "graphify", "summarize"],
        capture_output=True, text=True, cwd=str(tmp_path), timeout=15,
    )
    assert res.returncode == 0, f"summarize (no arg) failed: stderr={res.stderr}"
    out = res.stdout
    # Markers from the existing repo-overview path.
    assert "graphify summarize:" in out, (
        f"no-arg summarize should emit repo overview header:\n{out}"
    )
    assert "nodes" in out and "edges" in out and "communities" in out, (
        f"no-arg summarize should emit top-line stats:\n{out}"
    )
