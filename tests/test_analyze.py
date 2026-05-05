"""Tests for analyze.py."""
import json
import networkx as nx
from pathlib import Path
from graphify.build import build_from_json
from graphify.cluster import cluster
from graphify.analyze import god_nodes, surprising_connections, _is_concept_node, _is_rationale_node, graph_diff, _surprise_score, _file_category, suggest_questions

FIXTURES = Path(__file__).parent / "fixtures"


def make_graph():
    return build_from_json(json.loads((FIXTURES / "extraction.json").read_text()))


def test_god_nodes_returns_list():
    G = make_graph()
    result = god_nodes(G, top_n=3)
    assert isinstance(result, list)
    assert len(result) <= 3


def test_god_nodes_sorted_by_degree():
    G = make_graph()
    result = god_nodes(G, top_n=10)
    degrees = [r["degree"] for r in result]
    assert degrees == sorted(degrees, reverse=True)


def test_god_nodes_have_required_keys():
    G = make_graph()
    result = god_nodes(G, top_n=1)
    assert "id" in result[0]
    assert "label" in result[0]
    assert "degree" in result[0]


def test_surprising_connections_cross_source_multi_file():
    """Multi-file graph: should find cross-file edges between real entities."""
    G = make_graph()
    communities = cluster(G)
    surprises = surprising_connections(G, communities)
    assert len(surprises) > 0
    for s in surprises:
        assert s["source_files"][0] != s["source_files"][1]


def test_surprising_connections_excludes_concept_nodes():
    """Concept nodes (empty source_file) must not appear in surprises."""
    G = make_graph()
    # Add a concept node with empty source_file
    G.add_node("concept_x", label="Abstract Concept", file_type="document", source_file="")
    G.add_edge("n_transformer", "concept_x", relation="relates_to",
               confidence="INFERRED", source_file="", weight=0.5)
    communities = cluster(G)
    surprises = surprising_connections(G, communities)
    labels = [s["source"] for s in surprises] + [s["target"] for s in surprises]
    assert "Abstract Concept" not in labels


def test_surprising_connections_single_file_uses_community_bridges():
    """Single-file graph: should return cross-community edges, not empty list."""
    G = nx.Graph()
    # Build a graph with 2 clear communities + 1 bridge edge
    for i in range(5):
        G.add_node(f"a{i}", label=f"A{i}", file_type="code", source_file="single.py",
                   source_location=f"L{i}")
    for i in range(5):
        G.add_node(f"b{i}", label=f"B{i}", file_type="code", source_file="single.py",
                   source_location=f"L{i+10}")
    # Dense intra-community edges
    for i in range(4):
        G.add_edge(f"a{i}", f"a{i+1}", relation="calls", confidence="EXTRACTED",
                   source_file="single.py", weight=1.0)
    for i in range(4):
        G.add_edge(f"b{i}", f"b{i+1}", relation="calls", confidence="EXTRACTED",
                   source_file="single.py", weight=1.0)
    # One cross-community bridge
    G.add_edge("a4", "b0", relation="references", confidence="INFERRED",
               source_file="single.py", weight=0.5)

    communities = cluster(G)
    surprises = surprising_connections(G, communities)
    # Should find at least the bridge edge
    assert len(surprises) > 0


def test_surprising_connections_ambiguous_scores_higher_than_extracted():
    """AMBIGUOUS edge should score higher than an otherwise identical EXTRACTED edge."""
    G = nx.Graph()
    for nid, label, src in [
        ("a", "Alpha", "repo1/model.py"),
        ("b", "Beta", "repo2/train.py"),
        ("c", "Gamma", "repo1/data.py"),
        ("d", "Delta", "repo2/eval.py"),
    ]:
        G.add_node(nid, label=label, source_file=src, file_type="code")
    G.add_edge("a", "b", relation="calls", confidence="AMBIGUOUS", weight=1.0, source_file="repo1/model.py")
    G.add_edge("c", "d", relation="calls", confidence="EXTRACTED", weight=1.0, source_file="repo1/data.py")
    communities = {0: ["a", "c"], 1: ["b", "d"]}
    nc = {"a": 0, "c": 0, "b": 1, "d": 1}
    score_amb, _ = _surprise_score(G, "a", "b", G.edges["a", "b"], nc, "repo1/model.py", "repo2/train.py")
    score_ext, _ = _surprise_score(G, "c", "d", G.edges["c", "d"], nc, "repo1/data.py", "repo2/eval.py")
    assert score_amb > score_ext


