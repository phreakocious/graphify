"""Tests for multi-language AST extraction: JS/TS, Go, Rust."""
from __future__ import annotations
import shutil
from pathlib import Path
import pytest
from graphify.extract import extract_js, extract_go, extract_rust, extract

FIXTURES = Path(__file__).parent / "fixtures"


# ── helpers ──────────────────────────────────────────────────────────────────

def _labels(result):
    return [n["label"] for n in result["nodes"]]

def _call_pairs(result):
    node_by_id = {n["id"]: n["label"] for n in result["nodes"]}
    return {
        (node_by_id.get(e["source"], e["source"]), node_by_id.get(e["target"], e["target"]))
        for e in result["edges"] if e["relation"] == "calls"
    }

def _confidences(result):
    return {e["confidence"] for e in result["edges"]}


# ── TypeScript ────────────────────────────────────────────────────────────────

def test_ts_finds_class():
    r = extract_js(FIXTURES / "sample.ts")
    assert "error" not in r
    assert "HttpClient" in _labels(r)

def test_ts_finds_methods():
    r = extract_js(FIXTURES / "sample.ts")
    labels = _labels(r)
    assert any("get" in l for l in labels)
    assert any("post" in l for l in labels)

def test_ts_finds_function():
    r = extract_js(FIXTURES / "sample.ts")
    assert any("buildHeaders" in l for l in _labels(r))

def test_ts_emits_calls():
    r = extract_js(FIXTURES / "sample.ts")
    calls = _call_pairs(r)
    # .post() calls .get()
    assert any("post" in src and "get" in tgt for src, tgt in calls)

def test_ts_calls_are_extracted():
    r = extract_js(FIXTURES / "sample.ts")
    for e in r["edges"]:
        if e["relation"] == "calls":
            assert e["confidence"] == "EXTRACTED"

def test_ts_no_dangling_edges():
    r = extract_js(FIXTURES / "sample.ts")
    node_ids = {n["id"] for n in r["nodes"]}
    for e in r["edges"]:
        if e["relation"] in ("contains", "method", "calls"):
            assert e["source"] in node_ids


def test_ts_closure_factory_surfaces_inner_closures():
    """Closure-as-module: `function f() { function inner() {...}; const
    arrow = () => {}; return { inner, arrow } }`. Without the closure-
    pass, `inner`/`arrow` were invisible (`@inner` fell through to fuzzy
    on unrelated names). Now they're method-shaped children of the
    outer factory."""
    r = extract_js(FIXTURES / "closure_factory.ts")
    labels = _labels(r)
    # Outer factory still registered
    assert "buildEngine()" in labels
    # All three inner closures lifted to nodes
    assert "probeForward()" in labels, f"missing probeForward; got {labels}"
    assert "injectForward()" in labels, f"missing injectForward; got {labels}"
    assert "embeddingGenerate()" in labels, f"missing embeddingGenerate; got {labels}"
    # Method edges from the factory to each closure
    method_pairs = {
        (e["source"], e["target"])
        for e in r["edges"] if e["relation"] == "method"
    }
    label_to_id = {n["label"]: n["id"] for n in r["nodes"]}
    outer = label_to_id["buildEngine()"]
    for inner in ("probeForward()", "injectForward()", "embeddingGenerate()"):
        assert (outer, label_to_id[inner]) in method_pairs, (
            f"missing method edge buildEngine → {inner}"
        )


def test_ts_closure_factory_intra_call_attributes_correctly():
    """A call inside a nested closure must register the closure as the
    caller, not the outer factory. Without this, `injectForward calls
    probeForward` would attribute as `buildEngine calls probeForward` —
    semantically wrong, swamps the outer factory's call list."""
    r = extract_js(FIXTURES / "closure_factory.ts")
    label_to_id = {n["label"]: n["id"] for n in r["nodes"]}
    inner_caller = label_to_id["injectForward()"]
    inner_callee = label_to_id["probeForward()"]
    call_edges = {
        (e["source"], e["target"])
        for e in r["edges"] if e["relation"] == "calls"
    }
    assert (inner_caller, inner_callee) in call_edges, (
        f"closure-internal call not attributed to closure; got: {call_edges}"
    )


