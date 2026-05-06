# assemble node+edge dicts into a NetworkX graph, preserving edge direction
#
# Node deduplication — three layers:
#
# 1. Within a file (AST): each extractor tracks a `seen_ids` set. A node ID is
#    emitted at most once per file, so duplicate class/function definitions in
#    the same source file are collapsed to the first occurrence.
#
# 2. Between files (build): NetworkX G.add_node() is idempotent — calling it
#    twice with the same ID overwrites the attributes with the second call's
#    values. Nodes are added in extraction order (AST first, then semantic),
#    so if the same entity is extracted by both passes the semantic node
#    silently overwrites the AST node. This is intentional: semantic nodes
#    carry richer labels and cross-file context, while AST nodes have precise
#    source_location. If you need to change the priority, reorder extractions
#    passed to build().
#
# 3. Semantic merge (skill): before calling build(), the skill merges cached
#    and new semantic results using an explicit `seen` set keyed on node["id"],
#    so duplicates across cache hits and new extractions are resolved there
#    before any graph construction happens.
#
from __future__ import annotations
import re
import sys
import networkx as nx
from .validate import validate_extraction


def _normalize_id(s: str) -> str:
    """Normalize an ID string the same way extract._make_id does.

    Used to reconcile edge endpoints when the LLM generates IDs with slightly
    different punctuation or casing than the AST extractor.
    """
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", s)
    return cleaned.strip("_").lower()


def build_from_json(extraction: dict, *, directed: bool = False) -> nx.Graph:
    """Build a NetworkX graph from an extraction dict.

    directed=True produces a DiGraph that preserves edge direction (source→target).
    directed=False (default) produces an undirected Graph for backward compatibility.
    """
    # NetworkX <= 3.1 serialised edges as "links"; remap to "edges" for compatibility.
    if "edges" not in extraction and "links" in extraction:
        extraction = dict(extraction, edges=extraction["links"])
    errors = validate_extraction(extraction)
    # Dangling edges (stdlib/external imports) are expected - only warn about real schema errors.
    real_errors = [e for e in errors if "does not match any node id" not in e]
    if real_errors:
        print(f"[graphify] Extraction warning ({len(real_errors)} issues): {real_errors[0]}", file=sys.stderr)
    G: nx.Graph = nx.DiGraph() if directed else nx.Graph()
    for node in extraction.get("nodes", []):
        G.add_node(node["id"], **{k: v for k, v in node.items() if k != "id"})
    node_set = set(G.nodes())
    # Identify rationale nodes once so the edge loop can drop LLM-emitted
    # `uses`/`calls`/etc. edges that originate at a docstring node — those
    # invert the rationale relationship (the docstring is *for* the symbol,
    # not a user *of* it) and inflate target degree with phantom traffic.
    # AST emits `target --rationale_for--> rationale` (correct direction);
    # LLM extractors frequently emit `rationale --uses--> target` instead.
    # See PR #576 follow-up note.
    rationale_ids = {nid for nid, attrs in G.nodes(data=True)
                     if attrs.get("file_type") == "rationale"}
    # Normalized ID map: lets edges survive when the LLM generates IDs with
    # slightly different casing or punctuation than the AST extractor.
    # e.g. "Session_ValidateToken" maps to "session_validatetoken".
    norm_to_id: dict[str, str] = {_normalize_id(nid): nid for nid in node_set}
    # Edges with `imports`/`imports_from` relations whose target is an
    # external module (`numpy`, `sys`, …) get a synthetic stub node so the
    # edge survives the dangling-edge filter below and `who-imports-X` is
    # answerable. Internal-only edges (`calls`, `uses`, …) still drop on
    # missing targets — those would be genuine extraction bugs.
    IMPORT_RELATIONS = {"imports", "imports_from"}
    rationale_edges_dropped = 0
    for edge in extraction.get("edges", []):
        if "source" not in edge and "from" in edge:
            edge["source"] = edge["from"]
        if "target" not in edge and "to" in edge:
            edge["target"] = edge["to"]
        if "source" not in edge or "target" not in edge:
            continue
        # Drop LLM-emitted edges where the source is a rationale node and the
        # relation isn't the canonical `rationale_for`. These flip the docstring
        # relationship and pile phantom degree onto the target. The AST path
        # always emits the correct direction; only LLM extractors get this wrong.
        if (edge["source"] in rationale_ids
                and edge.get("relation") != "rationale_for"):
            rationale_edges_dropped += 1
            continue
        # Direction restoration: undirected nx.Graph serialization can swap
        # source/target. `_src`/`_tgt` attributes (set on add_edge below) are
        # the authoritative original direction. Prefer them when present so
        # graphs serialized before the to_json fix still load with correct
        # edges in DiGraph mode.
        src = edge.get("_src") or edge["source"]
        tgt = edge.get("_tgt") or edge["target"]
        # Remap mismatched IDs via normalization before dropping the edge.
        if src not in node_set:
            src = norm_to_id.get(_normalize_id(src), src)
        if tgt not in node_set:
            tgt = norm_to_id.get(_normalize_id(tgt), tgt)
        if src not in node_set:
            continue
        if tgt not in node_set:
            if edge.get("relation") in IMPORT_RELATIONS:
                # Use the raw ID as the label so `numpy` displays as numpy
                # rather than a normalized stub. file_type=external lets
                # navigate filter these out of code-only listings.
                # source_file/source_location are empty (not a file we
                # extracted) — required by the schema for downstream
                # validate calls (e.g. navigate's reload of graph.json).
                G.add_node(tgt, label=tgt, file_type="external",
                           node_kind="external_module",
                           source_file="", source_location="")
                node_set.add(tgt)
                norm_to_id[_normalize_id(tgt)] = tgt
            else:
                continue
        attrs = {k: v for k, v in edge.items() if k not in ("source", "target")}
        # Preserve original edge direction - undirected graphs lose it otherwise,
        # causing display functions to show edges backwards.
        attrs["_src"] = src
        attrs["_tgt"] = tgt
        G.add_edge(src, tgt, **attrs)
    hyperedges = extraction.get("hyperedges", [])
    if hyperedges:
        G.graph["hyperedges"] = hyperedges
    if rationale_edges_dropped:
        # One-line note (counted, never silent) — the omission-counts rule.
        print(f"[graphify] Dropped {rationale_edges_dropped} rationale-source "
              f"edges with non-rationale_for relations (LLM direction-inversion).",
              file=sys.stderr)
    _backfill_node_kind(G)
    return G


