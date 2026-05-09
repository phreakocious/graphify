from pathlib import Path
from graphify.extract import extract_python, extract, collect_files, _make_id

FIXTURES = Path(__file__).parent / "fixtures"


def test_make_id_strips_dots_and_underscores():
    assert _make_id("_auth") == "auth"
    assert _make_id(".httpx._client") == "httpx_client"


def test_make_id_consistent():
    """Same input always produces same output."""
    assert _make_id("foo", "Bar") == _make_id("foo", "Bar")


def test_make_id_no_leading_trailing_underscores():
    result = _make_id("__init__")
    assert not result.startswith("_")
    assert not result.endswith("_")


def test_extract_python_finds_class():
    result = extract_python(FIXTURES / "sample.py")
    labels = [n["label"] for n in result["nodes"]]
    assert "Transformer" in labels


def test_extract_python_finds_methods():
    result = extract_python(FIXTURES / "sample.py")
    labels = [n["label"] for n in result["nodes"]]
    assert any("__init__" in l or "forward" in l for l in labels)


def test_extract_python_no_dangling_edges():
    """All edge sources must reference a known node (targets may be external imports)."""
    result = extract_python(FIXTURES / "sample.py")
    node_ids = {n["id"] for n in result["nodes"]}
    for edge in result["edges"]:
        assert edge["source"] in node_ids, f"Dangling source: {edge['source']}"


def test_structural_edges_are_extracted():
    """contains / method / inherits / imports edges must always be EXTRACTED."""
    result = extract_python(FIXTURES / "sample.py")
    structural = {"contains", "method", "inherits", "imports", "imports_from"}
    for edge in result["edges"]:
        if edge["relation"] in structural:
            assert edge["confidence"] == "EXTRACTED", f"Expected EXTRACTED: {edge}"


def test_extract_merges_multiple_files():
    files = list(FIXTURES.glob("*.py"))
    result = extract(files)
    assert len(result["nodes"]) > 0
    assert result["input_tokens"] == 0


def test_collect_files_from_dir():
    from graphify.extract import _DISPATCH
    files = collect_files(FIXTURES)
    supported = set(_DISPATCH.keys())
    assert all(f.suffix in supported for f in files)
    assert len(files) > 0


def test_collect_files_skips_hidden():
    files = collect_files(FIXTURES)
    for f in files:
        assert not any(part.startswith(".") for part in f.parts)


def test_collect_files_follows_symlinked_directory(tmp_path):
    real_dir = tmp_path / "real_src"
    real_dir.mkdir()
    (real_dir / "lib.py").write_text("x = 1")
    (tmp_path / "linked_src").symlink_to(real_dir)

    files_no = collect_files(tmp_path, follow_symlinks=False)
    files_yes = collect_files(tmp_path, follow_symlinks=True)

    assert [f.name for f in files_no].count("lib.py") == 1
    assert [f.name for f in files_yes].count("lib.py") == 2


def test_collect_files_handles_circular_symlinks(tmp_path):
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "mod.py").write_text("x = 1")
    (sub / "cycle").symlink_to(tmp_path)

    files = collect_files(tmp_path, follow_symlinks=True)
    assert any(f.name == "mod.py" for f in files)


def test_collect_files_skips_build_artifacts(tmp_path):
    """build/ dist/ node_modules/ etc. are derived dirs, not source. They
    must not appear in the graph — without this, `pip install -e .`
    populates build/lib/<pkg>/*.py and every backtick reference in docs
    matches both real and vendored copies, polluting `references` edges
    as multi-match.
    """
    src = tmp_path / "real.py"
    src.write_text("class Real: pass\n")
    # Common Python build artefact location.
    (tmp_path / "build" / "lib" / "pkg").mkdir(parents=True)
    (tmp_path / "build" / "lib" / "pkg" / "real.py").write_text("class Real: pass\n")
    # Common JS dep dir.
    (tmp_path / "node_modules" / "lib").mkdir(parents=True)
    (tmp_path / "node_modules" / "lib" / "index.js").write_text("export const x = 1;\n")
    # __pycache__ should also be skipped.
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "real.cpython-312.pyc").write_text("")

    files = collect_files(tmp_path)
    rels = {str(f.relative_to(tmp_path)) for f in files}
    assert "real.py" in rels, f"top-level real.py should be present: {rels}"
    assert not any(p.startswith("build/") for p in rels), f"build/ leaked: {rels}"
    assert not any(p.startswith("node_modules/") for p in rels), f"node_modules/ leaked: {rels}"
    assert not any("__pycache__" in p for p in rels), f"__pycache__/ leaked: {rels}"


