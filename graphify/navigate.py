# Cursor-based graph navigation: dense affordance frames over graph.json.
#
# Default mode: ephemeral cursor with auto-generated session id. Each call
# generates a short id, persists the cursor under it (graph_dir/.navigate/<id>.json),
# prints the id, and sweeps stale ids (>30min) on the way in. Pass that id back
# via `--session <id>` (or session="<id>" in Python) to resume the cursor on a
# later call. Default chains stay one-shot — the next call without --session
# gets a fresh id and a fresh cursor. Per-id files mean parallel agents don't
# race on shared state.
#
# Pass session=False to suppress all disk activity (no id, no persist, no print).
#
# Op chain: each call processes a sequence of ops left-to-right. Examples:
#   ["@GeometryAnalyzer"]                         → focus, return frontier
#   ["@GeometryAnalyzer", "methods"]              → focus + list methods
#   ["@GeometryAnalyzer", "methods", "1"]         → focus + methods + pick #1
#   ["@GeometryAnalyzer", "methods", "1", "in"]   → ... + show callers of #1
#
# The result rendered is the last op's output (or the current frontier if the
# last op was a focus/back).
from __future__ import annotations
import json
import re
import secrets
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from difflib import get_close_matches
from pathlib import Path
from typing import Any
import networkx as nx

from graphify.build import build_from_json

CURSOR_DIR = ".navigate"
DEFAULT_GRAPH_PATH = "graphify-out/graph.json"
LIST_LIMIT = 25
STALE_AGE_SECONDS = 30 * 60  # 30 min: sweep cursor files older than this

# Edge relations representing structural parenthood, not semantic flow.
# Excluded from ↗in/↘out so those pivots show real callers/callees only.
_STRUCTURAL = ("method", "contains", "rationale_for", "inherits")


# --- loading ---------------------------------------------------------------

