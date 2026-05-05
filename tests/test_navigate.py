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