def test_no_dangling_edges_on_extract():
    """After merging multiple files, no internal edges should be dangling."""
    files = list(FIXTURES.glob("*.py"))
    result = extract(files)
    node_ids = {n["id"] for n in result["nodes"]}
    internal_relations = {"contains", "method", "inherits", "calls"}
    for edge in result["edges"]:
        if edge["relation"] in internal_relations:
            assert edge["source"] in node_ids, f"Dangling source: {edge}"
            assert edge["target"] in node_ids, f"Dangling target: {edge}"


def test_calls_edges_emitted():
    """Call-graph pass must produce INFERRED calls edges."""
    result = extract_python(FIXTURES / "sample_calls.py")
    calls = [e for e in result["edges"] if e["relation"] == "calls"]
    assert len(calls) > 0, "Expected at least one calls edge"


def test_calls_edges_are_extracted():
    """AST-resolved call edges are deterministic and should be EXTRACTED/1.0."""
    result = extract_python(FIXTURES / "sample_calls.py")
    for edge in result["edges"]:
        if edge["relation"] == "calls":
            assert edge["confidence"] == "EXTRACTED"
            assert edge["weight"] == 1.0


def test_calls_no_self_loops():
    result = extract_python(FIXTURES / "sample_calls.py")
    for edge in result["edges"]:
        if edge["relation"] == "calls":
            assert edge["source"] != edge["target"], f"Self-loop: {edge}"


def test_run_analysis_calls_compute_score():
    """run_analysis() calls compute_score() - must appear as a calls edge."""
    result = extract_python(FIXTURES / "sample_calls.py")
    calls = {(e["source"], e["target"]) for e in result["edges"] if e["relation"] == "calls"}
    node_by_label = {n["label"]: n["id"] for n in result["nodes"]}
    src = node_by_label.get("run_analysis()")
    tgt = node_by_label.get("compute_score()")
    assert src and tgt, "run_analysis or compute_score node not found"
    assert (src, tgt) in calls, f"run_analysis -> compute_score not found in {calls}"


def test_run_analysis_calls_normalize():
    result = extract_python(FIXTURES / "sample_calls.py")
    calls = {(e["source"], e["target"]) for e in result["edges"] if e["relation"] == "calls"}
    node_by_label = {n["label"]: n["id"] for n in result["nodes"]}
    src = node_by_label.get("run_analysis()")
    tgt = node_by_label.get("normalize()")
    assert src and tgt
    assert (src, tgt) in calls


def test_method_calls_module_function():
    """Analyzer.process() calls run_analysis() - cross class→function calls edge."""
    result = extract_python(FIXTURES / "sample_calls.py")
    calls = {(e["source"], e["target"]) for e in result["edges"] if e["relation"] == "calls"}
    node_by_label = {n["label"]: n["id"] for n in result["nodes"]}
    src = node_by_label.get(".process()")
    tgt = node_by_label.get("run_analysis()")
    assert src and tgt
    assert (src, tgt) in calls


def test_calls_deduplication():
    """Same caller→callee pair must appear only once even if called multiple times."""
    result = extract_python(FIXTURES / "sample_calls.py")
    call_pairs = [(e["source"], e["target"]) for e in result["edges"] if e["relation"] == "calls"]
    assert len(call_pairs) == len(set(call_pairs)), "Duplicate calls edges found"


