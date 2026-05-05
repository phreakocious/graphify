import json
from pathlib import Path
from graphify.build import build_from_json, build

FIXTURES = Path(__file__).parent / "fixtures"

def load_extraction():
    return json.loads((FIXTURES / "extraction.json").read_text())

def test_build_from_json_node_count():
    G = build_from_json(load_extraction())
    assert G.number_of_nodes() == 4

def test_build_from_json_edge_count():
    G = build_from_json(load_extraction())
    assert G.number_of_edges() == 4

def test_nodes_have_label():
    G = build_from_json(load_extraction())
    assert G.nodes["n_transformer"]["label"] == "Transformer"

def test_edges_have_confidence():
    G = build_from_json(load_extraction())
    data = G.edges["n_attention", "n_concept_attn"]
    assert data["confidence"] == "INFERRED"

def test_ambiguous_edge_preserved():
    G = build_from_json(load_extraction())
    data = G.edges["n_layernorm", "n_concept_attn"]
    assert data["confidence"] == "AMBIGUOUS"

def test_build_merges_multiple_extractions():
    ext1 = {"nodes": [{"id": "n1", "label": "A", "file_type": "code", "source_file": "a.py"}],
            "edges": [], "input_tokens": 0, "output_tokens": 0}
    ext2 = {"nodes": [{"id": "n2", "label": "B", "file_type": "document", "source_file": "b.md"}],
            "edges": [{"source": "n1", "target": "n2", "relation": "references",
                       "confidence": "INFERRED", "source_file": "b.md", "weight": 1.0}],
            "input_tokens": 0, "output_tokens": 0}
    G = build([ext1, ext2])
    assert G.number_of_nodes() == 2
    assert G.number_of_edges() == 1


def test_imports_to_external_module_creates_stub_node():
    """Edges with `imports`/`imports_from` relations whose target is an
    external module (numpy, sys, …) must survive the dangling-edge filter:
    a synthetic stub node is created so `who-imports-numpy` is queryable.
    """
    ext = {
        "nodes": [{"id": "foo_py", "label": "foo.py", "file_type": "code",
                   "source_file": "foo.py", "source_location": "L1"}],
        "edges": [
            {"source": "foo_py", "target": "numpy", "relation": "imports",
             "confidence": "EXTRACTED", "source_file": "foo.py",
             "source_location": "L3", "weight": 1.0},
            {"source": "foo_py", "target": "json", "relation": "imports_from",
             "confidence": "EXTRACTED", "source_file": "foo.py",
             "source_location": "L4", "weight": 1.0},
        ],
        "input_tokens": 0, "output_tokens": 0,
    }
    G = build_from_json(ext, directed=True)
    assert "numpy" in G.nodes
    assert "json" in G.nodes
    assert G.nodes["numpy"]["file_type"] == "external"
    assert G.nodes["numpy"]["node_kind"] == "external_module"
    assert G.has_edge("foo_py", "numpy")
    assert G.has_edge("foo_py", "json")
    assert G.edges["foo_py", "numpy"]["relation"] == "imports"


def test_non_import_edges_to_unknown_target_still_dropped():
    """Only `imports`/`imports_from` get the stub treatment. A `calls` edge
    pointing at an unknown target is a genuine extraction bug and should
    still be dropped — auto-creating stubs there would mask the bug."""
    ext = {
        "nodes": [{"id": "foo_py", "label": "foo.py", "file_type": "code",
                   "source_file": "foo.py", "source_location": "L1"}],
        "edges": [
            {"source": "foo_py", "target": "unknown_fn", "relation": "calls",
             "confidence": "EXTRACTED", "source_file": "foo.py",
             "source_location": "L3", "weight": 1.0},
        ],
        "input_tokens": 0, "output_tokens": 0,
    }
    G = build_from_json(ext, directed=True)
    assert "unknown_fn" not in G.nodes
    assert G.number_of_edges() == 0