def test_surprising_connections_cross_type_scores_higher():
    """Code↔paper edge should score higher than code↔code edge."""
    G = nx.Graph()
    for nid, label, src in [
        ("a", "Transformer", "code/model.py"),
        ("b", "FlashAttn", "papers/flash.pdf"),
        ("c", "Trainer", "code/train.py"),
        ("d", "Dataset", "code/data.py"),
    ]:
        G.add_node(nid, label=label, source_file=src, file_type="code")
    G.add_edge("a", "b", relation="references", confidence="EXTRACTED", weight=1.0, source_file="code/model.py")
    G.add_edge("c", "d", relation="calls", confidence="EXTRACTED", weight=1.0, source_file="code/train.py")
    nc = {"a": 0, "b": 1, "c": 0, "d": 0}
    score_cross, reasons_cross = _surprise_score(G, "a", "b", G.edges["a", "b"], nc, "code/model.py", "papers/flash.pdf")
    score_same, _ = _surprise_score(G, "c", "d", G.edges["c", "d"], nc, "code/train.py", "code/data.py")
    assert score_cross > score_same
    assert any("code" in r and "paper" in r for r in reasons_cross)


def test_surprising_connections_have_why_field():
    G = make_graph()
    communities = cluster(G)
    for s in surprising_connections(G, communities):
        assert "why" in s
        assert isinstance(s["why"], str)
        assert len(s["why"]) > 0


def test_file_category():
    assert _file_category("model.py") == "code"
    assert _file_category("flash.pdf") == "paper"
    assert _file_category("diagram.png") == "image"
    assert _file_category("notes.md") == "doc"
    # Languages added in later releases — would misclassify as "doc" without detect.py import
    assert _file_category("app.swift") == "code"
    assert _file_category("plugin.lua") == "code"
    assert _file_category("build.zig") == "code"
    assert _file_category("deploy.ps1") == "code"
    assert _file_category("server.ex") == "code"
    assert _file_category("component.jsx") == "code"
    assert _file_category("analysis.jl") == "code"
    assert _file_category("view.m") == "code"


def test_is_concept_node_empty_source():
    G = nx.Graph()
    G.add_node("c1", source_file="")
    assert _is_concept_node(G, "c1") is True


def test_is_concept_node_real_file():
    G = nx.Graph()
    G.add_node("n1", source_file="model.py")
    assert _is_concept_node(G, "n1") is False


def test_surprising_connections_have_required_keys():
    G = make_graph()
    communities = cluster(G)
    for s in surprising_connections(G, communities):
        assert "source" in s
        assert "target" in s
        assert "source_files" in s
        assert "confidence" in s


# --- graph_diff tests ---

def _make_simple_graph(nodes, edges):
    """Helper: build a small nx.Graph from node/edge specs."""
    G = nx.Graph()
    for node_id, label in nodes:
        G.add_node(node_id, label=label, source_file="test.py")
    for src, tgt, rel, conf in edges:
        G.add_edge(src, tgt, relation=rel, confidence=conf)
    return G


def test_graph_diff_new_nodes():
    G_old = _make_simple_graph([("n1", "Alpha"), ("n2", "Beta")], [])
    G_new = _make_simple_graph([("n1", "Alpha"), ("n2", "Beta"), ("n3", "Gamma")], [])
    diff = graph_diff(G_old, G_new)
    assert len(diff["new_nodes"]) == 1
    assert diff["new_nodes"][0]["id"] == "n3"
    assert diff["new_nodes"][0]["label"] == "Gamma"
    assert diff["removed_nodes"] == []
    assert "1 new node" in diff["summary"]


def test_graph_diff_removed_nodes():
    G_old = _make_simple_graph([("n1", "Alpha"), ("n2", "Beta"), ("n3", "Gamma")], [])
    G_new = _make_simple_graph([("n1", "Alpha"), ("n2", "Beta")], [])
    diff = graph_diff(G_old, G_new)
    assert diff["new_nodes"] == []
    assert len(diff["removed_nodes"]) == 1
    assert diff["removed_nodes"][0]["id"] == "n3"
    assert "removed" in diff["summary"]