def test_resolve_phantom_nodes_redirects_to_real_class():
    """A phantom node (empty source coords) emitted by the inheritance
    walker for a cross-file base should be replaced by edges pointing
    at the real class node when there's a unique label match. Lap-15
    consumer-Claude friction: `?` source_location in disambig listings."""
    from graphify.extract import _resolve_phantom_nodes
    nodes = [
        {"id": "foo_base", "label": "Base", "file_type": "code",
         "source_file": "foo.py", "source_location": "L1"},
        # Phantom from bar.py's class Sub(Base): inheritance
        {"id": "base", "label": "Base", "file_type": "code",
         "source_file": "", "source_location": ""},
        {"id": "bar_sub", "label": "Sub", "file_type": "code",
         "source_file": "bar.py", "source_location": "L1"},
    ]
    edges = [
        {"source": "bar_sub", "target": "base", "relation": "inherits",
         "confidence": "EXTRACTED"},
    ]
    new_nodes, new_edges = _resolve_phantom_nodes(nodes, edges)
    # Phantom dropped
    assert "base" not in {n["id"] for n in new_nodes}
    # Edge rewritten to real class
    assert any(e["source"] == "bar_sub" and e["target"] == "foo_base"
               and e["relation"] == "inherits" for e in new_edges), new_edges


def test_resolve_phantom_nodes_keeps_ambiguous():
    """When MULTIPLE real candidates have the same label, leave the
    phantom alone — silently picking one would propagate a bug into
    inheritance edges."""
    from graphify.extract import _resolve_phantom_nodes
    nodes = [
        {"id": "a_x", "label": "X", "file_type": "code",
         "source_file": "a.py", "source_location": "L1"},
        {"id": "b_x", "label": "X", "file_type": "code",
         "source_file": "b.py", "source_location": "L1"},
        {"id": "x", "label": "X", "file_type": "code",
         "source_file": "", "source_location": ""},
        {"id": "c_y", "label": "Y", "file_type": "code",
         "source_file": "c.py", "source_location": "L1"},
    ]
    edges = [
        {"source": "c_y", "target": "x", "relation": "inherits",
         "confidence": "EXTRACTED"},
    ]
    new_nodes, new_edges = _resolve_phantom_nodes(nodes, edges)
    # Phantom kept (ambiguous)
    assert "x" in {n["id"] for n in new_nodes}
    # Edge unchanged
    assert any(e["source"] == "c_y" and e["target"] == "x"
               for e in new_edges), new_edges


def test_resolve_phantom_nodes_drops_redirected_self_loop():
    """If redirect collides with the source itself, drop the resulting
    self-loop edge — never meaningful in inheritance graphs."""
    from graphify.extract import _resolve_phantom_nodes
    nodes = [
        {"id": "real_x", "label": "X", "file_type": "code",
         "source_file": "x.py", "source_location": "L1"},
        {"id": "phantom_x", "label": "X", "file_type": "code",
         "source_file": "", "source_location": ""},
    ]
    edges = [
        {"source": "real_x", "target": "phantom_x", "relation": "inherits",
         "confidence": "EXTRACTED"},
    ]
    new_nodes, new_edges = _resolve_phantom_nodes(nodes, edges)
    assert "phantom_x" not in {n["id"] for n in new_nodes}
    assert new_edges == [], f"self-loop should be dropped, got {new_edges}"