def load_graph(graph_path: str | Path) -> tuple[nx.DiGraph, dict[int, list[str]]]:
    """Load graph.json as a DiGraph plus a community→[node_ids] map.

    Also stamps each community's top-degree node label onto the DiGraph as
    `G.graph['community_labels']` — used by the renderer to print
    `c5=ExoticGeometryFramework` instead of bare `c5`.
    """
    path = Path(graph_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    G = build_from_json(data, directed=True)
    communities: dict[int, list[str]] = defaultdict(list)
    for nid, attrs in G.nodes(data=True):
        cid = attrs.get("community")
        if cid is not None:
            communities[cid].append(nid)
    # Auto-name communities by their highest-degree member. Skip rationale
    # nodes (they're docstrings/comments — labels would be sentence-shaped
    # rather than identifier-shaped) and cap label length so it stays compact.
    community_labels: dict[int, str] = {}
    for cid, members in communities.items():
        ranked = sorted(
            (m for m in members
             if G.nodes[m].get("file_type") != "rationale"),
            key=lambda n: -(G.in_degree(n) + G.out_degree(n)),
        )
        if not ranked:
            continue
        raw = G.nodes[ranked[0]].get("label", ranked[0])
        # Strip whitespace, collapse multi-line, cap length so the label
        # doesn't blow out frontier/listing line budgets.
        clean = " ".join(str(raw).split())
        if len(clean) > 28:
            clean = clean[:27] + "…"
        community_labels[cid] = clean
    G.graph["community_labels"] = community_labels
    return G, dict(communities)


def _community_tag(G: nx.DiGraph, cid: int | str | None) -> str:
    """Render community as `c<cid>=<auto-name>` when a name is available, else `c<cid>`."""
    if cid is None or cid == "?" or cid == -1:
        return f"c{cid}"
    labels = G.graph.get("community_labels") if hasattr(G, "graph") else None
    if labels and cid in labels:
        return f"c{cid}={labels[cid]}"
    return f"c{cid}"


def _norm(s: str) -> str:
    """Casefold + NFC-normalize for resolution. macOS filesystems hand back NFD
    (`H` + combining acute), but agents type NFC (`Hé`) — without normalization,
    `Hénon` from a path won't match `Hénon` typed by hand and `difflib` silently
    fails. Normalize on both sides.
    """
    return unicodedata.normalize("NFC", s).lower()


def label_index(G: nx.DiGraph) -> dict[str, list[str]]:
    """Normalized label/id → list of node ids. Used to resolve `@<label>`."""
    idx: dict[str, list[str]] = defaultdict(list)
    for nid, attrs in G.nodes(data=True):
        label = attrs.get("label", nid)
        idx[_norm(label)].append(nid)
        idx[_norm(nid)].append(nid)
    return dict(idx)


# --- cursor ----------------------------------------------------------------

@dataclass
class Cursor:
    current: str | None = None
    history: list[str] = field(default_factory=list)
    last_listing: list[str] = field(default_factory=list)
    last_pivot: str | None = None
    graph_path: str = DEFAULT_GRAPH_PATH
    created_at: float = 0.0  # epoch seconds; 0 means never persisted

    @classmethod
    def load(cls, path: Path) -> "Cursor":
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                allowed = cls.__annotations__
                return cls(**{k: v for k, v in data.items() if k in allowed})
            except Exception:
                return cls()
        return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.created_at = time.time()
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    def push(self, nid: str) -> None:
        if self.current and self.current != nid:
            self.history.append(self.current)
            if len(self.history) > 50:
                self.history = self.history[-50:]
        self.current = nid

    def pop(self) -> str | None:
        if not self.history:
            return None
        self.current = self.history.pop()
        return self.current


def _new_session_id() -> str:
    """Short random session id. 6 hex chars = ~16M distinct values, plenty under
    a 30-minute TTL. Keeps the printed token economy tight."""
    return secrets.token_hex(3)


def _sweep_stale(navigate_dir: Path) -> None:
    """Delete cursor files older than STALE_AGE_SECONDS. Best-effort."""
    if not navigate_dir.exists():
        return
    cutoff = time.time() - STALE_AGE_SECONDS
    for f in navigate_dir.glob("*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def _cursor_path(graph_path: Path, session_id: str) -> Path:
    return graph_path.parent / CURSOR_DIR / f"{session_id}.json"


# --- formatting helpers ----------------------------------------------------

def _short_src(src: str | None, loc: str | None) -> str:
    if not src:
        return "?"
    parts = Path(src).parts
    short = "/".join(parts[-2:]) if len(parts) >= 2 else parts[-1]
    if loc and loc.startswith("L"):
        return f"{short}:{loc[1:]}"
    return short


# In-process cache for per-file metadata (mtime, line count, git mtime). The
# same file appears under many nodes — `_node_summary` is called once per
# rendered item, but we only want one stat per file per `navigate` call.
_META_CACHE: dict[str, dict] = {}
_GIT_LOG_CACHE: dict[str, dict[str, int]] = {}  # repo_root → {file: last_commit_unixtime}
_GRAPH_MTIME: float = 0.0  # set per-call so per-file staleness can be flagged


def _file_meta(src: str | None) -> dict:
    """Best-effort per-file metadata: filesystem mtime, line count, git last-commit time.

    Cached per file per process — multiple nodes in the same listing pointing
    at the same file pay the stat cost once. Returns {} on missing file or
    permission error so renderers can degrade gracefully.
    """
    if not src:
        return {}
    if src in _META_CACHE:
        return _META_CACHE[src]
    out: dict = {}
    try:
        st = Path(src).stat()
        out["mtime"] = st.st_mtime
        out["size"] = st.st_size
    except OSError:
        _META_CACHE[src] = out
        return out
    # Line count is small for typical source — read once, cache. Skip for
    # very large files (>1MB) to avoid surprises.
    if out.get("size", 0) <= 1_000_000:
        try:
            with open(src, "rb") as f:
                out["lines"] = sum(1 for _ in f)
        except OSError:
            pass
    # Git last-commit time, if we can find one. Keyed off the repo root.
    git_t = _git_last_commit_time(src)
    if git_t is not None:
        out["git_mtime"] = git_t
    _META_CACHE[src] = out
    return out


def _git_last_commit_time(src: str) -> int | None:
    """Last-commit unixtime for `src` in its containing git repo, if any.

    Probes once per repo: a single `git log --format=%at --name-only HEAD`
    yields a {file: last_commit_unixtime} map for every tracked file. Cheap
    relative to running `git log -1 -- <file>` per node (which would be N
    forks for N nodes). Falls back silently if anything goes wrong.
    """
    import os as _os
    import subprocess as _sp
    abspath = _os.path.abspath(src)
    cur = _os.path.dirname(abspath)
    repo_root = None
    while cur and cur != "/":
        if _os.path.isdir(_os.path.join(cur, ".git")):
            repo_root = cur
            break
        nxt = _os.path.dirname(cur)
        if nxt == cur:
            break
        cur = nxt
    if not repo_root:
        return None
    if repo_root not in _GIT_LOG_CACHE:
        _GIT_LOG_CACHE[repo_root] = {}
        try:
            # `git log --name-only --format=%at` yields blocks of:
            #   <unixtime>
            #   path/one
            #   path/two
            # for each commit. We only keep the latest (first) sighting per file.
            r = _sp.run(
                ["git", "-C", repo_root, "log", "--name-only", "--format=%at", "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                cur_t = 0
                for line in r.stdout.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.isdigit():
                        cur_t = int(line)
                        continue
                    if cur_t and line not in _GIT_LOG_CACHE[repo_root]:
                        _GIT_LOG_CACHE[repo_root][line] = cur_t
        except (_sp.TimeoutExpired, FileNotFoundError, OSError):
            pass
    rel = _os.path.relpath(abspath, repo_root)
    return _GIT_LOG_CACHE[repo_root].get(rel)


def _humanize_age(epoch: float) -> str:
    """Compact relative time: 1h, 3d, 2mo, 1y. Empty for unknown."""
    if not epoch:
        return ""
    delta = time.time() - epoch
    if delta < 0:
        return ""
    if delta < 3600:
        return f"{int(delta // 60)}m"
    if delta < 86400:
        return f"{int(delta // 3600)}h"
    if delta < 86400 * 30:
        return f"{int(delta // 86400)}d"
    if delta < 86400 * 365:
        return f"{int(delta // (86400 * 30))}mo"
    return f"{int(delta // (86400 * 365))}y"


def _bin_confidence(edges: list[dict]) -> str:
    """Compress edge list to '11ext, 3inf@0.4-0.6'."""
    ext = sum(1 for e in edges if e.get("confidence") == "EXTRACTED")
    inf = [e for e in edges if e.get("confidence") == "INFERRED"]
    amb = sum(1 for e in edges if e.get("confidence") == "AMBIGUOUS")
    parts: list[str] = []
    if ext:
        parts.append(f"{ext}ext")
    if inf:
        scores = [e.get("confidence_score") for e in inf
                  if isinstance(e.get("confidence_score"), (int, float))]
        if scores:
            lo, hi = min(scores), max(scores)
            parts.append(f"{len(inf)}inf@{lo:.1f}-{hi:.1f}")
        else:
            parts.append(f"{len(inf)}inf")
    if amb:
        parts.append(f"{amb}amb")
    return ", ".join(parts) or "·"


def _label(G: nx.DiGraph, nid: str, max_len: int = 38) -> str:
    raw = G.nodes[nid].get("label", nid)
    s = " ".join(raw.split())
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def _file_letter(i: int) -> str:
    """0→a, 1→b, ..., 25→z, 26→aa, ..."""
    s = ""
    n = i
    while True:
        s = chr(ord("a") + n % 26) + s
        n = n // 26 - 1
        if n < 0:
            return s


# --- filters ---------------------------------------------------------------

def _passes_confidence(edge: dict, extracted_only: bool, min_confidence: float | None) -> bool:
    """Edge filter for --extracted-only and --min-confidence."""
    if extracted_only and edge.get("confidence") != "EXTRACTED":
        return False
    if min_confidence is not None:
        if edge.get("confidence") == "EXTRACTED":
            return True  # extracted edges are ground truth, score-exempt
        score = edge.get("confidence_score")
        if not isinstance(score, (int, float)) or score < min_confidence:
            return False
    return True


def _drop_breakdown(edges: list[dict], extracted_only: bool,
                    min_confidence: float | None) -> dict[str, int]:
    """Count how many edges the current filter would drop, by reason.

    Used to surface omissions to the agent — `↗in(0 / +462inf hidden)` is
    far more informative than `↗in(0)` when the filter is active.
    """
    inferred_hidden = 0
    low_conf_hidden = 0
    for e in edges:
        if _passes_confidence(e, extracted_only, min_confidence):
            continue
        if extracted_only and e.get("confidence") != "EXTRACTED":
            inferred_hidden += 1
        elif min_confidence is not None and e.get("confidence") != "EXTRACTED":
            score = e.get("confidence_score")
            if not isinstance(score, (int, float)) or score < min_confidence:
                low_conf_hidden += 1
    return {"inferred": inferred_hidden, "low_confidence": low_conf_hidden}


def _kind_drops(edges: list[dict], kinds: set[str] | None) -> dict[str, int]:
    """When --kind is active, count what each *excluded* relation contributed.

    Returned shape: `{"<rel>": count, ...}`. Lets the renderer say
    `+12 [uses,imports] hidden via --kind` so the agent knows what they
    asked the filter to drop. Empty when kinds is None (filter inactive).
    """
    if not kinds:
        return {}
    out: dict[str, int] = defaultdict(int)
    for e in edges:
        rel = e.get("relation") or ""
        if rel and rel not in kinds:
            out[rel] += 1
    return dict(out)


def _format_hidden(drops: dict[str, int]) -> str:
    """Render a drop breakdown as ' / +Ninf hidden, +Mlc hidden' or '' if none."""
    bits = []
    if drops.get("inferred"):
        bits.append(f"+{drops['inferred']}inf hidden")
    if drops.get("low_confidence"):
        bits.append(f"+{drops['low_confidence']}lc hidden")
    by_rel = drops.get("by_rel") or {}
    if by_rel:
        total = sum(by_rel.values())
        rels = ",".join(sorted(by_rel.keys()))
        bits.append(f"+{total} [{rels}] kind-hidden")
    return f" / {', '.join(bits)}" if bits else ""


# --- structured data builders ----------------------------------------------

def _node_summary(G: nx.DiGraph, nid: str) -> dict:
    a = G.nodes[nid]
    src = a.get("source_file")
    meta = _file_meta(src) if src else {}
    cid = a.get("community")
    labels = (G.graph.get("community_labels") if hasattr(G, "graph") else None) or {}
    return {
        "id": nid,
        "label": a.get("label", nid),
        "community": cid,
        "community_label": labels.get(cid) if cid is not None else None,
        "degree": G.in_degree(nid) + G.out_degree(nid),
        "source_file": src,
        "source_location": a.get("source_location"),
        "file_type": a.get("file_type", ""),
        # Per-file metadata — saves the agent from running stat/git for the
        # most common follow-up questions ("how stale is this?", "how big?").
        "mtime": meta.get("mtime"),
        "git_mtime": meta.get("git_mtime"),
        "lines": meta.get("lines"),
        "size": meta.get("size"),
    }


def _meta_tag(item: dict) -> str:
    """Compact ' · 3d · 482ln' suffix for a node's file metadata.

    Prefers git last-commit time (semantically meaningful "when did this
    actually change") over fs mtime ("when did I last touch it locally").
    Line count is added for code files only and only when known. Returns ''
    if no metadata is available so callers can append unconditionally.

    Also surfaces a `!stale` marker when the file's mtime is newer than the
    graph's mtime — strong signal that the graph doesn't reflect this file's
    current state and `graphify update .` may be due.
    """
    parts: list[str] = []
    age_src = item.get("git_mtime") or item.get("mtime")
    if age_src:
        age = _humanize_age(age_src)
        if age:
            tag = age
            if item.get("git_mtime"):
                tag = "g" + tag  # `g3d` distinguishes git mtime from local fs mtime
            parts.append(tag)
    lines = item.get("lines")
    if isinstance(lines, int) and lines > 0:
        parts.append(f"{lines}ln")
    fs_mtime = item.get("mtime")
    if fs_mtime and _GRAPH_MTIME and fs_mtime > _GRAPH_MTIME:
        parts.append("!stale")
    return (" · " + " · ".join(parts)) if parts else ""


def _frontier_data(G: nx.DiGraph, communities: dict[int, list[str]],
                   cursor: Cursor, *, extracted_only: bool,
                   min_confidence: float | None,
                   show_history: bool) -> dict:
    if not cursor.current or cursor.current not in G:
        return {"type": "frontier", "current": None,
                "message": "no cursor — focus a node with @<label>"}

    nid = cursor.current
    node = _node_summary(G, nid)

    out_edges = [G.edges[nid, v] for v in G.successors(nid)]
    in_edges = [G.edges[u, nid] for u in G.predecessors(nid)]

    def f(es): return [e for e in es if _passes_confidence(e, extracted_only, min_confidence)]

    # Unfiltered slices (for drop-count computation)
    methods_all = [e for e in out_edges if e.get("relation") == "method"]
    inh_all = [e for e in out_edges if e.get("relation") == "inherits"]
    contains_all = [e for e in out_edges if e.get("relation") == "contains"]
    out_semantic_all = [e for e in out_edges if e.get("relation") not in _STRUCTURAL]
    in_semantic_all = [e for e in in_edges if e.get("relation") not in _STRUCTURAL]
    parent_all = [e for e in in_edges if e.get("relation") in ("contains", "method")]

    methods = f(methods_all)
    inh = f(inh_all)
    contains = f(contains_all)
    out_semantic = f(out_semantic_all)
    in_semantic = f(in_semantic_all)
    parent_edges = f(parent_all)

    # rationale anchors (kept as set, no drop-count tracking — rat is rarely
    # filter-affected since rationale_for edges are EXTRACTED in practice)
    rat = set()
    rat_all_count = 0
    for u in G.predecessors(nid):
        if (G.nodes[u].get("file_type") == "rationale"
                or G.edges[u, nid].get("relation") == "rationale_for"):
            rat_all_count += 1
            if _passes_confidence(G.edges[u, nid], extracted_only, min_confidence):
                rat.add(u)
    for v in G.successors(nid):
        if G.edges[nid, v].get("relation") == "rationale_for":
            rat_all_count += 1
            if _passes_confidence(G.edges[nid, v], extracted_only, min_confidence):
                rat.add(v)

    cid = node["community"] if node["community"] is not None else -1
    coc_size = max(0, len(communities.get(cid, [])) - 1) if cid != -1 else 0

    # Class-level inbound rollup: when this node has method-out neighbors, sum
    # the *unique* callers across all of them (deduped) and report alongside
    # the direct in-count. A class node with `↗in(0)` is misleading when its
    # methods are called from 50 places — surface that.
    via_methods = 0
    if methods_all:  # only matters for class-shaped nodes
        callers: set[str] = set()
        for e in methods:  # filter-respecting method out-edges only
            v = None
            # methods is a list of edges; we need the destination node ids,
            # which we already collected separately for the listing pivot.
            # Re-derive cheaply: any successor with relation=method passing filters.
        # Easier: walk successors directly so we have the node id in hand.
        for v in G.successors(nid):
            e_mv = G.edges[nid, v]
            if (e_mv.get("relation") != "method"
                    or not _passes_confidence(e_mv, extracted_only, min_confidence)):
                continue
            for u in G.predecessors(v):
                if u == nid:
                    continue
                e_uv = G.edges[u, v]
                if (e_uv.get("relation") not in _STRUCTURAL
                        and _passes_confidence(e_uv, extracted_only, min_confidence)):
                    callers.add(u)
        via_methods = len(callers)

    # Sibling count: union of parents' contained children minus self.
    # Approximate but accurate for the common case of single-parent containment.
    sib_set: set[str] = set()
    for u in G.predecessors(nid):
        e_pu = G.edges[u, nid]
        if (e_pu.get("relation") in ("contains", "method")
                and _passes_confidence(e_pu, extracted_only, min_confidence)):
            for v in G.successors(u):
                if v == nid:
                    continue
                e_uv = G.edges[u, v]
                if (e_uv.get("relation") in ("contains", "method")
                        and _passes_confidence(e_uv, extracted_only, min_confidence)):
                    sib_set.add(v)
    sib_count = len(sib_set)

    def pivot_summary(name: str, edges: list[dict], edges_all: list[dict]) -> dict:
        return {
            "name": name,
            "count": len(edges),
            "extracted": sum(1 for e in edges if e.get("confidence") == "EXTRACTED"),
            "inferred": sum(1 for e in edges if e.get("confidence") == "INFERRED"),
            "confidence_text": _bin_confidence(edges),
            "drops": _drop_breakdown(edges_all, extracted_only, min_confidence),
        }

    def cnt(name: str, edges: list[dict], edges_all: list[dict]) -> dict:
        return {
            "name": name,
            "count": len(edges),
            "drops": _drop_breakdown(edges_all, extracted_only, min_confidence),
        }

    in_pivot = pivot_summary("in", in_semantic, in_semantic_all)
    if via_methods > 0:
        in_pivot["via_methods"] = via_methods
    return {
        "type": "frontier",
        "current": node,
        "pivots": {
            "in": in_pivot,
            "out": pivot_summary("out", out_semantic, out_semantic_all),
            "methods": cnt("methods", methods, methods_all),
            "contains": cnt("contains", contains, contains_all),
            "coc": {"name": "coc", "count": coc_size, "community_id": cid, "drops": {}},
            "rat": {"name": "rat", "count": len(rat),
                    "drops": {"inferred": max(0, rat_all_count - len(rat))} if extracted_only else {}},
            "inh": cnt("inh", inh, inh_all),
            "parent": cnt("parent", parent_edges, parent_all),
            "siblings": {"name": "siblings", "count": sib_count, "drops": {}},
        },
        "history_depth": len(cursor.history),
        "last_pivot": cursor.last_pivot,
        "last_listing_size": len(cursor.last_listing),
        "show_history": show_history,
    }


def _listing_data(G: nx.DiGraph, ids: list[str], pivot_name: str,
                  edge_for: dict[str, dict] | None, total: int,
                  sort_label: str, limit: int,
                  drops: dict[str, int] | None = None,
                  kinds: set[str] | None = None,
                  bodies: int | None = None) -> dict:
    items = []
    for nid in ids[:limit]:
        item = _node_summary(G, nid)
        if edge_for is not None and nid in edge_for:
            e = edge_for[nid]
            item["edge"] = {
                "relation": e.get("relation"),
                "confidence": e.get("confidence"),
                "confidence_score": e.get("confidence_score"),
            }
        if bodies and bodies > 0:
            preview = _read_body_preview(item.get("source_file"),
                                         item.get("source_location"),
                                         bodies)
            if preview:
                item["body_preview"] = preview
        items.append(item)
    return {
        "type": "listing",
        "pivot": pivot_name,
        "total": total,
        "showing": min(total, limit),
        "sort": sort_label,
        "items": items,
        "drops": drops or {},
        "kinds": sorted(kinds) if kinds else None,
        "bodies": bodies,
    }


def _read_body_preview(source_file: str | None, source_location: str | None,
                       n: int) -> list[str]:
    """Read the first `n` non-blank source lines starting at source_location.

    Best-effort: returns [] on missing file / unreadable / no usable location.
    Stops at the next dedent past the first body line so the preview doesn't
    bleed into the next sibling. The preview is meant to surface stubs and
    one-line redirects, not full bodies.
    """
    if not source_file or not source_location or n <= 0:
        return []
    loc = source_location
    line_no: int | None = None
    if loc.startswith("L"):
        try:
            line_no = int(loc[1:].split("-", 1)[0].split(":", 1)[0])
        except ValueError:
            return []
    if line_no is None:
        return []
    try:
        # Limit read window — function bodies past 200 lines aren't preview material
        with open(source_file, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return []
    start = max(0, line_no - 1)
    if start >= len(lines):
        return []
    # First, find the def/class line indent so we know where the body starts.
    header = lines[start]
    header_indent = len(header) - len(header.lstrip(" \t"))
    body_indent: int | None = None
    out: list[str] = []
    for raw in lines[start:start + 200]:
        stripped = raw.rstrip()
        if not stripped.strip():
            continue
        cur_indent = len(stripped) - len(stripped.lstrip(" \t"))
        # First non-blank past the header sets the body's indent baseline.
        if body_indent is None:
            if raw is header or len(out) == 0:
                # Always include the header itself as the first preview line —
                # signature is part of the orientation (decorator-only,
                # multi-line def, etc).
                out.append(stripped)
                continue
            if cur_indent > header_indent:
                body_indent = cur_indent
                out.append(stripped)
                if len(out) >= n:
                    break
                continue
            # If the next non-blank is at or below the header's indent, the
            # function body is empty (or the header was multi-line) — bail.
            break
        # Subsequent body lines: stop if we've dedented past the body baseline.
        if cur_indent < body_indent:
            break
        out.append(stripped)
        if len(out) >= n:
            break
    return out


# --- text renderers --------------------------------------------------------

LEGEND = (
    "  legend: c<N>=community · d=<degree> · src=<file>:<line> · "
    "ext=EXTRACTED edge (AST ground truth) · inf@<lo>-<hi>=INFERRED with score range · "
    "[N]=pick from listing · @<label>=focus · "
    "ops: in out methods contains coc rat inh parent siblings back reset"
)


def _render_frontier_text(data: dict, cursor: Cursor, *, show_ops: bool) -> str:
    if data.get("current") is None:
        return data.get("message", "no cursor.")

    n = data["current"]
    cid = n.get("community", "?")
    clabel = n.get("community_label")
    cstr = f"c{cid}={clabel}" if clabel else f"c{cid}"
    src = _short_src(n.get("source_file"), n.get("source_location"))
    ftype_tag = ""
    ft = n.get("file_type", "")
    if ft and ft != "code":
        ftype_tag = f" [{ft}]"

    p = data["pivots"]
    header = f"@ {n['label']}  · {cstr} · deg={n['degree']} · {src}{ftype_tag}{_meta_tag(n)}"

    def _glyph(prefix: str, pv: dict, with_conf: bool = False) -> str:
        # Format: glyph(count[: conf-mix][; +Ninf hidden][; +M via methods])
        # Always show the drop-count when filtering hides edges, so the agent
        # never thinks a 0-count means "nothing exists" when it really means
        # "nothing matches the current filter".
        # The via_methods rollup specifically guards against the trust-bug
        # case: `↗in(0)` on a class whose methods are called from 50 places
        # would suggest "nobody uses this", which is wrong.
        c = pv["count"]
        hidden = _format_hidden(pv.get("drops", {})).lstrip(" /").strip(", ").strip()
        # _format_hidden returns " / +Xinf hidden" — normalise to "; +Xinf hidden"
        hidden_suffix = f"; {hidden}" if hidden else ""
        via = pv.get("via_methods")
        via_suffix = f"; +{via} via methods" if via else ""
        if with_conf and c:
            return f"{prefix}({c}: {pv['confidence_text']}{hidden_suffix}{via_suffix})"
        suffix = hidden_suffix + via_suffix
        if suffix:
            return f"{prefix}({c}{suffix})"
        return f"{prefix}({c})"

    line_a = "  ".join([
        _glyph("↗in", p['in'], with_conf=True),
        _glyph("↘out", p['out'], with_conf=True),
        _glyph("◉methods", p['methods']),
        _glyph("◇contains", p['contains']),
    ])
    line_b_parts = [
        f"⊕coc({p['coc']['count']})",
        _glyph("←rat", p['rat']),
        _glyph("→inh", p['inh']),
        _glyph("⇡parent", p['parent']),
    ]
    if p.get("siblings", {}).get("count"):
        line_b_parts.append(f"◈sib({p['siblings']['count']})")
    if data.get("show_history"):
        line_b_parts.append(f"↺({data['history_depth']})")
    line_b = "  ".join(line_b_parts)

    out = [header, "  " + line_a, "  " + line_b]

    # Steering hint: if the focus node has no semantic edges but is structurally
    # parental, point at `contains`/`methods` so the agent doesn't bounce off
    # an apparently-empty file or class. Files-as-nodes are script leaves —
    # the actual call edges live one hop in via `contains`. Tell the agent to
    # drill, not just pivot.
    cur = data.get("current") or {}
    is_code_file = cur.get("file_type") == "code"
    if p['in']['count'] == 0 and p['out']['count'] == 0:
        if p['contains']['count'] > 0:
            kind = ("script-leaf — call edges live in the contained nodes"
                    if is_code_file else "no direct edges")
            out.append(
                f"  hint: {kind}. drill: `contains` ({p['contains']['count']}) "
                f"→ pick a function → then `out`."
            )
        elif p['methods']['count'] > 0:
            via = p['in'].get('via_methods', 0)
            if via:
                out.append(
                    f"  hint: class-shaped — direct callers=0 but {via} reach via "
                    f"its methods. drill: `methods` ({p['methods']['count']}) → "
                    f"pick a method → then `in`."
                )
            else:
                out.append(
                    f"  hint: no direct call edges. drill: `methods` "
                    f"({p['methods']['count']}) → pick a method → then `in`/`out`."
                )
        elif (p['contains']['count'] == 0 and p['methods']['count'] == 0
              and p['parent']['count'] == 0 and p['rat']['count'] == 0
              and p['inh']['count'] == 0):
            # Truly orphaned: no parent, no children, no rationale, no inheritance.
            # Often a top-level constant, isolated import, or genuinely dead node.
            # The agent should pivot through `coc` (Leiden cluster) for context
            # rather than poking at empty pivots.
            coc_n = p.get('coc', {}).get('count', 0)
            if coc_n > 0:
                out.append(
                    f"  hint: orphan — no in/out/parent/contains/methods. "
                    f"closest context is its community ({coc_n} members) — try `coc`."
                )
            else:
                out.append(
                    "  hint: fully isolated — no edges and no community peers. "
                    "graph likely has stale extraction; consider `graphify update .`."
                )
    elif p['methods']['count'] > 0 and p['in'].get('via_methods', 0) > p['in']['count']:
        # Has direct callers but methods see far more — class-shape signal.
        # Surface the rollup so the agent doesn't anchor on the direct count.
        out.append(
            f"  hint: class-shaped — {p['in']['count']} direct callers vs "
            f"{p['in']['via_methods']} via methods. `methods` then `in` reveals the wider call graph."
        )

    if data.get("last_listing_size") and data.get("last_pivot"):
        out.append(f"  last: {data['last_pivot']}({data['last_listing_size']}) · pick [N]")
    if show_ops:
        ops_line = "  ops: in | out | methods | contains | coc | rat | inh | parent | siblings"
        if data.get("show_history"):
            ops_line += " | back | reset"
        ops_line += " | @<label> | [N]"
        out.append(ops_line)
    return "\n".join(out)


def _render_listing_text(data: dict, *, show_ops: bool) -> str:
    if data["total"] == 0:
        # Even on an empty result, surface the drop count — `in: empty` after
        # extracted-only filtering would otherwise hide that 462 inferred edges
        # were dropped, which is exactly the orientation the agent needs.
        drops = data.get("drops") or {}
        bits = []
        if drops.get("inferred"):
            bits.append(f"+{drops['inferred']} INFERRED hidden")
        if drops.get("low_confidence"):
            bits.append(f"+{drops['low_confidence']} below --min-confidence")
        by_rel = drops.get("by_rel") or {}
        if by_rel:
            total = sum(by_rel.values())
            rels = ",".join(f"{r}:{c}" for r, c in sorted(by_rel.items(), key=lambda x: -x[1]))
            bits.append(f"+{total} hidden via --kind ({rels})")
        suffix = f"  ({', '.join(bits)}, drop the filter to show)" if bits else ""
        kinds_tag = ""
        if data.get("kinds"):
            kinds_tag = f" [--kind={','.join(data['kinds'])}]"
        return f"  {data['pivot']}{kinds_tag}: empty{suffix}"

    items = data["items"]

    # Build file-table only if it actually saves tokens: at least one file
    # must repeat across multiple rows.
    file_to_letter: dict[str, str] = {}
    file_use_count: dict[str, int] = defaultdict(int)
    for item in items:
        src = item.get("source_file") or ""
        if src:
            file_use_count[src] += 1
            if src not in file_to_letter:
                file_to_letter[src] = _file_letter(len(file_to_letter))
    has_repeat = any(c >= 2 for c in file_use_count.values())
    use_table = has_repeat and len(items) >= 3

    out: list[str] = []
    kinds_tag = ""
    if data.get("kinds"):
        kinds_tag = f" [--kind={','.join(data['kinds'])}]"
    header = f"  {data['pivot']}{kinds_tag} ({data['total']})"
    if data["total"] > data["showing"]:
        sort = data.get("sort") or ""
        if sort:
            header += f" — top {data['showing']} by {sort}"
        else:
            header += f" — showing first {data['showing']}"
    drops = data.get("drops") or {}
    drop_bits = []
    if drops.get("inferred"):
        drop_bits.append(f"+{drops['inferred']} INFERRED hidden")
    if drops.get("low_confidence"):
        drop_bits.append(f"+{drops['low_confidence']} below --min-confidence")
    by_rel = drops.get("by_rel") or {}
    if by_rel:
        total = sum(by_rel.values())
        rels = ",".join(f"{r}:{c}" for r, c in sorted(by_rel.items(), key=lambda x: -x[1]))
        drop_bits.append(f"+{total} hidden via --kind ({rels})")
    if drop_bits:
        header += f"  ({', '.join(drop_bits)})"
    out.append(header)

    if use_table:
        # Only emit letters that are actually referenced (suppression of
        # singletons isn't useful if they appear only once — they'd still
        # take a row in the table, so just elide the table for them).
        out.append("  files:")
        # Hoist file metadata (age, line count) onto the table row when present
        # so it's not repeated under every item that shares that file.
        per_file_meta: dict[str, dict] = {}
        for it in items:
            sf = it.get("source_file") or ""
            if sf and sf not in per_file_meta:
                per_file_meta[sf] = {
                    "git_mtime": it.get("git_mtime"),
                    "mtime": it.get("mtime"),
                    "lines": it.get("lines"),
                }
        for src, letter in file_to_letter.items():
            short = "/".join(Path(src).parts[-2:]) if Path(src).parts else src
            meta_str = _meta_tag(per_file_meta.get(src, {}))
            out.append(f"    [{letter}] {short}{meta_str}")

    # Mark the trust boundary: items are sorted EXTRACTED-first, so the
    # transition from EXTRACTED to non-EXTRACTED is the line below which the
    # agent should scrutinise more carefully. Only meaningful when both
    # appear (default is extracted-only, but --include-inferred can mix).
    boundary_inserted = False
    prev_was_extracted: bool | None = None
    for i, item in enumerate(items, start=1):
        src = item.get("source_file") or ""
        loc = item.get("source_location") or ""
        loc_num = loc[1:] if loc.startswith("L") else loc

        if use_table and src in file_to_letter:
            src_str = f"{file_to_letter[src]}:{loc_num}" if loc_num else file_to_letter[src]
        else:
            src_str = _short_src(src, loc) if src else "?"

        ft = item.get("file_type", "")
        ft_tag = f" [{ft}]" if ft and ft != "code" else ""
        edge_tag = ""
        is_extracted = False
        if "edge" in item:
            e = item["edge"]
            rel = e.get("relation", "?")
            confidence = e.get("confidence") or "?"
            is_extracted = confidence == "EXTRACTED"
            conf = confidence.lower()[:3]
            score = e.get("confidence_score")
            if isinstance(score, (int, float)) and conf == "inf":
                conf = f"inf@{score:.2f}"
            edge_tag = f" [{rel}/{conf}]"
        if (not boundary_inserted and "edge" in item
                and prev_was_extracted is True and not is_extracted):
            out.append("    ── inferred below ──")
            boundary_inserted = True
        if "edge" in item:
            prev_was_extracted = is_extracted
        label = (item.get("label") or item["id"])
        if len(label) > 44:
            label = label[:43] + "…"
        cid = item.get("community", -1)
        deg = item.get("degree", 0)
        # Only show per-item meta when the file-table didn't already absorb it.
        meta = "" if (use_table and src in file_to_letter) else _meta_tag(item)
        out.append(f"    [{i:>2}] {label:<44} c{cid:<3} d={deg:<4} {src_str}{ft_tag}{edge_tag}{meta}")
        # Optional body preview: surfaces stub/redirect/decorator-only patterns
        # without an actual Read. Truncate long lines so the preview doesn't
        # blow out the line budget; one signal per line is enough.
        for line in (item.get("body_preview") or []):
            shown = line if len(line) <= 90 else line[:89] + "…"
            out.append(f"         | {shown}")

    if data["total"] > data["showing"]:
        out.append(f"    … +{data['total'] - data['showing']} more  · raise --limit to see more")
    if show_ops:
        out.append("  pick: [N] to focus · @<label> jump")
    return "\n".join(out)


# --- pivot data sources ----------------------------------------------------

def _filtered_edges(edges_iter, extracted_only, min_confidence):
    return [e for e in edges_iter if _passes_confidence(e, extracted_only, min_confidence)]


def _rank_key(G: nx.DiGraph, nid: str, edge: dict) -> tuple[int, int, int]:
    ext_first = 0 if edge.get("confidence") == "EXTRACTED" else 1
    is_rat = 1 if G.nodes[nid].get("file_type") == "rationale" else 0
    deg = G.in_degree(nid) + G.out_degree(nid)
    return (ext_first, is_rat, -deg)


def _semantic_pass(rel: str, kinds: set[str] | None) -> bool:
    """Predicate for `in`/`out` pivots after pivot-internal structural exclusion.

    Without --kind: drop _STRUCTURAL edges (those have their own pivots).
    With --kind: ignore _STRUCTURAL list entirely; the user's allowlist is
    the source of truth, so `out --kind=contains` is honored even though
    contains is normally surfaced via the dedicated pivot.
    """
    if kinds is not None:
        return rel in kinds
    return rel not in _STRUCTURAL


def _transitive_walk(G: nx.DiGraph, nid: str, *,
                     direction: str,  # "out" | "in"
                     depth: int,
                     extracted_only: bool,
                     min_confidence: float | None,
                     kinds: set[str] | None) -> tuple[list[str], dict[str, dict]]:
    """BFS from nid along non-structural edges in `direction` for `depth` hops.

    Returns (ordered_ids, edge_for_dict) where edge_for is keyed by the
    *first* time we saw a node (the closest hop's edge). Cycles are skipped
    via a visited set. Used by `in`/`out` when `--depth > 1` is passed.
    """
    visited = {nid}
    frontier = {nid}
    found: list[str] = []
    edge_for: dict[str, dict] = {}
    for _ in range(max(1, depth)):
        new_frontier: set[str] = set()
        for u in frontier:
            iterable = (G.successors(u) if direction == "out"
                        else G.predecessors(u))
            for v in iterable:
                e = G.edges[u, v] if direction == "out" else G.edges[v, u]
                if not _semantic_pass(e.get("relation") or "", kinds):
                    continue
                if not _passes_confidence(e, extracted_only, min_confidence):
                    continue
                if v in visited:
                    continue
                visited.add(v)
                found.append(v)
                edge_for[v] = e
                new_frontier.add(v)
        frontier = new_frontier
        if not frontier:
            break
    return found, edge_for


def _pivot_data(G: nx.DiGraph, communities: dict[int, list[str]],
                cursor: Cursor, key: str, *,
                extracted_only: bool,
                min_confidence: float | None,
                kinds: set[str] | None = None,
                depth: int = 1
                ) -> tuple[str, list[str], dict[str, dict], str, dict[str, int]]:
    """Return (pivot_label, ordered_ids, edge_for_dict, sort_label, drops) for a pivot key.

    `drops` records what the current confidence filter excluded (inferred,
    low-confidence) so the renderer can surface it. Filters that are part
    of the pivot's semantics (e.g. `in` excluding _STRUCTURAL) aren't drops
    — those edges live under their own pivot (methods/contains/parent) and
    are reported there.

    `kinds`: optional allowlist of edge relation names. When set, `in`/`out`
    pivots restrict to those relations and surface kind-excluded counts so
    the agent sees what they filtered away.
    """
    nid = cursor.current
    if not nid:
        return ("", [], {}, "", {})

    if key == "in":
        # All non-structural in-edges, plus kind-drop visibility when --kind active
        full_semantic_in = [G.edges[u, nid] for u in G.predecessors(nid)
                            if G.edges[u, nid].get("relation") not in _STRUCTURAL]
        all_in = [G.edges[u, nid] for u in G.predecessors(nid)
                  if _semantic_pass(G.edges[u, nid].get("relation") or "", kinds)]
        if depth > 1:
            preds, edge_for = _transitive_walk(
                G, nid, direction="in", depth=depth,
                extracted_only=extracted_only, min_confidence=min_confidence,
                kinds=kinds,
            )
            preds.sort(key=lambda x: _rank_key(G, x, edge_for[x]))
            sort_label = f"transitive (depth≤{depth}), extracted-first, then degree desc"
        else:
            preds = [u for u in G.predecessors(nid)
                     if _semantic_pass(G.edges[u, nid].get("relation") or "", kinds)
                     and _passes_confidence(G.edges[u, nid], extracted_only, min_confidence)]
            edge_for = {u: G.edges[u, nid] for u in preds}
            preds.sort(key=lambda x: _rank_key(G, x, edge_for[x]))
            sort_label = "extracted-first, then degree desc"
        drops = _drop_breakdown(all_in, extracted_only, min_confidence)
        kd = _kind_drops(full_semantic_in, kinds)
        if kd:
            drops["by_rel"] = kd
        return ("↗in", preds, edge_for, sort_label, drops)

    if key == "out":
        full_semantic_out = [G.edges[nid, v] for v in G.successors(nid)
                             if G.edges[nid, v].get("relation") not in _STRUCTURAL]
        all_out = [G.edges[nid, v] for v in G.successors(nid)
                   if _semantic_pass(G.edges[nid, v].get("relation") or "", kinds)]
        if depth > 1:
            succs, edge_for = _transitive_walk(
                G, nid, direction="out", depth=depth,
                extracted_only=extracted_only, min_confidence=min_confidence,
                kinds=kinds,
            )
            succs.sort(key=lambda x: _rank_key(G, x, edge_for[x]))
            sort_label = f"transitive (depth≤{depth}), extracted-first, then degree desc"
        else:
            succs = [v for v in G.successors(nid)
                     if _semantic_pass(G.edges[nid, v].get("relation") or "", kinds)
                     and _passes_confidence(G.edges[nid, v], extracted_only, min_confidence)]
            edge_for = {v: G.edges[nid, v] for v in succs}
            succs.sort(key=lambda x: _rank_key(G, x, edge_for[x]))
            sort_label = "extracted-first, then degree desc"
        drops = _drop_breakdown(all_out, extracted_only, min_confidence)
        kd = _kind_drops(full_semantic_out, kinds)
        if kd:
            drops["by_rel"] = kd
        return ("↘out", succs, edge_for, sort_label, drops)

    if key == "methods":
        all_m = [G.edges[nid, v] for v in G.successors(nid)
                 if G.edges[nid, v].get("relation") == "method"]
        succs = [v for v in G.successors(nid)
                 if G.edges[nid, v].get("relation") == "method"
                 and _passes_confidence(G.edges[nid, v], extracted_only, min_confidence)]
        # Sort by degree desc so hub methods (the API surface) sort to the top.
        # Source-order made sense for small classes but loses information once
        # the class has 20+ methods — the agent ends up scanning for the
        # high-degree ones anyway.
        succs.sort(key=lambda x: -(G.in_degree(x) + G.out_degree(x)))
        return ("◉methods", succs, {v: G.edges[nid, v] for v in succs}, "degree desc",
                _drop_breakdown(all_m, extracted_only, min_confidence))

    if key == "contains":
        all_c = [G.edges[nid, v] for v in G.successors(nid)
                 if G.edges[nid, v].get("relation") == "contains"]
        succs = [v for v in G.successors(nid)
                 if G.edges[nid, v].get("relation") == "contains"
                 and _passes_confidence(G.edges[nid, v], extracted_only, min_confidence)]
        succs.sort(key=lambda x: -(G.in_degree(x) + G.out_degree(x)))
        return ("◇contains", succs, {v: G.edges[nid, v] for v in succs}, "degree desc",
                _drop_breakdown(all_c, extracted_only, min_confidence))

    if key == "inh":
        succs = [v for v in G.successors(nid)
                 if G.edges[nid, v].get("relation") == "inherits"
                 and _passes_confidence(G.edges[nid, v], extracted_only, min_confidence)]
        all_inh = [G.edges[nid, v] for v in G.successors(nid)
                   if G.edges[nid, v].get("relation") == "inherits"]
        return ("→inh", succs, {v: G.edges[nid, v] for v in succs}, "source order",
                _drop_breakdown(all_inh, extracted_only, min_confidence))

    if key == "parent":
        all_p = [G.edges[u, nid] for u in G.predecessors(nid)
                 if G.edges[u, nid].get("relation") in ("contains", "method")]
        preds = [u for u in G.predecessors(nid)
                 if G.edges[u, nid].get("relation") in ("contains", "method")
                 and _passes_confidence(G.edges[u, nid], extracted_only, min_confidence)]
        return ("⇡parent", preds, {u: G.edges[u, nid] for u in preds}, "source order",
                _drop_breakdown(all_p, extracted_only, min_confidence))

    if key in ("callers", "callees"):
        # Sugar for `in --kind=calls` / `out --kind=calls`. The single most
        # common edge filter — "who calls this?" / "who does this call?" —
        # deserves its own short op rather than a flag combination. We
        # recurse into the underlying in/out pivot with the kind allowlist
        # so the drop/rank/filter logic stays in one place.
        base_key = "in" if key == "callers" else "out"
        _pname, ids, edges, sort_label, drops = _pivot_data(
            G, communities, cursor, base_key,
            extracted_only=extracted_only,
            min_confidence=min_confidence,
            kinds={"calls"},
        )
        glyph = "◀callers" if key == "callers" else "▶callees"
        return (glyph, ids, edges, sort_label, drops)

    if key == "siblings":
        # Structural peers — other nodes that share at least one parent file or
        # class with the current node. Disjoint from `coc` (semantic Leiden
        # cluster) and useful for "what else is in this file?" / "what else does
        # this class have?" without pivoting through `parent` then `contains`.
        parents = [u for u in G.predecessors(nid)
                   if G.edges[u, nid].get("relation") in ("contains", "method")
                   and _passes_confidence(G.edges[u, nid], extracted_only, min_confidence)]
        sibs: list[str] = []
        seen = {nid}
        sib_edges: dict[str, dict] = {}
        for parent in parents:
            for v in G.successors(parent):
                if v in seen:
                    continue
                e = G.edges[parent, v]
                if (e.get("relation") in ("contains", "method")
                        and _passes_confidence(e, extracted_only, min_confidence)):
                    seen.add(v)
                    sibs.append(v)
                    sib_edges[v] = e
        sibs.sort(key=lambda x: -(G.in_degree(x) + G.out_degree(x)))
        return ("◈sib", sibs, sib_edges, "degree desc", {})

    if key == "rat":
        rat = set()
        all_rat_edges: list[dict] = []
        for u in G.predecessors(nid):
            e = G.edges[u, nid]
            if (G.nodes[u].get("file_type") == "rationale"
                    or e.get("relation") == "rationale_for"):
                all_rat_edges.append(e)
                if _passes_confidence(e, extracted_only, min_confidence):
                    rat.add(u)
        for v in G.successors(nid):
            e = G.edges[nid, v]
            if e.get("relation") == "rationale_for":
                all_rat_edges.append(e)
                if _passes_confidence(e, extracted_only, min_confidence):
                    rat.add(v)
        ids = sorted(rat, key=lambda x: -(G.in_degree(x) + G.out_degree(x)))
        return ("←rat", ids, {}, "degree desc",
                _drop_breakdown(all_rat_edges, extracted_only, min_confidence))

    if key == "coc":
        # Co-community: same Leiden cluster. Not co-occurrence in commits or imports.
        # No filter applies (members are nodes, not edges) so drops is empty.
        cid = G.nodes[nid].get("community", -1)
        if cid == -1:
            return ("⊕coc", [], {}, "", {})
        members = [n for n in communities.get(cid, []) if n != nid]
        members.sort(key=lambda x: -(G.in_degree(x) + G.out_degree(x)))
        labels = G.graph.get("community_labels") if hasattr(G, "graph") else None
        clabel = (labels or {}).get(cid)
        pname = f"⊕coc(c{cid}={clabel})" if clabel else f"⊕coc(c{cid})"
        return (pname, members, {}, "degree desc", {})

    return ("", [], {}, "", {})


# --- focus resolution ------------------------------------------------------

def _is_private_label(label: str) -> bool:
    """Treat leading underscore (after stripping `.` and `()`) as a private/helper marker.

    Used to push `_axes_info()` below `AXES` in fuzzy/substring ranking — public
    names are almost always what the agent meant when typing a short query.
    """
    s = label.strip("()").lstrip(".")
    return s.startswith("_")


def _rank_match(G: nx.DiGraph, key: str, nid: str) -> tuple[int, int, int]:
    """Sort key for fuzzy/substring matches. Prefer (1) public names,
    (2) shorter labels (less padding around the key), (3) higher degree (load-bearing).
    """
    label = G.nodes[nid].get("label", nid)
    is_priv = 1 if _is_private_label(label) else 0
    length_pad = max(0, len(label) - len(key))
    deg = G.in_degree(nid) + G.out_degree(nid)
    return (is_priv, length_pad, -deg)


def resolve_focus(G: nx.DiGraph, idx: dict[str, list[str]],
                  target: str) -> tuple[str | None, list[str], str, list[str]]:
    """Resolve `@<label>` → (chosen_id_or_None, candidates, match_type, alternatives).

    match_type ∈ {"exact", "substring", "fuzzy", ""}. The caller announces non-exact
    substitutions so the agent can verify rather than being silently steered.

    `alternatives` is non-empty only when `chosen` is set AND the match was non-exact.
    It carries 3-5 next-best ids the agent could pivot to with `@<label>` — this turns
    a single-hit fuzzy match from a take-it-or-grep moment into a guided pivot.
    """
    key = _norm(target.strip())
    if key.startswith("@"):
        key = key[1:]
    if not key:
        return None, [], "", []

    ALT_LIMIT = 4

    # 1. exact match (label or id)
    matches = idx.get(key, [])
    if len(matches) == 1:
        return matches[0], [], "exact", []
    if len(matches) > 1:
        # Rank multiple exact-match candidates by public-first / degree
        matches_sorted = sorted(matches, key=lambda n: _rank_match(G, key, n))
        return None, matches_sorted, "exact", []

    # 2. substring fallback (rank: public-first, shorter, higher degree).
    # Distinguish "prefix" (label starts with key) from generic "substring" so
    # the agent can gauge match strength — `@multi_axis_fingerprint` matching
    # `multi_axis_fingerprint.py` is a prefix hit (very high confidence) vs
    # `@axis` matching `score_source_by_axis()` (substring, weaker signal).
    substring_hits: list[str] = []
    prefix_hits: list[str] = []
    for nid in G.nodes():
        label = _norm(G.nodes[nid].get("label", nid))
        if key in label:
            substring_hits.append(nid)
            if label.startswith(key):
                prefix_hits.append(nid)
    substring_hits.sort(key=lambda n: _rank_match(G, key, n))
    prefix_hits.sort(key=lambda n: _rank_match(G, key, n))
    # Single canonical hit: prefer the prefix-hit set if it's exactly one,
    # otherwise fall through to whatever the substring set produced.
    if len(prefix_hits) == 1:
        all_labels = {_norm(G.nodes[n].get("label", n)): n for n in G.nodes()}
        close = get_close_matches(key, list(all_labels.keys()), n=ALT_LIMIT + 2, cutoff=0.6)
        alt_ids = [all_labels[lbl] for lbl in close if all_labels[lbl] != prefix_hits[0]][:ALT_LIMIT]
        return prefix_hits[0], [], "prefix", alt_ids
    if len(substring_hits) == 1:
        all_labels = {_norm(G.nodes[n].get("label", n)): n for n in G.nodes()}
        close = get_close_matches(key, list(all_labels.keys()), n=ALT_LIMIT + 2, cutoff=0.6)
        alt_ids = [all_labels[lbl] for lbl in close if all_labels[lbl] != substring_hits[0]][:ALT_LIMIT]
        return substring_hits[0], [], "substring", alt_ids
    if substring_hits:
        # Multi-hit: tag the listing as "prefix" if every hit is a prefix-hit
        # (cleaner signal to the agent), else "substring". Return the full
        # list — the renderer truncates to `limit`; the caller sees the
        # untruncated total for accurate "X matches" announcement.
        match_type = "prefix" if prefix_hits and len(prefix_hits) == len(substring_hits) else "substring"
        return None, substring_hits, match_type, []

    # 3. fuzzy (typo) fallback — labels only. difflib returns close matches in
    # similarity-desc order; preserve that ordering rather than re-ranking,
    # since fuzzy similarity is the dominant signal for typos.
    all_labels = {_norm(G.nodes[n].get("label", n)): n for n in G.nodes()}
    close = get_close_matches(key, list(all_labels.keys()), n=LIST_LIMIT, cutoff=0.7)
    fuzzy_ids = [all_labels[lbl] for lbl in close]
    if not fuzzy_ids:
        return None, [], "fuzzy", []
    if len(fuzzy_ids) == 1:
        return fuzzy_ids[0], [], "fuzzy", []
    # Multiple fuzzy hits: present as disambig listing rather than auto-picking
    # — the score gap with many candidates isn't reliable enough to silently
    # commit. The disambig itself acts as the pivot menu.
    return None, fuzzy_ids, "fuzzy", []


# --- op chain --------------------------------------------------------------

PIVOT_KEYS = {
    # Single-letter aliases — token economy on chained calls. `c` is *not*
    # aliased to keep `coc`/`contains` unambiguous; spell those out.
    "in": "in", "i": "in", "↗in": "in", "↗": "in",
    "out": "out", "o": "out", "↘out": "out", "↘": "out",
    "methods": "methods", "m": "methods", "◉methods": "methods", "◉": "methods", "method": "methods",
    "contains": "contains", "◇contains": "contains", "◇": "contains",
    "coc": "coc", "⊕coc": "coc", "⊕": "coc", "community": "coc",
    "rat": "rat", "r": "rat", "←rat": "rat", "←": "rat", "rationale": "rat",
    "inh": "inh", "→inh": "inh", "→": "inh", "inherits": "inh",
    "parent": "parent", "p": "parent", "⇡parent": "parent", "⇡": "parent",
    "siblings": "siblings", "sib": "siblings", "s": "siblings",
    "◈sib": "siblings", "◈": "siblings",
    # Sugar for the most common edge filter (`in/out --kind=calls`). The
    # arrow glyphs are directional ("◀" = callers come from the left,
    # "▶" = callees flow to the right).
    "callers": "callers", "◀callers": "callers", "◀": "callers",
    "callees": "callees", "▶callees": "callees", "▶": "callees",
}

CONTROL_KEYS = {
    "back": "back", "↺": "back",
    "reset": "reset",
    "status": "status", "where": "status", ".": "status",
}


def _is_pick(op: str) -> int | None:
    """Return integer index if op is `[N]` or just `N`, else None."""
    s = op.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    if s.isdigit():
        return int(s)
    return None


def navigate(ops: list[str] | str, *,
             graph_path: str | None = None,
             session: str | bool = True,
             fmt: str = "text",
             extracted_only: bool = True,
             min_confidence: float | None = None,
             show_legend: bool = False,
             show_ops_hint: bool = True,
             limit: int = LIST_LIMIT,
             kinds: set[str] | None = None,
             bodies: int | None = None,
             depth: int = 1) -> str:
    """Apply a chain of ops, return rendered output.

    ops: list of ops or a single string. Strings get split on whitespace.
    session: True (default) → ephemeral session: generate a fresh id, persist
        the cursor under it, print the id. Pass that id back as a string to
        resume a prior session. False → fully stateless: no disk activity,
        no id printed.
    fmt: "text" or "json".
    extracted_only: drop INFERRED edges. Default True — AST ground truth is
        almost always what an agent wants for navigation. Pass False (or use
        the CLI `--include-inferred` flag) to widen the result set to include
        LLM-inferred edges, which are bulk-tagged at confidence ~0.80 and add
        useful breadth on cross-language or doc-linked navigation but produce
        false positives on `path` queries (string-match fallthrough).
    min_confidence: drop edges with score below threshold.
    show_legend: prepend a one-line legend before output.
    show_ops_hint: append the ops cheat-sheet (turn off in chained agent calls).
    limit: max items per listing (default 25).
    kinds: optional allowlist of edge relation names (e.g. {"calls", "uses"}).
        When set, `in`/`out` pivots include only edges whose relation is in
        the set; counts of kind-excluded edges are surfaced in the drop
        breakdown so the agent can choose to widen.
    bodies: optional N. When set, `contains`/`methods` listings render the
        first N non-blank source lines under each item — surfaces dead/
        redirect/`raise NotImplementedError` stubs without a separate Read.
    """
    if isinstance(ops, str):
        ops = ops.strip().split() if ops.strip() else []

    gpath = Path(graph_path or DEFAULT_GRAPH_PATH)
    if not gpath.exists():
        msg = f"error: graph not found at {gpath}. run `graphify update <path>` first."
        return json.dumps({"type": "error", "message": msg}) if fmt == "json" else msg

    # Capture graph mtime so `_meta_tag` can flag files newer than the graph
    # (likely stale extraction). Also reset the per-process meta cache — the
    # filesystem may have moved between calls in long-running session usage.
    global _GRAPH_MTIME
    try:
        _GRAPH_MTIME = gpath.stat().st_mtime
    except OSError:
        _GRAPH_MTIME = 0.0
    _META_CACHE.clear()

    G, communities = load_graph(gpath)

    # --- session resolution -------------------------------------------------
    # session=False  → no disk, no id, no print
    # session=True   → ephemeral: fresh id, fresh cursor, persist + print
    # session=<str>  → resume that id (or start fresh under it if missing)
    notes: list[str] = []
    trace: list[str] = []  # fuzzy-match / pick announcements
    session_id: str | None = None
    persist = session is not False

    if persist:
        navigate_dir = gpath.parent / CURSOR_DIR
        _sweep_stale(navigate_dir)
        if isinstance(session, str) and session:
            session_id = session
            cpath = _cursor_path(gpath, session_id)
            if cpath.exists():
                cursor = Cursor.load(cpath)
                cursor.graph_path = str(gpath)
                # stale session warning
                if cursor.created_at and time.time() - cursor.created_at > STALE_AGE_SECONDS:
                    age_min = (time.time() - cursor.created_at) / 60
                    notes.append(f"  note: session {session_id} is {age_min:.0f}min old — `reset` to clear.")
            else:
                notes.append(f"  note: session {session_id} not found, starting fresh under that id.")
                cursor = Cursor(graph_path=str(gpath))
        else:
            session_id = _new_session_id()
            cursor = Cursor(graph_path=str(gpath))
    else:
        cursor = Cursor(graph_path=str(gpath))
        session_id = None

    last_data: dict | None = None
    chain_summary: list[str] = []  # one segment per non-trivial op for the chain trace

    def _summarize_step(op_str: str, ld: dict | None) -> None:
        """Append a compact tag for this op to chain_summary (only meaningful when chained)."""
        if ld is None:
            return
        t = ld.get("type")
        if t == "listing":
            chain_summary.append(f"{op_str}({ld.get('total', 0)})")
        elif t == "frontier":
            cur = ld.get("current") or {}
            label = cur.get("label", "?") if cur else "?"
            chain_summary.append(f"{op_str}→@{label}")
        elif t == "error":
            chain_summary.append(f"{op_str}!err")
        elif t == "status":
            chain_summary.append(op_str)

    # Process the chain
    show_history = persist and isinstance(session, str) and bool(session)
    if not ops:
        # No ops: show frontier (or status placeholder)
        last_data = _frontier_data(G, communities, cursor,
                                   extracted_only=extracted_only,
                                   min_confidence=min_confidence,
                                   show_history=show_history)
    else:
        idx = label_index(G)
        for op in ops:
            op_str = op.strip()
            if not op_str:
                continue

            handled = True
            if op_str.startswith("@"):
                # focus by label
                chosen, candidates, match_type, alternatives = resolve_focus(G, idx, op_str)
                if chosen:
                    if match_type and match_type != "exact":
                        chosen_label = G.nodes[chosen].get("label", chosen)
                        trace.append(f"  > matched `{op_str}` → {chosen_label} ({match_type})")
                        if alternatives:
                            alt_labels = [G.nodes[a].get("label", a) for a in alternatives]
                            trace.append(
                                f"  > also near: {', '.join(alt_labels)}  "
                                f"(pivot with @<label>)"
                            )
                    cursor.push(chosen)
                    cursor.last_listing = []
                    cursor.last_pivot = None
                    last_data = _frontier_data(G, communities, cursor,
                                               extracted_only=extracted_only,
                                               min_confidence=min_confidence,
                                               show_history=show_history)
                elif candidates:
                    if match_type and match_type != "exact":
                        trace.append(f"  > {len(candidates)} {match_type} matches for `{op_str}` — pick [N]")
                    cursor.last_listing = candidates[:limit]
                    cursor.last_pivot = "@-disambig"
                    last_data = _listing_data(G, candidates,
                                              f"@{op_str[1:]} ambiguous", None,
                                              total=len(candidates),
                                              sort_label="match relevance",
                                              limit=limit)
                else:
                    last_data = {"type": "error",
                                 "message": f"no node matches `{op_str}`. try a substring."}
            elif (n := _is_pick(op_str)) is not None:
                # pick from last listing
                if not cursor.last_listing:
                    last_data = {"type": "error",
                                 "message": "no listing to pick from. run a pivot first."}
                elif not (1 <= n <= len(cursor.last_listing)):
                    last_data = {"type": "error",
                                 "message": f"[{n}] out of range (1..{len(cursor.last_listing)})."}
                else:
                    picked_id = cursor.last_listing[n - 1]
                    picked_label = G.nodes[picked_id].get("label", picked_id)
                    trace.append(f"  > picked [{n}] {picked_label}")
                    cursor.push(picked_id)
                    cursor.last_listing = []
                    cursor.last_pivot = None
                    last_data = _frontier_data(G, communities, cursor,
                                               extracted_only=extracted_only,
                                               min_confidence=min_confidence,
                                               show_history=show_history)
            elif (ctrl := CONTROL_KEYS.get(op_str)) is not None:
                # control ops
                if ctrl == "reset":
                    if persist and session_id is not None:
                        _cursor_path(gpath, session_id).unlink(missing_ok=True)
                    cursor = Cursor(graph_path=str(gpath))
                    last_data = {"type": "status", "message": "cursor cleared."}
                elif ctrl == "back":
                    if cursor.pop():
                        cursor.last_listing = []
                        cursor.last_pivot = None
                        last_data = _frontier_data(G, communities, cursor,
                                                   extracted_only=extracted_only,
                                                   min_confidence=min_confidence,
                                                   show_history=show_history)
                    else:
                        last_data = {"type": "error", "message": "history empty."}
                elif ctrl == "status":
                    last_data = _frontier_data(G, communities, cursor,
                                               extracted_only=extracted_only,
                                               min_confidence=min_confidence,
                                               show_history=show_history)
            elif (pkey := PIVOT_KEYS.get(op_str)) is not None:
                # pivot
                if not cursor.current:
                    last_data = {"type": "error",
                                 "message": "no cursor. focus with @<label> first."}
                else:
                    pname, ids, edge_for, sort_label, drops = _pivot_data(
                        G, communities, cursor, pkey,
                        extracted_only=extracted_only,
                        min_confidence=min_confidence,
                        kinds=kinds,
                        depth=depth,
                    )
                    # Persist only the rendered window — keeps the cursor file
                    # small (coc on a 1000-node community would otherwise be ~40KB).
                    cursor.last_listing = ids[:limit]
                    cursor.last_pivot = pkey
                    last_data = _listing_data(G, ids, pname, edge_for,
                                              total=len(ids),
                                              sort_label=sort_label,
                                              limit=limit,
                                              drops=drops,
                                              kinds=kinds,
                                              bodies=bodies if pkey in ("contains", "methods") else None)
            else:
                handled = False
                last_data = {"type": "error",
                             "message": (f"unknown op `{op_str}`. ops: @<label> · "
                                         "in · out · methods · contains · coc · rat · inh · "
                                         "parent · back · reset · [N]")}
            _summarize_step(op_str, last_data)

            # Abort the chain on hard failures so a downstream op doesn't
            # silently overwrite the listing/error and mask the failure.
            # Two cases:
            #   1. error: nothing useful for downstream to build on.
            #   2. @-disambig: a pick is required before pivots make sense.
            if last_data and last_data.get("type") == "error":
                if len(ops) > 1:
                    trace.append(
                        f"  chain aborted at `{op_str}` — fix and rerun "
                        f"(remaining: {' '.join(o for o in ops[ops.index(op) + 1:])})"
                    )
                break
            if cursor.last_pivot == "@-disambig":
                if len(ops) > 1 and op != ops[-1]:
                    trace.append(
                        f"  chain aborted at `{op_str}` — pick [N] then resume "
                        f"(remaining: {' '.join(o for o in ops[ops.index(op) + 1:])})"
                    )
                break

    # Render a chain summary line when there were 2+ ops — mid-chain results
    # otherwise vanish ("output is the last op's result"), leaving the agent
    # to guess what got skipped.
    if len(chain_summary) >= 2:
        trace.append(f"  chain: " + " → ".join(chain_summary))

    # Persist cursor under session_id (if session enabled)
    if persist and session_id is not None:
        cursor.save(_cursor_path(gpath, session_id))

    # Render
    if fmt == "json":
        payload: dict[str, Any] = {}
        if session_id is not None:
            payload["session"] = session_id
        if notes:
            payload["notes"] = [n.strip() for n in notes]
        if trace:
            payload["trace"] = [t.strip() for t in trace]
        payload.update(last_data or {})
        return json.dumps(payload, indent=2, default=str, ensure_ascii=False)

    # Text
    parts: list[str] = []
    if show_legend:
        parts.append(LEGEND)
    if notes:
        parts.extend(notes)
    if trace:
        parts.extend(trace)
    if last_data is None:
        parts.append("(no output)")
    elif last_data.get("type") == "frontier":
        parts.append(_render_frontier_text(last_data, cursor, show_ops=show_ops_hint))
    elif last_data.get("type") == "listing":
        parts.append(_render_listing_text(last_data, show_ops=show_ops_hint))
    elif last_data.get("type") == "status":
        parts.append(last_data["message"])
    elif last_data.get("type") == "error":
        parts.append(last_data["message"])
    else:
        parts.append(json.dumps(last_data, default=str))

    if session_id is not None:
        parts.append(f"  session: {session_id}  (resume with --session {session_id})")

    return "\n".join(parts)