def test_graph_diff_new_edges():
    nodes = [("n1", "Alpha"), ("n2", "Beta"), ("n3", "Gamma")]
    G_old = _make_simple_graph(nodes, [("n1", "n2", "calls", "EXTRACTED")])
    G_new = _make_simple_graph(
        nodes,
        [("n1", "n2", "calls", "EXTRACTED"), ("n2", "n3", "uses", "INFERRED")],
    )
    diff = graph_diff(G_old, G_new)
    assert len(diff["new_edges"]) == 1
    new_edge = diff["new_edges"][0]
    assert new_edge["relation"] == "uses"
    assert new_edge["confidence"] == "INFERRED"
    assert diff["removed_edges"] == []
    assert "new edge" in diff["summary"]


def test_graph_diff_empty_diff():
    nodes = [("n1", "Alpha"), ("n2", "Beta")]
    edges = [("n1", "n2", "calls", "EXTRACTED")]
    G_old = _make_simple_graph(nodes, edges)
    G_new = _make_simple_graph(nodes, edges)
    diff = graph_diff(G_old, G_new)
    assert diff["new_nodes"] == []
    assert diff["removed_nodes"] == []
    assert diff["new_edges"] == []
    assert diff["removed_edges"] == []
    assert diff["summary"] == "no changes"


# --- rationale-node gap-detection tests ---

def test_is_rationale_node_via_file_type():
    G = nx.Graph()
    G.add_node("rat", label="Computes the score.", file_type="rationale", source_file="m.py")
    assert _is_rationale_node(G, "rat") is True


def test_is_rationale_node_via_edge_relation_only():
    G = nx.Graph()
    G.add_node("rat", label="Computes the score.", source_file="m.py")
    G.add_node("fn", label="compute_score", file_type="code", source_file="m.py")
    G.add_edge("rat", "fn", relation="rationale_for", confidence="EXTRACTED")
    assert _is_rationale_node(G, "rat") is True


def test_is_rationale_node_returns_false_for_code():
    G = nx.Graph()
    G.add_node("fn", label="compute_score", file_type="code", source_file="m.py")
    G.add_node("other", label="other", file_type="code", source_file="m.py")
    G.add_edge("fn", "other", relation="calls", confidence="EXTRACTED")
    assert _is_rationale_node(G, "fn") is False


def test_suggest_questions_excludes_rationale_from_isolated():
    """Rationale nodes attached via single rationale_for edge are not gaps."""
    G = nx.Graph()
    # Triangle of well-connected code nodes (no isolated code).
    for nid in ("a", "b", "c"):
        G.add_node(nid, label=nid, file_type="code", source_file="m.py")
    G.add_edge("a", "b", relation="calls", confidence="EXTRACTED", source_file="m.py")
    G.add_edge("b", "c", relation="calls", confidence="EXTRACTED", source_file="m.py")
    G.add_edge("a", "c", relation="calls", confidence="EXTRACTED", source_file="m.py")
    # Rationale orbiters — each has degree 1, but they're not gaps
    for i in range(5):
        rid = f"rat{i}"
        G.add_node(rid, label=f"docstring sentence {i}.", file_type="rationale", source_file="m.py")
        G.add_edge(rid, "a", relation="rationale_for", confidence="EXTRACTED", source_file="m.py")
    communities = {0: ["a", "b", "c"] + [f"rat{i}" for i in range(5)]}
    labels = {0: "alpha"}
    qs = suggest_questions(G, communities, labels)
    isolated_qs = [q for q in qs if q.get("type") == "isolated_nodes"]
    assert isolated_qs == [], f"rationale nodes flagged as isolated: {isolated_qs}"