def test_resolve_phantom_nodes_preserves_unrelated_edges():
    """Regression: when a phantom exists, all NON-phantom edges must
    survive the rewrite. Earlier dedup logic seeded the existing-pairs
    set with every real edge upfront, then skipped any edge already in
    the set during the rewrite loop — silently dropping every contains/
    method/calls edge whenever any phantom triggered the pass."""
    from graphify.extract import _resolve_phantom_nodes
    nodes = [
        {"id": "file_foo", "label": "foo.py", "file_type": "code",
         "source_file": "foo.py", "source_location": "L1"},
        {"id": "foo_helper", "label": "helper()", "file_type": "code",
         "source_file": "foo.py", "source_location": "L10"},
        {"id": "foo_runner", "label": "runner()", "file_type": "code",
         "source_file": "foo.py", "source_location": "L20"},
        {"id": "foo_base", "label": "Base", "file_type": "code",
         "source_file": "foo.py", "source_location": "L30"},
        # Phantom from another file's `class Sub(Base):` — triggers the pass
        {"id": "base", "label": "Base", "file_type": "code",
         "source_file": "", "source_location": ""},
        {"id": "bar_sub", "label": "Sub", "file_type": "code",
         "source_file": "bar.py", "source_location": "L1"},
    ]
    edges = [
        {"source": "file_foo", "target": "foo_helper", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "file_foo", "target": "foo_runner", "relation": "contains",
         "confidence": "EXTRACTED"},
        {"source": "foo_runner", "target": "foo_helper", "relation": "calls",
         "confidence": "EXTRACTED"},
        {"source": "bar_sub", "target": "base", "relation": "inherits",
         "confidence": "EXTRACTED"},
    ]
    new_nodes, new_edges = _resolve_phantom_nodes(nodes, edges)
    pairs = {(e["source"], e["target"], e["relation"]) for e in new_edges}
    assert ("file_foo", "foo_helper", "contains") in pairs, pairs
    assert ("file_foo", "foo_runner", "contains") in pairs, pairs
    assert ("foo_runner", "foo_helper", "calls") in pairs, pairs
    assert ("bar_sub", "foo_base", "inherits") in pairs, pairs
    assert "base" not in {n["id"] for n in new_nodes}


# ── Lap-27: CLI-script indicator detection ────────────────────────────────────
# Investigation-style scripts (the EGF *_diagnostic.py / phase_coherence_*.py
# pattern) have zero external in-edges, so shape's entry-points line says
# "no entry points" exactly when the agent most needs orientation. The
# extractor stamps `script` / `script_kind` / `script_entries` on the file
# node so navigate / shape can land the agent on the runnable entry instead.


def _file_node(result):
    """First node returned is always the file node (extract_python invariant)."""
    return result["nodes"][0]


def test_script_main_block_detected(tmp_path):
    """Canonical `if __name__ == "__main__":` block — strongest indicator."""
    p = tmp_path / "tool.py"
    p.write_text(
        "def helper():\n"
        "    pass\n"
        "def main():\n"
        "    helper()\n"
        "if __name__ == \"__main__\":\n"
        "    main()\n"
    )
    fn = _file_node(extract_python(p))
    assert fn.get("script") is True
    assert fn.get("script_kind") == "main_block"
    # Entry should be the call inside __main__ (line 6), not the if itself.
    assert fn.get("script_entries") == [6], fn.get("script_entries")


def test_script_top_level_call_detected(tmp_path):
    """EGF investigation style — bare top-level call to a function defined
    locally is the runnable entry. Must fire even without a __main__ block."""
    p = tmp_path / "investigation.py"
    p.write_text(
        "def analyze(data): return data\n"
        "def report(x): print(x)\n"
        "result = analyze([1,2,3])\n"
        "report(result)\n"
    )
    fn = _file_node(extract_python(p))
    assert fn.get("script") is True
    assert fn.get("script_kind") == "top_level"
    # Both lines qualify (assignment-with-own-RHS + bare call).
    assert fn.get("script_entries") == [3, 4], fn.get("script_entries")


def test_script_top_level_for_loop_detected(tmp_path):
    """Top-level for/while are script-y irrespective of whether the body
    calls own fns — module-level loops only show up in scripts/notebooks."""
    p = tmp_path / "loop_script.py"
    p.write_text(
        "def step(i): print(i)\n"
        "for i in range(10):\n"
        "    step(i)\n"
    )
    fn = _file_node(extract_python(p))
    assert fn.get("script_kind") == "top_level"
    assert fn.get("script_entries") == [2]