def _backfill_node_kind(G: nx.Graph) -> None:
    """Set `node_kind` on nodes that lack one, so the invariant `every node
    has a node_kind` holds.

    Without this, ~80% of nodes in a Python corpus end up with kind=None
    (the Python extractor only tags classes; functions, methods, files,
    and rationale fragments are untagged), forcing every downstream
    consumer to re-derive the kind from label/file_type. Tooling that
    filters on node_kind would silently miss them.

    Rules — applied only when no kind is already set:
      - file_type=rationale → "rationale"
      - file_type=external  → "external_module"
      - label matches the basename of source_file → "file"
      - label starts with "." and ends with "()" → "method" (AST method stub)
      - label ends with "()" → "function"
      - otherwise leave None (likely a class node from a non-tagging pass —
        we'd rather leave it None than mislabel; classes get caught by
        explicit-tagging extractors).
    """
    from pathlib import Path as _Path
    for nid, attrs in G.nodes(data=True):
        if attrs.get("node_kind"):
            continue
        ft = attrs.get("file_type") or ""
        label = attrs.get("label") or ""
        if ft == "rationale":
            attrs["node_kind"] = "rationale"
            continue
        if ft == "external":
            attrs["node_kind"] = "external_module"
            continue
        src = attrs.get("source_file") or ""
        if src and label and _Path(src).name == label:
            attrs["node_kind"] = "file"
            continue
        if label.startswith(".") and label.endswith("()"):
            attrs["node_kind"] = "method"
            continue
        if label.endswith("()"):
            attrs["node_kind"] = "function"
            continue
        # Leave kind unset for nodes we can't classify by shape — better
        # than guessing wrong.


def build(extractions: list[dict], *, directed: bool = False) -> nx.Graph:
    """Merge multiple extraction results into one graph.

    directed=True produces a DiGraph that preserves edge direction (source→target).
    directed=False (default) produces an undirected Graph for backward compatibility.

    Extractions are merged in order. For nodes with the same ID, the last
    extraction's attributes win (NetworkX add_node overwrites). Pass AST
    results before semantic results so semantic labels take precedence, or
    reverse the order if you prefer AST source_location precision to win.
    """
    combined: dict = {"nodes": [], "edges": [], "hyperedges": [], "input_tokens": 0, "output_tokens": 0}
    for ext in extractions:
        combined["nodes"].extend(ext.get("nodes", []))
        combined["edges"].extend(ext.get("edges", []))
        combined["hyperedges"].extend(ext.get("hyperedges", []))
        combined["input_tokens"] += ext.get("input_tokens", 0)
        combined["output_tokens"] += ext.get("output_tokens", 0)
    return build_from_json(combined, directed=directed)
