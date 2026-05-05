"""Tests for serve.py - MCP graph query helpers (no mcp package required)."""
import json
import pytest
import networkx as nx
from networkx.readwrite import json_graph

from graphify.serve import (
    _communities_from_graph,
    _score_nodes,
    _bfs,
    _dfs,
    _subgraph_to_text,
    _load_graph,
)


def _make_graph() -> nx.Graph:
    G = nx.Graph()
    G.add_node("n1", label="extract", source_file="extract.py", source_location="L10", community=0)
    G.add_node("n2", label="cluster", source_file="cluster.py", source_location="L5", community=0)
    G.add_node("n3", label="build", source_file="build.py", source_location="L1", community=1)
    G.add_node("n4", label="report", source_file="report.py", source_location="L1", community=1)
    G.add_node("n5", label="isolated", source_file="other.py", source_location="L1", community=2)
    G.add_edge("n1", "n2", relation="calls", confidence="INFERRED")
    G.add_edge("n2", "n3", relation="imports", confidence="EXTRACTED")
    G.add_edge("n3", "n4", relation="uses", confidence="EXTRACTED")
    return G


# --- _communities_from_graph ---

def test_communities_from_graph_basic():
    G = _make_graph()
    communities = _communities_from_graph(G)
    assert 0 in communities
    assert 1 in communities
    assert "n1" in communities[0]
    assert "n2" in communities[0]
    assert "n3" in communities[1]

def test_communities_from_graph_no_community_attr():
    G = nx.Graph()
    G.add_node("a", label="foo")  # no community attr
    communities = _communities_from_graph(G)
    assert communities == {}

def test_communities_from_graph_isolated():
    G = _make_graph()
    communities = _communities_from_graph(G)
    assert 2 in communities
    assert "n5" in communities[2]


# --- _score_nodes ---

def test_score_nodes_exact_label_match():
    G = _make_graph()
    scored = _score_nodes(G, ["extract"])
    nids = [nid for _, nid in scored]
    assert "n1" in nids
    assert scored[0][1] == "n1"  # highest score first

def test_score_nodes_no_match():
    G = _make_graph()
    scored = _score_nodes(G, ["xyzzy"])
    assert scored == []

def test_score_nodes_source_file_partial():
    G = _make_graph()
    # "cluster.py" contains "cluster" - should score 0.5 for source match
    scored = _score_nodes(G, ["cluster"])
    nids = [nid for _, nid in scored]
    assert "n2" in nids


# --- _bfs ---

def test_bfs_depth_1():
    G = _make_graph()
    visited, edges = _bfs(G, ["n1"], depth=1)
    assert "n1" in visited
    assert "n2" in visited  # direct neighbor
    assert "n3" not in visited  # 2 hops away

def test_bfs_depth_2():
    G = _make_graph()
    visited, edges = _bfs(G, ["n1"], depth=2)
    assert "n3" in visited  # n1 -> n2 -> n3

def test_bfs_disconnected():
    G = _make_graph()
    visited, edges = _bfs(G, ["n5"], depth=3)
    assert visited == {"n5"}  # isolated node

def test_bfs_returns_edges():
    G = _make_graph()
    visited, edges = _bfs(G, ["n1"], depth=1)
    assert len(edges) >= 1
    assert any(u == "n1" or v == "n1" for u, v in edges)


# --- _dfs ---

def test_dfs_depth_1():
    G = _make_graph()
    visited, edges = _dfs(G, ["n1"], depth=1)
    assert "n1" in visited
    assert "n2" in visited
    assert "n3" not in visited

def test_dfs_full_chain():
    G = _make_graph()
    visited, edges = _dfs(G, ["n1"], depth=5)
    assert {"n1", "n2", "n3", "n4"}.issubset(visited)


# --- _subgraph_to_text ---

def test_subgraph_to_text_contains_labels():
    G = _make_graph()
    text = _subgraph_to_text(G, {"n1", "n2"}, [("n1", "n2")])
    assert "extract" in text
    assert "cluster" in text

def test_subgraph_to_text_truncates():
    G = _make_graph()
    # Very small budget forces truncation
    text = _subgraph_to_text(G, {"n1", "n2", "n3", "n4"}, [("n1", "n2")], token_budget=1)
    assert "truncated" in text