def test_script_shebang_only_when_no_other_signal(tmp_path):
    """Shebang is the weakest indicator — fires only when neither
    main_block nor top_level applies. Catches wrapper scripts that
    just delegate to an imported `main`."""
    p = tmp_path / "wrapper.py"
    p.write_text(
        "#!/usr/bin/env python\n"
        "from foo import main\n"
        "main()\n"
    )
    fn = _file_node(extract_python(p))
    assert fn.get("script_kind") == "shebang"
    assert fn.get("script_entries") == [1]


def test_library_file_no_script_signal(tmp_path):
    """Pure library (only defs and class — no top-level work) must NOT
    be tagged as a script. Most files in any codebase fall here, so a
    false positive would be very loud."""
    p = tmp_path / "lib.py"
    p.write_text(
        "def public_fn(): return 42\n"
        "class Helper:\n"
        "    def do(self): return public_fn()\n"
    )
    fn = _file_node(extract_python(p))
    assert fn.get("script") in (False, None)
    assert fn.get("script_kind") is None


def test_library_with_top_level_imported_call_no_false_positive(tmp_path):
    """Library files commonly run config setup at module level
    (`logger.setLevel(...)`, `pd.set_option(...)`). These are member
    expressions on imported names — NOT bare calls to own fns. The
    detector requires a bare-identifier callee in `own_fn_names` to
    fire, so this case stays clean."""
    p = tmp_path / "config_lib.py"
    p.write_text(
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        "logger.setLevel(logging.INFO)\n"
        "def public_fn(): return 42\n"
    )
    fn = _file_node(extract_python(p))
    assert fn.get("script") in (False, None), (
        f"library w/ logger config tripped script flag: {fn}"
    )


def test_script_main_block_priority_over_top_level(tmp_path):
    """When both signals are present, main_block wins — the `if __name__`
    block is the canonical entry, and top-level helpers are typically
    test scaffolding the script wraps in `__main__` for cleanliness."""
    p = tmp_path / "both.py"
    p.write_text(
        "def setup(): pass\n"
        "def main(): setup()\n"
        "setup()\n"
        "if __name__ == \"__main__\":\n"
        "    main()\n"
    )
    fn = _file_node(extract_python(p))
    assert fn.get("script_kind") == "main_block"


def test_script_js_require_main_detected(tmp_path):
    """JS canonical `if (require.main === module)`."""
    from graphify.extract import extract_js
    p = tmp_path / "tool.js"
    p.write_text(
        "function main() { console.log('hi'); }\n"
        "if (require.main === module) {\n"
        "    main();\n"
        "}\n"
    )
    fn = _file_node(extract_js(p))
    assert fn.get("script_kind") == "main_block"
    assert fn.get("script_entries") == [3]


def test_script_ts_import_meta_main_detected(tmp_path):
    """Modern Deno/Bun style — `if (import.meta.main)`."""
    from graphify.extract import extract_js
    p = tmp_path / "tool.ts"
    p.write_text(
        "function main(): void { console.log('hi'); }\n"
        "if (import.meta.main) {\n"
        "    main();\n"
        "}\n"
    )
    fn = _file_node(extract_js(p))
    assert fn.get("script_kind") == "main_block"


def test_script_js_top_level_assignment_to_own(tmp_path):
    """JS assignment whose RHS calls a function defined in the file —
    same EGF investigation pattern, JS flavor. Detector walks any
    non-boring top-level statement for own-fn calls in the subtree."""
    from graphify.extract import extract_js
    p = tmp_path / "investigation.js"
    p.write_text(
        "#!/usr/bin/env node\n"
        "function analyze(d) { return d.x; }\n"
        "function report(d) { console.log(d); }\n"
        "const data = { x: 1 };\n"
        "const result = analyze(data);\n"
        "report(result);\n"
    )
    fn = _file_node(extract_js(p))
    assert fn.get("script_kind") == "top_level"
    # Lines 5, 6: `const result = analyze(data);` and `report(result);`.
    assert 5 in (fn.get("script_entries") or [])
    assert 6 in (fn.get("script_entries") or [])