def test_report_skips_rationale_only_community_in_communities_section():
    """A community whose only member is a rationale (docstring) node renders
    as `Nodes (1): "Find metrics where every '+' source..."` — sentence-shaped
    label, not actionable. Drop it the same way we drop file-stub-only ones."""
    from graphify.report import generate
    G = nx.Graph()
    G.add_node("doc", label="Find metrics where every '+' source...",
               file_type="rationale", source_file="m.py", source_location="L9")
    # Padding code-only community
    for i, lbl in enumerate(("X", "Y", "Z", "W")):
        G.add_node(lbl.lower(), label=lbl, file_type="code", source_file="o.py",
                   source_location=f"L{i+1}")
    G.add_edge("x", "y", relation="calls", confidence="EXTRACTED", source_file="o.py")
    G.add_edge("y", "z", relation="calls", confidence="EXTRACTED", source_file="o.py")
    G.add_edge("z", "w", relation="calls", confidence="EXTRACTED", source_file="o.py")
    communities = {0: ["doc"], 1: ["x", "y", "z", "w"]}
    cohesion = {0: 1.0, 1: 1.0}
    labels = {0: "RatOnly", 1: "Other"}
    detection = {"total_files": 2, "total_words": 100, "needs_graph": True, "warning": None}
    report = generate(G, communities, cohesion, labels, [], [], detection,
                      {"input": 0, "output": 0}, "./p")
    assert "### Community 0" not in report
    assert "Find metrics" not in report
    assert "### Community 1" in report


def test_report_skips_communities_with_no_real_nodes():
    """A community whose entire membership is file/stub nodes filters down
    to zero displayable members. Don't emit `Nodes (0):` — skip the entry."""
    from graphify.report import generate
    G = nx.Graph()
    # File-stub-only community: file label matches source_file basename
    # → _is_file_node returns True → real_nodes will be empty.
    G.add_node("f1", label="solo.py", file_type="code", source_file="solo.py", source_location="L1")
    G.add_node("f2", label="other.py", file_type="code", source_file="other.py", source_location="L1")
    G.add_edge("f1", "f2", relation="imports", confidence="EXTRACTED", source_file="solo.py")
    # Padding community with real (non-stub) code nodes
    for i, lbl in enumerate(("X", "Y", "Z", "W")):
        G.add_node(lbl.lower(), label=lbl, file_type="code", source_file="o.py",
                   source_location=f"L{i+1}")
    G.add_edge("x", "y", relation="calls", confidence="EXTRACTED", source_file="o.py")
    G.add_edge("y", "z", relation="calls", confidence="EXTRACTED", source_file="o.py")
    G.add_edge("z", "w", relation="calls", confidence="EXTRACTED", source_file="o.py")
    communities = {0: ["f1", "f2"], 1: ["x", "y", "z", "w"]}
    cohesion = {0: 1.0, 1: 1.0}
    labels = {0: "FilesOnly", 1: "Other"}
    detection = {"total_files": 2, "total_words": 100, "needs_graph": True, "warning": None}
    report = generate(G, communities, cohesion, labels, [], [], detection,
                      {"input": 0, "output": 0}, "./p")
    assert "Nodes (0)" not in report
    # Community 0 should be elided since all its members are file stubs
    assert "### Community 0" not in report
    # Community 1 should still appear
    assert "### Community 1" in report


def test_report_skips_singleton_communities():
    """A community of size 1 isn't a thin community — it's clustering noise.
    Reporting "Community X (1 node) too small to be meaningful" produces no
    actionable signal, so suppress."""
    from graphify.report import generate
    G = nx.Graph()
    # Singleton community: just an isolated file node
    G.add_node("loner", label="loner.py", file_type="code",
               source_file="loner.py", source_location="L1")
    # Padding community with ≥3 nodes so report has something else to report
    for i, lbl in enumerate(("X", "Y", "Z", "W")):
        G.add_node(lbl.lower(), label=lbl, file_type="code", source_file="o.py",
                   source_location=f"L{i+1}")
    G.add_edge("x", "y", relation="calls", confidence="EXTRACTED", source_file="o.py")
    G.add_edge("y", "z", relation="calls", confidence="EXTRACTED", source_file="o.py")
    G.add_edge("z", "w", relation="calls", confidence="EXTRACTED", source_file="o.py")
    communities = {0: ["loner"], 1: ["x", "y", "z", "w"]}
    cohesion = {0: 1.0, 1: 1.0}
    labels = {0: "Loner", 1: "Other"}
    detection = {"total_files": 2, "total_words": 100, "needs_graph": True, "warning": None}
    report = generate(G, communities, cohesion, labels, [], [], detection,
                      {"input": 0, "output": 0}, "./p")
    assert "Loner" not in report.split("## Knowledge Gaps")[-1] if "## Knowledge Gaps" in report else True
    assert "loner.py" not in report.split("## Knowledge Gaps")[-1] if "## Knowledge Gaps" in report else True