# ── Go ────────────────────────────────────────────────────────────────────────

def test_go_finds_struct():
    r = extract_go(FIXTURES / "sample.go")
    assert "error" not in r
    assert "Server" in _labels(r)

def test_go_finds_methods():
    r = extract_go(FIXTURES / "sample.go")
    labels = _labels(r)
    assert any("Start" in l for l in labels)
    assert any("Stop" in l for l in labels)

def test_go_finds_constructor():
    r = extract_go(FIXTURES / "sample.go")
    assert any("NewServer" in l for l in _labels(r))

def test_go_emits_calls():
    r = extract_go(FIXTURES / "sample.go")
    # main() calls NewServer and Start
    assert len(_call_pairs(r)) > 0

def test_go_has_extracted_calls():
    r = extract_go(FIXTURES / "sample.go")
    assert "EXTRACTED" in _confidences(r)

def test_go_no_dangling_edges():
    r = extract_go(FIXTURES / "sample.go")
    node_ids = {n["id"] for n in r["nodes"]}
    for e in r["edges"]:
        if e["relation"] in ("contains", "method", "calls"):
            assert e["source"] in node_ids


# ── Rust ──────────────────────────────────────────────────────────────────────

def test_rust_finds_struct():
    r = extract_rust(FIXTURES / "sample.rs")
    assert "error" not in r
    assert "Graph" in _labels(r)

def test_rust_finds_impl_methods():
    r = extract_rust(FIXTURES / "sample.rs")
    labels = _labels(r)
    assert any("add_node" in l for l in labels)
    assert any("add_edge" in l for l in labels)

def test_rust_finds_function():
    r = extract_rust(FIXTURES / "sample.rs")
    assert any("build_graph" in l for l in _labels(r))

def test_rust_emits_calls():
    r = extract_rust(FIXTURES / "sample.rs")
    calls = _call_pairs(r)
    assert any("build_graph" in src for src, _ in calls)

def test_rust_calls_are_extracted():
    r = extract_rust(FIXTURES / "sample.rs")
    for e in r["edges"]:
        if e["relation"] == "calls":
            assert e["confidence"] == "EXTRACTED"

def test_rust_no_dangling_edges():
    r = extract_rust(FIXTURES / "sample.rs")
    node_ids = {n["id"] for n in r["nodes"]}
    for e in r["edges"]:
        if e["relation"] in ("contains", "method", "calls"):
            assert e["source"] in node_ids


# ── extract() dispatch ────────────────────────────────────────────────────────

def test_extract_dispatches_all_languages():
    files = [
        FIXTURES / "sample.py",
        FIXTURES / "sample.ts",
        FIXTURES / "sample.go",
        FIXTURES / "sample.rs",
    ]
    r = extract(files)
    source_files = {n["source_file"] for n in r["nodes"] if n["source_file"]}
    # All four files should contribute nodes
    assert any("sample.py" in f for f in source_files)
    assert any("sample.ts" in f for f in source_files)
    assert any("sample.go" in f for f in source_files)
    assert any("sample.rs" in f for f in source_files)


# ── Cache ─────────────────────────────────────────────────────────────────────

def test_cache_hit_returns_same_result(tmp_path):
    src = FIXTURES / "sample.py"
    dst = tmp_path / "sample.py"
    dst.write_bytes(src.read_bytes())

    r1 = extract([dst])
    r2 = extract([dst])
    assert len(r1["nodes"]) == len(r2["nodes"])
    assert len(r1["edges"]) == len(r2["edges"])

def test_cache_miss_after_file_change(tmp_path):
    dst = tmp_path / "a.py"
    dst.write_text("def foo(): pass\n")
    r1 = extract([dst])

    dst.write_text("def foo(): pass\ndef bar(): pass\n")
    r2 = extract([dst])
    # bar() should appear in the second result
    labels2 = [n["label"] for n in r2["nodes"]]
    assert any("bar" in l for l in labels2)