def test_script_metadata_survives_to_file_node(tmp_path):
    """Sanity: the stamp lives on the file node specifically (not
    methods/classes), and extract.py never stamps it on inner nodes —
    methods get script_kind=None even if their owning file is a script."""
    p = tmp_path / "tool.py"
    p.write_text(
        "def main(): pass\n"
        "if __name__ == \"__main__\":\n"
        "    main()\n"
    )
    result = extract_python(p)
    file_node = result["nodes"][0]
    assert file_node.get("script_kind") == "main_block"
    # All other nodes must NOT carry script metadata.
    for n in result["nodes"][1:]:
        assert "script_kind" not in n, (
            f"Inner node leaked script metadata: {n}"
        )


# ── TSX (JSX-aware) parsing ──────────────────────────────────────────────────
# .tsx files require tree-sitter-typescript's `language_tsx`, not the plain
# `language_typescript` grammar. Parsing JSX with the wrong grammar produces
# silent ERROR nodes and drops every function/call inside JSX trees.

def test_extract_tsx_finds_helpers_and_component():
    """Functions defined alongside a JSX-returning component must be captured."""
    from graphify.extract import extract_js
    result = extract_js(FIXTURES / "sample.tsx")
    labels = [n["label"] for n in result["nodes"]]
    assert any("fmtDate" in l for l in labels), f"fmtDate missing from {labels}"
    assert any("fmtCount" in l for l in labels), f"fmtCount missing from {labels}"
    assert any("App" in l for l in labels), f"App missing from {labels}"


def test_extract_tsx_jsx_expression_calls_resolve():
    """Calls inside JSX expressions like `{fmtDate(now)}` must yield call edges.

    Regression guard for the TSX language fix: with `language_typescript`,
    JSX is parsed as ERROR nodes and these call_expressions disappear.
    """
    from graphify.extract import extract_js
    result = extract_js(FIXTURES / "sample.tsx")
    nodes_by_id = {n["id"]: n for n in result["nodes"]}
    call_targets = {
        nodes_by_id[e["target"]]["label"]
        for e in result["edges"]
        if e["relation"] == "calls" and e["target"] in nodes_by_id
    }
    assert "fmtDate()" in call_targets, (
        f"JSX expression call to fmtDate() not captured. Targets: {call_targets}"
    )
    assert "fmtCount()" in call_targets, (
        f"JSX expression call to fmtCount() not captured. Targets: {call_targets}"
    )


def test_extract_tsx_uses_tsx_grammar():
    """Wiring check: the .tsx config must use tree-sitter's `language_tsx`."""
    from graphify.extract import _TSX_CONFIG, _TS_CONFIG
    assert _TSX_CONFIG.ts_language_fn == "language_tsx"
    assert _TS_CONFIG.ts_language_fn == "language_typescript"


# ── CommonJS require() imports ───────────────────────────────────────────────

def test_extract_js_destructured_require_imports_from():
    """`const { foo } = require('./mod')` must emit imports_from to the resolved module path."""
    from graphify.extract import extract_js
    result = extract_js(FIXTURES / "cjs_require.js")
    imports_from = [e for e in result["edges"] if e["relation"] == "imports_from"]
    targets = [e["target"] for e in imports_from]
    assert any("foundation" in t for t in targets), f"No foundation import_from: {targets}"
    assert any("utils" in t for t in targets), f"No utils import_from: {targets}"
    assert any("helpers" in t for t in targets), f"No helpers import_from: {targets}"
    for e in imports_from:
        assert e["confidence"] == "EXTRACTED"