def test_report_skips_docstring_pair_thin_community():
    """A 2-node community of {1 code, 1 rationale} is a docstring pair, not noise."""
    from graphify.report import generate
    G = nx.Graph()
    G.add_node("fn", label="_gap_ratio()", file_type="code",
               source_file="m.py", source_location="L10")
    G.add_node("doc", label="λ₃/λ₂ — gap above Fiedler eigenvalue.",
               file_type="rationale", source_file="m.py", source_location="L9")
    G.add_edge("doc", "fn", relation="rationale_for", confidence="EXTRACTED",
               source_file="m.py")
    # Padding community with ≥3 code nodes so we don't trip a different thin-community case
    for i, lbl in enumerate(("X", "Y", "Z", "W")):
        G.add_node(lbl.lower(), label=lbl, file_type="code", source_file="o.py",
                   source_location=f"L{i+1}")
    G.add_edge("x", "y", relation="calls", confidence="EXTRACTED", source_file="o.py")
    G.add_edge("y", "z", relation="calls", confidence="EXTRACTED", source_file="o.py")
    G.add_edge("z", "w", relation="calls", confidence="EXTRACTED", source_file="o.py")
    communities = {0: ["fn", "doc"], 1: ["x", "y", "z", "w"]}
    cohesion = {0: 1.0, 1: 1.0}
    labels = {0: "GapRatio", 1: "Other"}
    detection = {"total_files": 2, "total_words": 100, "needs_graph": True, "warning": None}
    tokens = {"input": 0, "output": 0}
    report = generate(G, communities, cohesion, labels, [], [], detection, tokens, "./p")
    assert "Thin community" not in report, "docstring pair flagged as thin community"
    if "## Knowledge Gaps" in report:
        gaps = report.split("## Knowledge Gaps")[-1]
        assert "λ₃/λ₂" not in gaps, "rationale node flagged as isolated"


def test_report_thin_community_uses_real_node_count():
    """A 2-node community of {symbol, file} is shown as `Nodes (1):` in the
    Communities section (file filtered). The Knowledge Gaps thin-community
    detector must agree — counting the file too produces inconsistent
    output where the same community is "1 node" up top and "2-node thin"
    in the gap list."""
    from graphify.report import generate
    G = nx.Graph()
    # Symbol-and-its-file pair — the dominant 2-node thin shape on AST-only graphs
    G.add_node("node_rs", label="node.rs", file_type="code",
               source_file="node.rs", source_location="L1")
    G.add_node("node_struct", label="Node", file_type="code",
               source_file="node.rs", source_location="L5")
    G.add_edge("node_struct", "node_rs", relation="defined_in",
               confidence="EXTRACTED", source_file="node.rs")
    # Padding community
    for i, lbl in enumerate(("X", "Y", "Z", "W")):
        G.add_node(lbl.lower(), label=lbl, file_type="code", source_file="o.py",
                   source_location=f"L{i+1}")
    G.add_edge("x", "y", relation="calls", confidence="EXTRACTED", source_file="o.py")
    G.add_edge("y", "z", relation="calls", confidence="EXTRACTED", source_file="o.py")
    G.add_edge("z", "w", relation="calls", confidence="EXTRACTED", source_file="o.py")
    communities = {0: ["node_struct", "node_rs"], 1: ["x", "y", "z", "w"]}
    cohesion = {0: 1.0, 1: 1.0}
    labels = {0: "NodeStuff", 1: "Other"}
    detection = {"total_files": 2, "total_words": 100, "needs_graph": True, "warning": None}
    tokens = {"input": 0, "output": 0}
    report = generate(G, communities, cohesion, labels, [], [], detection, tokens, "./p")
    if "## Knowledge Gaps" in report:
        gaps = report.split("## Knowledge Gaps")[-1]
        assert "Thin community" not in gaps, "{symbol, file} pair flagged as thin community"
