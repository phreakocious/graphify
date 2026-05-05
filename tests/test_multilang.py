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


def test_ts_interface_and_class_node_kind(tmp_path):
    """Lap-6 friction 5: extracted nodes must carry `node_kind` so the
    disambig listing can annotate `[iface]`/`[impl]`. Without this the
    agent can't tell a runtime class method from an interface signature
    that share the same name."""
    src = tmp_path / "shape.ts"
    src.write_text("""
interface Shape {
  area(): number;
}
class Circle implements Shape {
  area(): number { return 3.14; }
}
""")
    r = extract_js(src)
    by_label = {n["label"]: n for n in r["nodes"]}
    assert by_label["Shape"]["node_kind"] == "interface"
    assert by_label["Circle"]["node_kind"] == "class"
    assert by_label[".area()"]["node_kind"] in ("iface_method", "impl_method")
    # Both interface and class have a `.area()` — find them by parent kind
    iface_method_id = by_label["Shape"]["id"]
    impl_method_id = by_label["Circle"]["id"]
    method_targets = {(e["source"], e["target"])
                      for e in r["edges"] if e["relation"] == "method"}
    assert any(s == iface_method_id for s, _ in method_targets)
    assert any(s == impl_method_id for s, _ in method_targets)


def test_ts_interface_property_signatures_become_methods(tmp_path):
    """Lap-12: a callback-typed interface member like
    `prebuilt: (engine) => void` is a `property_signature` AST node, not a
    `method_signature`. Both shapes are part of the interface contract, so
    `methods`/`contains` should surface them. Plain data fields under inline
    type literals (function param types) must NOT leak as nodes."""
    src = tmp_path / "iface.ts"
    src.write_text("""
export interface TriggerHooks {
    prebuilt: (engine: any) => void;
    captureLayer: (n: number) => void;
    runHook(name: string): boolean;
}
function consume(opts: { temp: number }): void { }
""")
    r = extract_js(src)
    by_label = {n["label"]: n for n in r["nodes"]}
    # All three interface members should land as iface_method.
    for name in (".prebuilt()", ".captureLayer()", ".runHook()"):
        assert name in by_label, f"missing {name}, got: {sorted(by_label)}"
        assert by_label[name]["node_kind"] == "iface_method", (
            f"{name} should be iface_method, got: {by_label[name]['node_kind']}"
        )
    # All three should be method-of the interface.
    iface_id = by_label["TriggerHooks"]["id"]
    method_targets = {(e["source"], e["target"])
                      for e in r["edges"] if e["relation"] == "method"}
    for name in (".prebuilt()", ".captureLayer()", ".runHook()"):
        assert (iface_id, by_label[name]["id"]) in method_targets, (
            f"missing method edge TriggerHooks → {name}"
        )
    # Inline type literal in `consume(opts: { temp: number })` must NOT
    # produce a `.temp()` node.
    assert ".temp()" not in by_label, (
        f"inline type-literal property leaked as a node: {sorted(by_label)}"
    )


def test_ts_class_implements_interface_emits_impl_of(tmp_path):
    """`class Foo implements Bar` should yield an `impl_of` edge (not a
    raw type_ref) when Bar is in the same file."""
    src = tmp_path / "iface.ts"
    src.write_text("""
interface Pet { name: string; }
class Dog implements Pet { name = "rex"; }
""")
    r = extract_js(src)
    impl_of = [(e["source"], e["target"]) for e in r["edges"]
               if e["relation"] == "impl_of"]
    assert len(impl_of) == 1, f"expected 1 impl_of edge, got: {r['edges']}"
    by_label = {n["label"]: n["id"] for n in r["nodes"]}
    assert (by_label["Dog"], by_label["Pet"]) in impl_of


def test_ts_interface_extends_emits_inherits(tmp_path):
    """`interface Pet extends Animal` should yield an `inherits` edge
    in the same file."""
    src = tmp_path / "ext.ts"
    src.write_text("""
interface Animal { age: number; }
interface Pet extends Animal { owner: string; }
""")
    r = extract_js(src)
    inh = [(e["source"], e["target"]) for e in r["edges"]
           if e["relation"] == "inherits"]
    by_label = {n["label"]: n["id"] for n in r["nodes"]}
    assert (by_label["Pet"], by_label["Animal"]) in inh


def test_ts_type_ref_captures_param_and_return_types(tmp_path):
    """Lap-6 friction 6: TS interfaces and type aliases used as types
    in function signatures must produce `type_ref` edges. Without this,
    `SteeringMode`/`TriggerHooks` show degree=1 even when they're the
    canonical type surface used everywhere."""
    src = tmp_path / "use.ts"
    src.write_text("""
interface Hook { on(): void; }
type Mode = "fast" | "slow";
function run(h: Hook, m: Mode): Hook {
  return h;
}
""")
    r = extract_js(src)
    by_label = {n["label"]: n["id"] for n in r["nodes"]}
    type_refs = {(e["source"], e["target"]) for e in r["edges"]
                 if e["relation"] == "type_ref"}
    assert (by_label["run()"], by_label["Hook"]) in type_refs
    assert (by_label["run()"], by_label["Mode"]) in type_refs


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