def test_subgraph_to_text_edge_included():
    G = _make_graph()
    text = _subgraph_to_text(G, {"n1", "n2"}, [("n1", "n2")])
    assert "EDGE" in text
    assert "calls" in text


def test_subgraph_to_text_priority_nodes_render_first():
    """Lap-12: when priority_nodes is set (no-anchor query path), the
    suggestion-engine's top-N must render first regardless of degree.
    Without this, BFS-expansion's degree-sort buries the term-scored hit
    below hub neighbours."""
    G = nx.Graph()
    # `hub` has degree 3, `target` has degree 1. Default sort would put
    # hub first; priority should flip target to the top.
    G.add_node("hub", label="types_hub", source_file="types.ts",
               source_location="L1", community=0)
    G.add_node("target", label="polar_decomp", source_file="polar.ts",
               source_location="L1", community=0)
    G.add_node("a", label="a")
    G.add_node("b", label="b")
    G.add_node("c", label="c")
    G.add_edge("hub", "a"); G.add_edge("hub", "b"); G.add_edge("hub", "c")
    G.add_edge("target", "hub")
    nodes = {"hub", "target", "a", "b", "c"}
    text_default = _subgraph_to_text(G, nodes, [], token_budget=2000)
    text_priority = _subgraph_to_text(G, nodes, [], token_budget=2000,
                                       priority_nodes=["target"])
    # Default: hub appears first (highest degree)
    assert text_default.index("types_hub") < text_default.index("polar_decomp")
    # Priority: target appears first
    assert text_priority.index("polar_decomp") < text_priority.index("types_hub")


def test_subgraph_to_text_node_limit_caps_rows():
    """Lap-16 TS field-report friction 3: `query --limit 10` was ignored.
    `node_limit` caps rendered NODE rows and drops edges between dropped
    nodes — the byte-budget truncator alone can't satisfy a structural cap."""
    G = nx.Graph()
    for i in range(20):
        G.add_node(f"n{i}", label=f"sym{i}", source_file=f"f{i}.py",
                   source_location="L1", community=0)
    # No edges — keeps the test focused on the node-cap path.
    nodes = {f"n{i}" for i in range(20)}
    text = _subgraph_to_text(G, nodes, [], token_budget=10000, node_limit=5)
    # Exactly 5 NODE rows
    assert text.count("NODE ") == 5, f"expected 5, got {text.count('NODE ')}\n{text}"
    # Truncation footer present
    assert "+15 more nodes" in text, text


def test_subgraph_to_text_node_limit_drops_edges_to_dropped_nodes():
    """When `node_limit` cuts a node, edges incident on that node must
    not appear in the output — otherwise the listing has dangling
    endpoints with no NODE row to match.

    `priority_nodes` pins the kept node so this test doesn't depend on
    nondeterministic degree-tied iteration order."""
    G = nx.Graph()
    G.add_node("keep", label="keep", source_file="a.py", source_location="L1", community=0)
    G.add_node("drop", label="drop", source_file="b.py", source_location="L1", community=0)
    G.add_edge("keep", "drop", relation="calls", confidence="EXTRACTED")
    nodes = {"keep", "drop"}
    text = _subgraph_to_text(G, nodes, [("keep", "drop")], token_budget=10000,
                             priority_nodes=["keep"], node_limit=1)
    assert "NODE keep" in text
    assert "NODE drop" not in text
    assert "EDGE" not in text, f"edge to dropped node should be omitted:\n{text}"


# --- _load_graph ---

def test_load_graph_roundtrip(tmp_path):
    G = _make_graph()
    data = json_graph.node_link_data(G, edges="links")
    p = tmp_path / "graph.json"
    p.write_text(json.dumps(data))
    G2 = _load_graph(str(p))
    assert G2.number_of_nodes() == G.number_of_nodes()
    assert G2.number_of_edges() == G.number_of_edges()

def test_load_graph_missing_file(tmp_path):
    graphify_dir = tmp_path / "graphify-out"
    graphify_dir.mkdir()
    with pytest.raises(SystemExit):
        _load_graph(str(graphify_dir / "nonexistent.json"))