def test_extract_js_destructured_require_named_symbols():
    """Destructured CJS requires must emit symbol-level `imports` edges per binder."""
    from graphify.extract import extract_js, _make_id, _file_stem
    result = extract_js(FIXTURES / "cjs_require.js")
    sym_targets = [e["target"] for e in result["edges"] if e["relation"] == "imports"]
    foundation_stem = _file_stem(FIXTURES / "foundation.js")
    assert _make_id(foundation_stem, "loadFoundation") in sym_targets
    assert _make_id(foundation_stem, "validateConfig") in sym_targets


def test_extract_js_member_require_emits_property_symbol():
    """`const x = require('./m').y` must emit symbol edge for `y`."""
    from graphify.extract import extract_js, _make_id, _file_stem
    result = extract_js(FIXTURES / "cjs_require.js")
    sym_targets = [e["target"] for e in result["edges"] if e["relation"] == "imports"]
    helpers_stem = _file_stem(FIXTURES / "helpers.js")
    assert _make_id(helpers_stem, "helperFn") in sym_targets


def test_extract_js_arrow_function_still_extracted(tmp_path):
    """Regression: arrow functions in lexical_declaration must still produce nodes
    after the require() handler was added to _js_extra_walk."""
    from graphify.extract import extract_js
    p = tmp_path / "arrow_only.js"
    p.write_text("const greet = () => console.log('hi');\n")
    result = extract_js(p)
    labels = [n["label"] for n in result["nodes"]]
    assert "greet()" in labels


# ── Cross-file call import-evidence promotion ─────────────────────────────────

def test_cross_file_call_promoted_to_extracted_with_import_evidence(tmp_path):
    """When the caller's file has an `imports` edge to the callee symbol,
    the cross-file `calls` edge must be EXTRACTED with confidence_score 1.0."""
    caller = tmp_path / "caller.js"
    callee = tmp_path / "lib.js"
    caller.write_text(
        "const { doWork } = require('./lib');\n"
        "function run() { doWork(); }\n"
    )
    callee.write_text(
        "function doWork() { return 1; }\n"
        "module.exports = { doWork };\n"
    )
    result = extract([caller, callee], cache_root=tmp_path)
    nodes = {n["id"]: n for n in result["nodes"]}
    call_edges = [
        e for e in result["edges"]
        if e["relation"] == "calls"
        and nodes[e["source"]]["label"] == "run()"
        and nodes[e["target"]]["label"] == "doWork()"
    ]
    assert len(call_edges) == 1
    assert call_edges[0]["confidence"] == "EXTRACTED"
    assert call_edges[0]["confidence_score"] == 1.0


def test_multi_candidate_disambiguated_by_import_evidence(tmp_path):
    """When two files both define `doWork`, but the caller imports from
    only one, the resolver must pick that one as EXTRACTED — not last-wins
    INFERRED. This is the navigator-specific multi-candidate path that
    upstream's single-branch resolver doesn't exercise."""
    caller = tmp_path / "caller.js"
    lib_a = tmp_path / "libA.js"
    lib_b = tmp_path / "libB.js"
    caller.write_text(
        "const { doWork } = require('./libA');\n"
        "function run() { doWork(); }\n"
    )
    lib_a.write_text(
        "function doWork() { return 'A'; }\n"
        "module.exports = { doWork };\n"
    )
    lib_b.write_text(
        "function doWork() { return 'B'; }\n"
        "module.exports = { doWork };\n"
    )
    result = extract([caller, lib_a, lib_b], cache_root=tmp_path)
    nodes = {n["id"]: n for n in result["nodes"]}
    call_edges = [
        e for e in result["edges"]
        if e["relation"] == "calls"
        and nodes[e["source"]]["label"] == "run()"
    ]
    assert len(call_edges) == 1, f"Expected 1 call edge, got: {call_edges}"
    edge = call_edges[0]
    assert edge["confidence"] == "EXTRACTED"
    # Picked candidate must be libA's doWork, not libB's
    assert nodes[edge["target"]]["source_file"].endswith("libA.js")
