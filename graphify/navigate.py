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
import sys
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from difflib import get_close_matches
from pathlib import Path
from typing import Any
import networkx as nx

from graphify.build import build_from_json
from graphify.analyze import _is_file_node
from graphify.resolve import (
    _norm,
    label_index,
    _file_meta,
    _is_archived_path,
    resolve_focus,
    _META_CACHE,
)

CURSOR_DIR = ".navigate"
DEFAULT_GRAPH_PATH = "graphify-out/graph.json"
LIST_LIMIT = 25
# `coc` listings on big communities (100+ members) flood context at 25 —
# the agent rarely needs that much breadth at once, and the cursor caches
# the resolved id list anyway, so re-running with `--limit N` is cheap.
# Lap-12: drop the coc default to a tighter window unless the caller
# explicitly raised --limit. `coc summary` is the structural-shape escape
# valve when even 10 members is too much.
COC_LIST_LIMIT_DEFAULT = 10
STALE_AGE_SECONDS = 30 * 60  # 30 min: sweep cursor files older than this

# Edge relations representing structural parenthood, not semantic flow.
# Excluded from ↗in/↘out so those pivots show real callers/callees only.
_STRUCTURAL = ("method", "contains", "rationale_for", "inherits")


# --- loading ---------------------------------------------------------------

def _check_graph_freshness(G: nx.DiGraph, graph_path: str | Path) -> str | None:
    """Compare graph.json mtime against the source files it indexes; return
    a banner when at least one source file is newer than the graph,
    `None` when the graph is current.

    Lap-20d (TS-Claude head-to-head report #1, "highest leverage"): the
    graph is a derived artifact, but every command silently treats it as
    ground truth. The "NOT FIXED" cycle from lap-20a→20b was caused by
    exactly this — the user re-ran a command, got the old shape, and
    concluded the fix didn't work. A freshness banner converts a silent
    correctness hazard into a visible "rerun update" prompt.

    Walks unique `source_file` paths in the graph (one stat per file,
    not per node). Misses brand-new files that aren't in the graph yet
    — `graphify changed` covers those.
    """
    try:
        graph_mtime = Path(graph_path).stat().st_mtime
    except OSError:
        return None
    seen: set[str] = set()
    newer_count = 0
    newest_mtime = 0.0
    for _nid, attrs in G.nodes(data=True):
        sf = attrs.get("source_file")
        if not sf or sf in seen:
            continue
        seen.add(sf)
        try:
            mt = Path(sf).stat().st_mtime
        except OSError:
            continue
        if mt > graph_mtime:
            newer_count += 1
            if mt > newest_mtime:
                newest_mtime = mt
    if newer_count == 0:
        return None
    age = newest_mtime - graph_mtime
    if age < 60:
        age_str = f"{int(age)}s"
    elif age < 3600:
        age_str = f"{int(age / 60)} min"
    elif age < 86400:
        age_str = f"{age / 3600:.1f} hr"
    else:
        age_str = f"{age / 86400:.1f} days"
    return (f"⚠ graph is {age_str} stale ({newer_count} file"
            f"{'s' if newer_count != 1 else ''} modified since extract). "
            f"run `graphify update .` for accuracy.")


def load_graph(graph_path: str | Path,
               *, freshness_check: bool = True) -> tuple[nx.DiGraph, dict[int, list[str]]]:
    """Load graph.json as a DiGraph plus a community→[node_ids] map.

    Also stamps each community's top-degree node label onto the DiGraph as
    `G.graph['community_labels']` — used by the renderer to print
    `c5=ExoticGeometryFramework` instead of bare `c5`.

    When `freshness_check=True` (default), emit a stale-graph banner to
    stderr if any indexed source file is newer than graph.json. Pass
    `freshness_check=False` to suppress (tests, CI, machine pipelines).
    """
    path = Path(graph_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    G = build_from_json(data, directed=True)
    communities: dict[int, list[str]] = defaultdict(list)
    for nid, attrs in G.nodes(data=True):
        cid = attrs.get("community")
        if cid is not None:
            communities[cid].append(nid)
    # Auto-name communities by their highest-degree non-file, non-rationale
    # member. File nodes accumulate `defined_in` edges from every symbol
    # they contain plus `imports` edges, which inflates their degree way
    # past any single semantic node. On the zero-tvm engine cluster a
    # 31-line shared `types.ts` was beating `buildDecodeEngine` for the
    # cluster name purely because every symbol in the file linked back
    # to it. The file isn't the semantic center; it's just the union.
    #
    # Prefer code symbols. Fall back to file nodes only when the cluster
    # has nothing else (rare — usually a community of just a couple of
    # standalone files).
    community_labels: dict[int, str] = {}
    community_hubs: dict[int, str] = {}
    for cid, members in communities.items():
        non_rat = [m for m in members
                   if G.nodes[m].get("file_type") != "rationale"]
        symbols = [m for m in non_rat if not _is_file_node(G, m)]
        pick_pool = symbols or non_rat  # fall back to file if no symbols
        if not pick_pool:
            continue
        ranked = sorted(
            pick_pool,
            key=lambda n: -(G.in_degree(n) + G.out_degree(n)),
        )
        hub_nid = ranked[0]
        raw = G.nodes[hub_nid].get("label", hub_nid)
        # Strip whitespace, collapse multi-line, cap label length so the
        # frontier/listing line budgets aren't blown out by sentence-shaped
        # labels (a docstring rationale that slipped through, etc.).
        clean = " ".join(str(raw).split())
        if len(clean) > 28:
            clean = clean[:27] + "…"
        community_labels[cid] = clean
        community_hubs[cid] = hub_nid
    G.graph["community_labels"] = community_labels
    G.graph["community_hubs"] = community_hubs
    # Mark cross-language INFERRED edges so `_passes_confidence` can drop
    # them with a single dict lookup instead of resolving languages on the
    # hot path. Drops surface as `+N cross-lang hidden`.
    _mark_cross_lang_edges(G)
    if freshness_check:
        banner = _check_graph_freshness(G, graph_path)
        if banner:
            print(banner, file=sys.stderr)
            G.graph["_freshness_banner"] = banner
    return G, dict(communities)


def _community_tag(G: nx.DiGraph, cid: int | str | None) -> str:
    """Render community as `c<cid>=<auto-name>` when a name is available, else `c<cid>`."""
    if cid is None or cid == "?" or cid == -1:
        return f"c{cid}"
    labels = G.graph.get("community_labels") if hasattr(G, "graph") else None
    if labels and cid in labels:
        return f"c{cid}={labels[cid]}"
    return f"c{cid}"


# --- cursor ----------------------------------------------------------------

@dataclass
class Cursor:
    current: str | None = None
    history: list[str] = field(default_factory=list)
    last_listing: list[str] = field(default_factory=list)
    last_pivot: str | None = None
    graph_path: str = DEFAULT_GRAPH_PATH
    created_at: float = 0.0  # epoch seconds; 0 means never persisted
    # Ops queued behind a disambig abort. When the next call resolves
    # the disambig (e.g. caller types `[N]` to pick), these replay
    # automatically after the pick so the agent doesn't re-type the
    # rest of the chain. Cleared on `back`/`reset` or if the next call
    # starts with a non-pick op (treat it as an override).
    queued_ops: list[str] = field(default_factory=list)
    # Per-session hint dedup: stable keys of hints already emitted in this
    # session. Each hint kind shows once; subsequent identical conditions
    # don't re-emit. Reset by `reset` (clears the cursor entirely).
    # Ephemeral non-session calls start with this empty every time, so
    # dedup only takes effect once the agent commits to `--session <id>`.
    hints_emitted: list[str] = field(default_factory=list)

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


# --- session-recent path log ----------------------------------------------
#
# Lap-3: the PreToolUse hook nudges "scout cheaper with graphify" on every
# Read, even on files the agent just navigated to via graphify itself. We
# log the source paths surfaced by each navigate() call here, with timestamps,
# and the hook reads this file to suppress the nudge for recently-visited
# targets. Best-effort; failures here must never abort navigate.

RECENT_PATHS_DIR = ".session"
RECENT_PATHS_FILE = "recent-paths"
RECENT_PATHS_TTL = 600          # 10 minutes
RECENT_PATHS_MAX = 200


def _record_session_paths(gpath: Path, source_files: set[str]) -> None:
    if not source_files:
        return
    target = gpath.parent / RECENT_PATHS_DIR / RECENT_PATHS_FILE
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        existing: list[str] = []
        if target.exists():
            try:
                existing = target.read_text(encoding="utf-8").splitlines()
            except OSError:
                existing = []
        kept: list[str] = []
        for line in existing:
            ts_str, _, _ = line.partition("\t")
            try:
                if now - float(ts_str) <= RECENT_PATHS_TTL:
                    kept.append(line)
            except ValueError:
                continue
        new_lines = [f"{now:.0f}\t{p}" for p in sorted(source_files) if p]
        all_lines = kept + new_lines
        if len(all_lines) > RECENT_PATHS_MAX:
            all_lines = all_lines[-RECENT_PATHS_MAX:]
        target.write_text("\n".join(all_lines) + "\n", encoding="utf-8")
    except OSError:
        return


def _collect_session_paths(last_data: dict | None, cursor: Cursor,
                           G: nx.DiGraph) -> set[str]:
    paths: set[str] = set()
    def _add(sf: str | None) -> None:
        if not sf:
            return
        try:
            paths.add(str(Path(sf).resolve()))
        except OSError:
            paths.add(str(sf))

    if cursor.current and cursor.current in G:
        _add(G.nodes[cursor.current].get("source_file"))
    if last_data:
        if last_data.get("type") == "frontier":
            cur = last_data.get("current") or {}
            _add(cur.get("source_file"))
        elif last_data.get("type") == "listing":
            for item in last_data.get("items", []):
                _add(item.get("source_file"))
    return paths


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


def _maybe_link(label: str, src: str | None, loc: str | None, md: bool) -> str:
    """If md mode and we have a path, render `[label](src:line)` markdown link.

    Otherwise return `label` unchanged. Used uniformly across renderers so
    --md affects every clickable-shaped output without per-call branching.
    The line component is parsed from `Lnnn` style source_location keys —
    if it doesn't parse, we still emit a link to the bare file path.
    """
    if not md or not src:
        return label
    line = ""
    if loc and loc.startswith("L"):
        try:
            line_no = int(loc[1:].split("-", 1)[0].split(":", 1)[0])
            line = f":{line_no}"
        except ValueError:
            line = ""
    return f"[{label}]({src}{line})"


_GRAPH_MTIME: float = 0.0  # set per-call so per-file staleness can be flagged


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

# Maps file extension → coarse language tag, used to detect inferred edges
# that cross a language boundary. The LLM-tagger frequently hallucinates on
# common method names (`.get()`, `.set()`) when the corpus mixes languages
# (e.g. a TS engine with a Python runtime sibling) — every inferred call
# in the TS portion picks up a same-named Python method as a target. AST
# (`EXTRACTED`) edges are ground truth and exempt; true polyglot calls
# (CFFI, JNI, etc.) appear as language-specific imports/extern decls and
# don't go through the inferrer.
_LANG_BY_EXT = {
    ".py": "python", ".pyi": "python",
    ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript",
    ".mjs": "javascript", ".cjs": "javascript",
    ".rb": "ruby",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin", ".kts": "kotlin",
    ".scala": "scala", ".sc": "scala",
    ".cs": "csharp",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp",
    ".hpp": "cpp", ".hxx": "cpp",
    ".c": "c", ".h": "c",
    ".m": "objc", ".mm": "objc",
    ".swift": "swift",
    ".php": "php",
    ".zig": "zig",
    ".jl": "julia",
    ".v": "verilog", ".sv": "verilog", ".vh": "verilog",
    ".ps1": "powershell", ".psm1": "powershell", ".psd1": "powershell",
}


def _node_language(G: nx.DiGraph, nid: str) -> str | None:
    """Best-effort language tag for a node, derived from source_file extension.
    Returns None when the node has no source_file or the extension is unknown.
    """
    if nid not in G.nodes:
        return None
    sf = G.nodes[nid].get("source_file") or ""
    if not sf:
        return None
    ext = Path(sf).suffix.lower()
    return _LANG_BY_EXT.get(ext)


def _mark_cross_lang_edges(G: nx.DiGraph) -> None:
    """Stamp `_xlang=True` on every INFERRED edge whose endpoints come from
    different language files. Called once at load time so the per-edge filter
    cost is a single dict lookup. AST edges are skipped — they're ground
    truth even when they cross languages (rare but legal: CFFI imports,
    template files, etc.)."""
    for u, v, data in G.edges(data=True):
        if data.get("confidence") == "EXTRACTED":
            continue
        src_lang = _node_language(G, u)
        tgt_lang = _node_language(G, v)
        if src_lang and tgt_lang and src_lang != tgt_lang:
            data["_xlang"] = True


def _passes_confidence(edge: dict, extracted_only: bool, min_confidence: float | None) -> bool:
    """Edge filter for cross-language drop, --extracted-only and --min-confidence.
    Cross-language inferred edges are always dropped; the other two are
    user-controlled.
    """
    if edge.get("_xlang"):
        return False
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
    far more informative than `↗in(0)` when the filter is active. Cross-lang
    is counted separately because it's always-on (no user flag), so the
    agent should know when it's been load-bearing.
    """
    inferred_hidden = 0
    low_conf_hidden = 0
    cross_lang_hidden = 0
    for e in edges:
        if _passes_confidence(e, extracted_only, min_confidence):
            continue
        # Cross-lang takes precedence over the user-controlled filters: an
        # edge that would *also* fail extracted_only/min_confidence is
        # primarily a cross-lang drop, since that filter is always-on.
        if e.get("_xlang"):
            cross_lang_hidden += 1
        elif extracted_only and e.get("confidence") != "EXTRACTED":
            inferred_hidden += 1
        elif min_confidence is not None and e.get("confidence") != "EXTRACTED":
            score = e.get("confidence_score")
            if not isinstance(score, (int, float)) or score < min_confidence:
                low_conf_hidden += 1
    return {"inferred": inferred_hidden, "low_confidence": low_conf_hidden,
            "cross_lang": cross_lang_hidden}


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
    if drops.get("cross_lang"):
        bits.append(f"+{drops['cross_lang']} cross-lang hidden")
    by_rel = drops.get("by_rel") or {}
    if by_rel:
        total = sum(by_rel.values())
        rels = ",".join(sorted(by_rel.keys()))
        bits.append(f"+{total} [{rels}] kind-hidden")
    if drops.get("archived"):
        bits.append(f"+{drops['archived']} archived hidden")
    return f" / {', '.join(bits)}" if bits else ""


# --- structured data builders ----------------------------------------------

# Path patterns that mark a node as living in clearly-archived territory.
# When a disambiguation lists nodes with the same label across active code
# and an archive dir, the archived ones should sort to the end and carry a
# visible tag so the agent doesn't pick them by accident.
#
def _filter_archived_ids(G: nx.DiGraph, ids: list[str],
                         mode: str) -> tuple[list[str], int]:
    """Apply --no-archived / --archived-only to a list of node ids.

    mode ∈ {"all", "no", "only"}. Returns (filtered_ids, hidden_count) so
    the listing renderer can surface "+N archived hidden" rather than
    silently dropping items (per the always-surface-omission-counts rule).
    """
    if mode == "all":
        return list(ids), 0
    keep: list[str] = []
    hidden = 0
    for nid in ids:
        src = G.nodes[nid].get("source_file") if nid in G else None
        is_arch = _is_archived_path(src)
        if mode == "no" and is_arch:
            hidden += 1
            continue
        if mode == "only" and not is_arch:
            hidden += 1
            continue
        keep.append(nid)
    return keep, hidden


def _is_function_kind(G: nx.DiGraph, nid: str) -> bool:
    """Is this node a function/method (vs class/file/rationale)?

    Used by empty-pivot diagnostics to redirect `methods`/`contains` calls
    on a function to the right pivots (`out`/`in`/`read`).

    TS extractor tags impl_method/iface_method explicitly. Python's pass
    leaves node_kind None on functions, so fall back to a label heuristic:
    `name()` on a node that has its own source_location (not a file hub
    where label happens to look function-shaped). File hubs are excluded
    by checking the label doesn't match the basename of source_file.
    """
    a = G.nodes[nid]
    kind = a.get("node_kind")
    if kind in ("impl_method", "iface_method", "function"):
        return True
    if kind:  # any other tagged kind (class/interface/type_alias/etc) → not a function
        return False
    label = a.get("label") or ""
    if not label.endswith("()"):
        return False
    src = a.get("source_file") or ""
    if src and Path(src).name == label:
        return False
    return True


def _is_test_path(src: str | None) -> bool:
    """Heuristic: is this source path a test file? Cheap path check, no file read."""
    if not src:
        return False
    s = src.lower()
    parts = Path(s).parts
    if any(p in ("tests", "test", "__tests__", "spec", "specs") for p in parts):
        return True
    name = Path(s).name
    return (name.startswith("test_") or name.endswith("_test.py")
            or name.endswith(".test.ts") or name.endswith(".test.js")
            or name.endswith(".test.tsx") or name.endswith(".spec.ts")
            or name.endswith(".spec.js"))


def _node_summary(G: nx.DiGraph, nid: str) -> dict:
    a = G.nodes[nid]
    src = a.get("source_file")
    meta = _file_meta(src) if src else {}
    cid = a.get("community")
    labels = (G.graph.get("community_labels") if hasattr(G, "graph") else None) or {}
    hubs = (G.graph.get("community_hubs") if hasattr(G, "graph") else None) or {}
    return {
        "id": nid,
        "label": a.get("label", nid),
        "community": cid,
        "community_label": labels.get(cid) if cid is not None else None,
        "is_community_hub": cid is not None and hubs.get(cid) == nid,
        "degree": G.in_degree(nid) + G.out_degree(nid),
        "source_file": src,
        "source_location": a.get("source_location"),
        "file_type": a.get("file_type", ""),
        "is_test": _is_test_path(src),
        "is_archived": _is_archived_path(src),
        # Coarse kind annotation: "interface" / "class" / "type_alias" /
        # "iface_method" / "impl_method" / None. Surfaced as
        # [iface]/[impl]/[type] in disambig listings so the agent can
        # tell the type-decl candidate apart from the runtime impl.
        "node_kind": a.get("node_kind"),
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

    # Rationale-share-of-contains: how many of this node's contained
    # children are rationale (doc/comment) fragments. When most/all of
    # them are rationale on a file hub, the file's `contains` listing is
    # mostly docs — the renderer surfaces a "docs/rationale-mostly file"
    # hint pointing at `read [N]` instead of a productive symbol drill.
    contains_rationale_count = 0
    for v in G.successors(nid):
        e = G.edges[nid, v]
        if (e.get("relation") == "contains"
                and _passes_confidence(e, extracted_only, min_confidence)
                and G.nodes[v].get("file_type") == "rationale"):
            contains_rationale_count += 1

    # Per-relation incoming counts used to gate impl_of/type_ref hints. The
    # `interface_kind` / `empty_class_protocol` / `closure_iface_dispatch`
    # hints used to suggest `in --kind=impl_of` unconditionally on every
    # interface/iface_method/protocol-class node. On corpora that lean on
    # structural (duck-typed) implementation rather than explicit `class Foo
    # implements Bar`, no impl_of edges get extracted, and the hint sends
    # agents on a wild goose chase. Counting actual incoming impl_of/type_ref
    # edges per-node lets the hint adapt: suggest impl_of when it'd return
    # something, fall back to type_ref when that's where the action is.
    impl_of_in_count = 0
    type_ref_in_count = 0
    for u in G.predecessors(nid):
        e = G.edges[u, nid]
        if not _passes_confidence(e, extracted_only, min_confidence):
            continue
        rel = e.get("relation")
        if rel == "impl_of":
            impl_of_in_count += 1
        elif rel == "type_ref":
            type_ref_in_count += 1

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
        "contains_rationale_count": contains_rationale_count,
        "impl_of_in_count": impl_of_in_count,
        "type_ref_in_count": type_ref_in_count,
        # Echoed so the renderer can confirm `--include-inferred` was
        # honored when the resulting view is identical to the default
        # (no inferred edges existed to add).
        "extracted_only": extracted_only,
    }


_DUPE_COLLAPSE_THRESHOLD = 5


def _listing_data(G: nx.DiGraph, ids: list[str], pivot_name: str,
                  edge_for: dict[str, dict] | None, total: int,
                  sort_label: str, limit: int,
                  drops: dict[str, int] | None = None,
                  kinds: set[str] | None = None,
                  bodies: int | None = None,
                  extracted_only: bool = True,
                  collapse_dupes: bool = True,
                  node_kinds: set[str] | None = None) -> dict:
    """Build a listing dict from a list of node ids.

    Lap-9 collapse: when >=5 adjacent items share a label, group them into
    a single collapsed row with `dupe_count`/`dupe_samples`/`dupe_member_ids`.
    The limit is applied AFTER collapse so the visible row count matches the
    listing's `[N]` indices. `cursor.last_listing` (set by the caller from
    `[item["id"] for item in items]`) lands on the first member of each
    group; an agent who needs a specific dupe pivots via `@<dir>/<file>/<sym>`.

    `collapse_dupes=False` opts out (CLI: `--no-collapse`).
    """
    # Lap-21 #5: drop external_module nodes from listings in default mode.
    # These are stubs from unresolved imports (`kg_compile_v2_compile`,
    # `numpy`, etc.) — load-bearing when --include-inferred widens the
    # graph (they participate in inferred call edges) but pure noise in
    # default/AST listings, where they mix snake_case stubs alongside
    # real PascalCase/camelCase symbols and bury the meaningful rows.
    # `extracted_only=True` is the gate (set False by --include-inferred).
    external_hidden = 0
    if extracted_only:
        filtered_ids = []
        for nid in ids:
            attrs = G.nodes[nid]
            if (attrs.get("file_type") == "external"
                    or attrs.get("node_kind") == "external_module"):
                external_hidden += 1
                continue
            filtered_ids.append(nid)
        ids = filtered_ids
        if total >= len(filtered_ids) + external_hidden:
            total -= external_hidden

    # Lap-21 polish: --node-kind drops rows whose node_kind isn't in the
    # allowlist. TS-Claude reported `@compile` substring-listing returning
    # 37 rows where the top 3 (the actual functions) were what they
    # wanted; --node-kind=function lops the file/iface/external rows.
    # Synonym: "method" matches both raw "method" and "impl_method";
    # "function" matches "function" only. The set is matched as a strict
    # equality check on each item's node_kind attribute.
    node_kind_hidden = 0
    if node_kinds:
        # Expand canonical synonyms so `--node-kind=method` catches both
        # "method" and "impl_method" without requiring callers to know
        # the extractor's tag scheme.
        expanded = set(node_kinds)
        if "method" in expanded:
            expanded.update({"impl_method", "iface_method"})
        if "function" in expanded:
            expanded.update({"impl_method"})  # treat impls as functions too
        if "interface" in expanded:
            expanded.update({"iface_method"})
        kept = []
        for nid in ids:
            nk = G.nodes[nid].get("node_kind") or ""
            if nk in expanded:
                kept.append(nid)
            else:
                node_kind_hidden += 1
        ids = kept
        if total >= len(kept) + node_kind_hidden:
            total -= node_kind_hidden

    raw_items = []
    for nid in ids:
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
        raw_items.append(item)

    collapsed_count = 0  # how many underlying nodes got folded into groups
    if collapse_dupes:
        items: list[dict] = []
        i = 0
        # Group ADJACENT same-label items only. The upstream sort (degree
        # desc, locality, EXTRACTED-first) usually clusters same-label
        # rows together. Re-sorting just to maximize grouping would fight
        # the existing sort contract; better to under-collapse than to
        # rearrange items the agent expects to see ranked.
        while i < len(raw_items):
            cur_label = raw_items[i].get("label")
            j = i + 1
            while j < len(raw_items) and raw_items[j].get("label") == cur_label:
                j += 1
            group_size = j - i
            if group_size >= _DUPE_COLLAPSE_THRESHOLD:
                first = raw_items[i]
                first["dupe_count"] = group_size
                first["dupe_samples"] = [
                    {"source_file": raw_items[k].get("source_file") or "",
                     "source_location": raw_items[k].get("source_location") or ""}
                    for k in range(i, min(j, i + 3))
                ]
                # Group member ids for downstream tooling that wants to
                # walk the full set; cursor.last_listing only stores the
                # representative id per visible row.
                first["dupe_member_ids"] = [raw_items[k]["id"] for k in range(i, j)]
                items.append(first)
                collapsed_count += group_size - 1
            else:
                items.extend(raw_items[i:j])
            i = j
    else:
        items = list(raw_items)

    items = items[:limit]
    drops_out = dict(drops or {})
    if external_hidden:
        drops_out["external"] = external_hidden
    if node_kind_hidden:
        drops_out["node_kind"] = node_kind_hidden
    return {
        "type": "listing",
        "pivot": pivot_name,
        "total": total,
        # Visible rows post-collapse. Distinct from `total` (underlying
        # node count). When the listing was collapsed, `total` may exceed
        # `showing + extras` — the renderer surfaces this gap as
        # "+M more in collapsed groups".
        "showing": len(items),
        "collapsed": collapsed_count,
        "sort": sort_label,
        "items": items,
        "drops": drops_out,
        "kinds": sorted(kinds) if kinds else None,
        "bodies": bodies,
        # Echoed so the renderer can confirm `--include-inferred` was
        # honored when an empty result might otherwise look like a
        # silently-dropped flag.
        "extracted_only": extracted_only,
    }


def _signature_end(lines: list[str], start: int, max_search: int = 40) -> int:
    """Return the line index AFTER the signature header (multi- or single-line).

    A multi-line `def foo(\\n    a,\\n    b,\\n) -> T:` has continuation
    lines indented far past the body's indent baseline. Without skipping
    them, the body walker fixates on the param-column indent, then
    breaks the moment the actual body dedents past it — capturing only
    the signature lines and missing the body.

    Tracks ONLY `(` / `)` depth — not `{}` or `[]`. The TS/JS body opens
    with `{` on the header line (e.g. `function foo(): T {`); counting it
    as a continuation kept the walker inside the body until matching `}`
    and silently truncated to args-only. `[]` was once tracked because
    Python type hints like `Dict[str, int]` can wrap multi-line params,
    but those brackets are always inside the param parens anyway so
    paren-only is sufficient. No string/comment awareness; bounded by
    `max_search` so we don't walk forever on malformed input.
    """
    depth = 0
    for i in range(start, min(len(lines), start + max_search)):
        for c in lines[i]:
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
        if depth <= 0:
            return i + 1
    return start + 1


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
    # Skip past a multi-line signature so deeply-indented continuation
    # lines don't fixate body_indent (see _signature_end docstring).
    sig_end = _signature_end(lines, start)
    body_indent: int | None = None
    out: list[str] = [header.rstrip()]
    # Append any signature continuation lines verbatim — the agent gets
    # the full signature for orientation; the preview cap counts these too.
    for i in range(start + 1, min(sig_end, start + 200)):
        cont = lines[i].rstrip()
        if not cont.strip():
            continue
        out.append(cont)
        if len(out) >= n:
            return out
    for raw in lines[sig_end:start + 200]:
        stripped = raw.rstrip()
        if not stripped.strip():
            continue
        cur_indent = len(stripped) - len(stripped.lstrip(" \t"))
        if body_indent is None:
            if cur_indent > header_indent:
                body_indent = cur_indent
                out.append(stripped)
                if len(out) >= n:
                    break
                continue
            # If the next non-blank is at or below the header's indent, the
            # function body is empty — bail.
            break
        # Subsequent body lines: stop if we've dedented past the body baseline.
        if cur_indent < body_indent:
            break
        out.append(stripped)
        if len(out) >= n:
            break
    return out


def _read_body_full(source_file: str | None, source_location: str | None,
                    max_lines: int = 200,
                    flat: bool = False) -> tuple[list[str], int, bool]:
    """Read the full body at source_location, preserving indentation.

    Returns (lines, start_line_no, truncated). Lines include the header.
    Walks until the next dedent past the body's indent baseline, or until
    `max_lines` is reached. Used by the `read` op to fold node-find +
    body-read into a single navigate call (Lap-3 wishlist #2).

    `flat=True` skips the indent-walker logic and returns the next
    `max_lines` lines verbatim. Used for file nodes — a file has no
    nested body, so the indent walker bails after the first non-indented
    line and returns just one line. Flat mode treats `read` as "dump
    the next N lines from this offset", which is what the agent meant.
    """
    if not source_file or not source_location:
        return [], 0, False
    loc = source_location
    line_no: int | None = None
    if loc.startswith("L"):
        try:
            line_no = int(loc[1:].split("-", 1)[0].split(":", 1)[0])
        except ValueError:
            return [], 0, False
    if line_no is None:
        return [], 0, False
    try:
        with open(source_file, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return [], 0, False
    start = max(0, line_no - 1)
    if start >= len(lines):
        return [], 0, False
    if flat:
        # File-node mode: dump the next max_lines verbatim. The indent
        # walker doesn't fit because file nodes have no enclosing body.
        slab = [ln.rstrip("\n") for ln in lines[start:start + max_lines]]
        truncated = len(lines) - start > max_lines
        return slab, line_no, truncated
    header = lines[start].rstrip("\n")
    header_indent = len(header) - len(header.lstrip(" \t"))
    # Skip past multi-line signature continuation lines (paren depth > 0
    # on the header). Without this the deeply-indented argument
    # continuation rows fixated body_indent at the param column,
    # causing the actual body to dedent below it and the walker to bail
    # after just the signature.
    sig_end = _signature_end(lines, start)
    out: list[str] = [header]
    for i in range(start + 1, min(sig_end, start + 1 + max_lines)):
        out.append(lines[i].rstrip("\n"))
    body_indent: int | None = None
    truncated = False
    for raw in lines[sig_end:start + 1 + max_lines]:
        stripped_full = raw.rstrip("\n")
        if not stripped_full.strip():
            out.append(stripped_full)
            continue
        cur_indent = len(stripped_full) - len(stripped_full.lstrip(" \t"))
        if body_indent is None:
            if cur_indent > header_indent:
                body_indent = cur_indent
            else:
                # Body never started (decorator-only / single-line def). Bail.
                break
        if cur_indent < body_indent:
            break
        out.append(stripped_full)
    if len(lines) - start - 1 > max_lines and len(out) >= max_lines:
        truncated = True
    return out, line_no, truncated


def _coc_summary_data(G: nx.DiGraph, communities: dict[int, list[str]],
                      cursor: Cursor) -> dict:
    """Structural shape of the focus's coc community without enumerating members.

    Built for the `coc summary` op: when a community has 500+ members,
    rendering the listing burns tokens on rows the agent isn't going to
    pick from. The summary surfaces what an experienced reviewer scans
    for first — top hubs, composition, dominant edge type, file spread —
    and skips the long tail.

    Returns a dict shaped for `_render_coc_summary_text`. Self is excluded
    from member counts and hub ranking; intra-community degree (edges
    whose other endpoint also lives in the community) is the rank used
    for hub picking, since cross-community out-edges don't tell you
    anything about *this* cluster's center.
    """
    nid = cursor.current
    if not nid or nid not in G:
        return {"type": "error",
                "message": "no cursor — focus a node before `coc summary`."}
    cid = G.nodes[nid].get("community", -1)
    if cid == -1:
        return {"type": "error",
                "message": f"@{G.nodes[nid].get('label', nid)} has no community assignment."}
    raw_members = communities.get(cid, [])
    members = [m for m in raw_members if m != nid]
    member_set = set(raw_members)
    labels = (G.graph.get("community_labels") if hasattr(G, "graph") else None) or {}

    def _is_file_hub(n: str) -> bool:
        attrs = G.nodes[n]
        sf = attrs.get("source_file") or ""
        return bool(sf and attrs.get("label") == Path(sf).name)

    file_hubs = [m for m in members if _is_file_hub(m)]
    rationale = [m for m in members if G.nodes[m].get("file_type") == "rationale"]
    symbols = [m for m in members if not _is_file_hub(m)
               and G.nodes[m].get("file_type") != "rationale"]

    # Intra-community degree: degree counted only against edges where
    # both endpoints are members. Strips out the long tail of cross-
    # community edges that would otherwise pull a generic hub like
    # `print()` to the top of every community it appears in.
    def _intra_deg(n: str) -> int:
        d = 0
        for v in G.successors(n):
            if v in member_set:
                d += 1
        for u in G.predecessors(n):
            if u in member_set:
                d += 1
        return d

    by_deg = sorted(members, key=lambda m: -_intra_deg(m))
    top_hubs = []
    for m in by_deg[:5]:
        top_hubs.append({
            "id": m,
            "label": G.nodes[m].get("label", m),
            "intra_degree": _intra_deg(m),
            "is_file": _is_file_hub(m),
        })

    # Leaf threshold: ≤1 intra-community neighbor. The exact threshold
    # is heuristic — `0` would miss singleton tails that have one wired
    # neighbor; `2` would over-collect bus-stop-shaped utility nodes.
    leaves = sum(1 for m in members if _intra_deg(m) <= 1)

    rel_counts: dict[str, int] = defaultdict(int)
    for u in member_set:
        for v in G.successors(u):
            if v in member_set:
                rel = G.edges[u, v].get("relation") or "?"
                rel_counts[rel] += 1
    top_rels = sorted(rel_counts.items(), key=lambda x: -x[1])[:3]

    files = {G.nodes[m].get("source_file", "") for m in raw_members}
    files = {f for f in files if f}

    return {
        "type": "coc_summary",
        "community_id": cid,
        "community_label": labels.get(cid),
        "total": len(members),
        "file_hub_count": len(file_hubs),
        "symbol_count": len(symbols),
        "rationale_count": len(rationale),
        "leaf_count": leaves,
        "top_hubs": top_hubs,
        "top_relations": top_rels,
        "files_count": len(files),
    }


def _render_coc_summary_text(data: dict, *, md: bool = False) -> str:
    if data.get("type") == "error":
        return f"  coc summary: {data.get('message')}"
    cid = data.get("community_id")
    cl = data.get("community_label")
    cstr = f"c{cid}={cl}" if cl else f"c{cid}"
    out = [
        f"  coc summary {cstr}: {data['total']} member(s) across "
        f"{data['files_count']} file(s)"
    ]
    out.append(
        f"    composition: {data['file_hub_count']} file-hub(s), "
        f"{data['symbol_count']} symbol(s), "
        f"{data['rationale_count']} rationale, "
        f"{data['leaf_count']} leaf-degree (≤1)"
    )
    hubs = data.get("top_hubs") or []
    if hubs:
        # Render top hubs with intra-community degree so the agent can
        # judge which are wired-into-the-cluster vs which are just
        # high-traffic across the whole graph.
        bits = []
        for h in hubs:
            tag = " [file]" if h.get("is_file") else ""
            bits.append(f"{h['label']}{tag} (d={h['intra_degree']})")
        out.append(f"    top hubs: {', '.join(bits)}")
    rels = data.get("top_relations") or []
    if rels:
        rel_str = ", ".join(f"{r}:{c}" for r, c in rels)
        out.append(f"    edge mix (intra-community): {rel_str}")
    out.append(
        f"    drill: `coc` for full member list, "
        f"or `coc --explain-cost` to size the listing first."
    )
    return "\n".join(out)


def _estimate_listing_bytes(G: nx.DiGraph, ids: list[str], limit: int) -> int:
    """Cheap estimate of rendered text size for a listing of node ids.

    Used by `--explain-cost`: lets the caller see "would return 532 nodes
    ≈ 4 kB" before paying the token cost on a `coc` against a thousand-node
    community. We sum per-row overhead plus actual label + path lengths
    (the dominant variable cost) to avoid wildly over- or under-estimating.

    Limit-bounded — we only count rendered rows, not the underlying total.
    The caller surfaces both numbers.
    """
    showing = min(len(ids), limit)
    if showing == 0:
        return 80
    # Header + cluster-hoist line + footer "+N more" + ops line ≈ 250 bytes.
    bytes_total = 250
    for nid in ids[:showing]:
        if nid not in G:
            continue
        attrs = G.nodes[nid]
        label_len = min(45, len(attrs.get("label", nid)))
        src = attrs.get("source_file") or ""
        # File-table compresses repeated paths down to a single letter, but
        # for the estimate we assume the worst case (no repeats) since
        # we'd need to actually walk the listing to know. ~40 chars cap on
        # short_src.
        src_len = min(40, len(src))
        # Per-row overhead: index, label padding, deg/cN tag, edge tag,
        # meta. ~30 bytes is conservative.
        bytes_total += 30 + label_len + src_len
    return bytes_total


def _humanize_bytes(n: int) -> str:
    """1234 → '1.2 kB'. Used by the explain-cost preview."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} kB"
    return f"{n / (1024 * 1024):.1f} MB"


def _render_preview_text(data: dict) -> str:
    """Render an `--explain-cost` preview. One-liner: pivot, total nodes,
    estimated rendered bytes, and the actual run command (no flag)."""
    pivot = data.get("pivot") or "?"
    total = data.get("total", 0)
    showing = data.get("showing", 0)
    est = data.get("estimated_bytes", 0)
    drops = data.get("drops") or {}
    bits = []
    if drops.get("inferred"):
        bits.append(f"+{drops['inferred']} inf hidden")
    if drops.get("archived"):
        bits.append(f"+{drops['archived']} archived hidden")
    drops_tail = (f"  (filters: {', '.join(bits)})") if bits else ""
    body = (f"  preview {pivot}: would return {total} node(s), "
            f"render ≈ {_humanize_bytes(est)}")
    if total > showing:
        body += f" (top {showing} after --limit)"
    body += drops_tail
    body += "\n  drop --explain-cost to commit, or `coc summary` for shape only."
    return body


def _render_body_text(data: dict, *, md: bool = False) -> str:
    """Render a `read`/`body` op result. Output is the file path/line header
    plus the body lines numbered, so the agent can jump straight to a
    specific line without a separate Read call."""
    label = data.get("label") or "?"
    sf = data.get("source_file") or "?"
    ln = data.get("start_line") or 0
    body_lines = data.get("lines") or []
    truncated = data.get("truncated")
    linked_label = _maybe_link(label, data.get("source_file"),
                                data.get("source_location"), md)
    if not body_lines:
        return f"  read @{linked_label}: no body at {sf}:{ln} (missing source or unparseable location)"
    header = f"  read @{linked_label}  ({sf}:{ln}, {len(body_lines)} lines"
    if truncated:
        header += f" — truncated at {len(body_lines)}, raise with `read N` or focus contained items"
    header += ")"
    out = [header]
    for i, raw in enumerate(body_lines):
        out.append(f"  {ln + i:>4}  {raw}")
    return "\n".join(out)


# --- text renderers --------------------------------------------------------

LEGEND = (
    "  legend:\n"
    "    columns: c<N>=community · d=<degree> · src=<file>:<line> · "
    "g<N>d=git mtime age (days; `g3d` = last commit 3 days ago) · "
    "<N>ln=line count\n"
    "    edge confidence: ext=EXTRACTED (AST ground truth) · inf@<lo>-<hi>=INFERRED with score range\n"
    "    hidden cues (inline on each pivot count): "
    "+Ninf=INFERRED hidden · +Nlc=below --min-confidence · "
    "+Nxl=cross-language inferred hidden (always on) · "
    "+Nkind=--kind-filtered · +Narch=archived hidden\n"
    "    tags: [archived]=path under frozen/legacy/deprecated/archive(d)/ · "
    "[iface]/[impl]/[type]=TS interface, class implementing one, or type alias · "
    "[test]=test file\n"
    "    nodes: `Foo()` = function/symbol decl · `.foo()` = method/property reference (call-site or interface signature)\n"
    "    pivots:\n"
    "      in/out      = inbound/outbound semantic edges (calls, uses, type_ref, ...)\n"
    "      callers/callees = sugar for `in/out --kind=calls`\n"
    "      dependents/dependencies = transitive callers/callees (depth=3 default; sugar over callers/callees --depth=3)\n"
    "      methods     = method-of edges (class → its methods)\n"
    "      contains    = file → its top-level decls (or class → its members)\n"
    "      coc         = co-community siblings (same Leiden cluster — symbols co-located by graph topology)\n"
    "      coc summary = structural shape only (top hubs / composition / edge mix); cheap on big communities\n"
    "      rat         = rationale anchors (docstring/comment nodes attached to the symbol)\n"
    "      inh         = inheritance edges (class extends, interface extends)\n"
    "      parent      = structural parent (containing class or file)\n"
    "      siblings    = peers under the same parent (file/class)\n"
    "      read [N]    = dump focused node body inline (N caps lines, default 200)\n"
    "      back/reset  = pop history / clear cursor\n"
    "    picks: [N] or N (after a listing) · @<label>=focus a node by label/id"
)


def _emit_hint(out: list[str], key: str, message: str,
               cursor: Cursor | None, quiet_hints: bool) -> None:
    """Append a hint line subject to two gates:
       1. `quiet_hints=True` → suppress all hints (CLI: `--quiet-hints`).
       2. `key in cursor.hints_emitted` → already shown this hint kind in
          this session — skip the repeat.
    Records emitted keys on the cursor for future calls. Non-session
    (ephemeral) cursors discard the record on save, so dedup only takes
    effect once the agent commits to `--session <id>`.
    """
    if quiet_hints:
        return
    if cursor is not None and key in cursor.hints_emitted:
        return
    out.append(message)
    if cursor is not None:
        cursor.hints_emitted.append(key)


def _render_frontier_text(data: dict, cursor: Cursor, *, show_ops: bool,
                           md: bool = False, quiet_hints: bool = False) -> str:
    if data.get("current") is None:
        # First-contact: surface the cheat-sheet unconditionally so a brand-new
        # agent doesn't have to know `--ops-hint` exists. After a focus lands,
        # show_ops controls whether subsequent frontiers carry the line.
        msg = data.get("message", "no cursor.")
        return (msg + "\n  ops: @<label> to focus · in | out | methods | contains | "
                "coc | rat | inh | parent | siblings | callers | callees | read | filter <rgx>")

    n = data["current"]
    cid = n.get("community", "?")
    clabel = n.get("community_label")
    # Lap-13 field-report fix: `c3=DecodeEngine` reads on first glance like
    # "Atlas IS DecodeEngine" — the equals sign suggests identity. The
    # cluster label is actually the *hub* of the community Atlas belongs
    # to, not Atlas itself. Using `hub:` makes the role explicit without
    # adding length. Listings keep `=` because their surrounding `all in`
    # / `coc summary` text disambiguates the relationship.
    # Lap-21 polish: when the focus IS the community's hub, `hub:<label>`
    # restates the focus's own label — pure noise. Suppress.
    if clabel and not n.get("is_community_hub"):
        cstr = f"c{cid} hub:{clabel}"
    else:
        cstr = f"c{cid}"
    src = _short_src(n.get("source_file"), n.get("source_location"))
    ftype_tag = ""
    ft = n.get("file_type", "")
    if ft and ft != "code":
        ftype_tag = f" [{ft}]"

    p = data["pivots"]
    test_tag = " [test]" if n.get("is_test") else ""
    archived_tag = " [archived]" if n.get("is_archived") else ""
    # Lap-7: surface kind on the header too — agents inspecting an interface
    # vs its impl class need the cue at the top, not just on child listings.
    kind = n.get("node_kind") or ""
    if kind in ("interface", "iface_method"):
        kind_tag = " [iface]"
    elif kind == "impl_method":
        kind_tag = " [impl]"
    elif kind == "type_alias":
        kind_tag = " [type]"
    else:
        kind_tag = ""
    linked_label = _maybe_link(n['label'], n.get('source_file'),
                                n.get('source_location'), md)
    header = f"@ {linked_label}  · {cstr} · deg={n['degree']} · {src}{ftype_tag}{test_tag}{archived_tag}{kind_tag}{_meta_tag(n)}"

    def _glyph(prefix: str, pv: dict, with_conf: bool = False) -> str:
        # Format: glyph(count[+inline hidden][; +M via methods])
        # Always show the drop-count when filtering hides edges, so the agent
        # never thinks a 0-count means "nothing exists" when it really means
        # "nothing matches the current filter".
        # The via_methods rollup specifically guards against the trust-bug
        # case: `↗in(0)` on a class whose methods are called from 50 places
        # would suggest "nobody uses this", which is wrong.
        #
        # Lap-8: hidden counts inline as `↗in(0+41inf)` instead of the
        # parenthetical `(0; +41 INFERRED hidden)` form. Reporter said the
        # old format was easy to misread as a global flag suggestion. The
        # inline form keeps the count and the visible-vs-hidden split
        # together at a glance. Surfaces:
        #   +Ninf  = inferred-confidence hidden
        #   +Nlc   = below --min-confidence
        #   +Nkind = kind-filtered (--kind=)
        #   +Narch = archived-hidden (--no-archived)
        c = pv["count"]
        drops = pv.get("drops") or {}
        hidden_bits = []
        if drops.get("inferred"):
            hidden_bits.append(f"+{drops['inferred']}inf")
        if drops.get("low_confidence"):
            hidden_bits.append(f"+{drops['low_confidence']}lc")
        if drops.get("cross_lang"):
            hidden_bits.append(f"+{drops['cross_lang']}xl")
        by_rel = drops.get("by_rel") or {}
        if by_rel:
            hidden_bits.append(f"+{sum(by_rel.values())}kind")
        if drops.get("archived"):
            hidden_bits.append(f"+{drops['archived']}arch")
        hidden_inline = "".join(hidden_bits)
        via = pv.get("via_methods")
        via_suffix = f"; +{via} via methods" if via else ""
        if with_conf and c:
            return f"{prefix}({c}{hidden_inline}: {pv['confidence_text']}{via_suffix})"
        return f"{prefix}({c}{hidden_inline}{via_suffix})"

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
            _emit_hint(out, "drill_via_contains",
                f"  hint: {kind}. drill: `contains` ({p['contains']['count']}) "
                f"→ pick a function → then `out`.",
                cursor, quiet_hints)
        elif p['methods']['count'] > 0:
            via = p['in'].get('via_methods', 0)
            if via:
                _emit_hint(out, "class_shape_via_methods",
                    f"  hint: class-shaped — direct callers=0 but {via} reach via "
                    f"its methods. drill: `methods` ({p['methods']['count']}) → "
                    f"pick a method → then `in`.",
                    cursor, quiet_hints)
            else:
                _emit_hint(out, "drill_via_methods",
                    f"  hint: no direct call edges. drill: `methods` "
                    f"({p['methods']['count']}) → pick a method → then `in`/`out`.",
                    cursor, quiet_hints)
        elif (p['contains']['count'] == 0 and p['methods']['count'] == 0
              and p['parent']['count'] == 0 and p['rat']['count'] == 0
              and p['inh']['count'] == 0):
            coc_n = p.get('coc', {}).get('count', 0)
            if coc_n > 0:
                _emit_hint(out, "orphan_in_cluster",
                    f"  hint: orphan — no in/out/parent/contains/methods. "
                    f"closest context is its community ({coc_n} members) — try `coc`.",
                    cursor, quiet_hints)
            else:
                _emit_hint(out, "fully_isolated",
                    "  hint: fully isolated — no edges and no community peers. "
                    "graph likely has stale extraction; consider `graphify update .`.",
                    cursor, quiet_hints)
    elif p['methods']['count'] > 0 and p['in'].get('via_methods', 0) > p['in']['count']:
        _emit_hint(out, "class_shape_rollup",
            f"  hint: class-shaped — {p['in']['count']} direct callers vs "
            f"{p['in']['via_methods']} via methods. `methods` then `in` reveals the wider call graph.",
            cursor, quiet_hints)
    elif (is_code_file and p['methods']['count'] == 0
          and p['contains']['count'] > 0):
        _emit_hint(out, "file_shape",
            f"  hint: file — {p['contains']['count']} contained, "
            f"{p['in']['count']} importer(s); drill: `contains` for the "
            f"symbols inside, `in` to see who imports.",
            cursor, quiet_hints)

    # Lap-9 specific-cause empty hints. The generic "0 in" message doesn't
    # explain WHY a node has no callers. These cases turn a misleading
    # result into a productive next action by naming the structural cause.
    label = cur.get("label", "")
    node_kind = cur.get("node_kind") or ""

    # Closure / interface dispatch: method-shaped label (`.foo()` form) with
    # 0 EXTRACTED in or out but inferred edges available. AST-only resolution
    # drops on dynamic dispatch — closure factories returning records, or
    # interface methods bound at runtime — because the call-site target is a
    # property reference, not a direct symbol. Naming the cause prevents
    # "this method has no callers" misreads. Threshold is 1 (not 5) and
    # fires on either direction: closure-heavy designs are common enough
    # that AST-only is the wrong default for in/out on dispatch methods.
    inf_in = (p['in'].get('drops') or {}).get('inferred', 0)
    inf_out = (p['out'].get('drops') or {}).get('inferred', 0)
    is_method_shape = label.startswith(".") and label.endswith("()")
    is_iface = node_kind == "iface_method"
    if is_method_shape and ((p['in']['count'] == 0 and inf_in >= 1)
                            or (p['out']['count'] == 0 and inf_out >= 1)):
        cause = ("interface method — runtime sites bind to implementations"
                 if is_iface else "closure dispatch likely")
        bits = []
        if p['in']['count'] == 0 and inf_in >= 1:
            bits.append(f"0 direct callers but {inf_in} inferred")
        if p['out']['count'] == 0 and inf_out >= 1:
            bits.append(f"0 direct callees but {inf_out} inferred")
        # Only suggest impl_of when the focused node actually has incoming
        # impl_of edges. On structurally-typed TS corpora (no explicit
        # `class Foo implements Bar`), impl_of is rarely emitted, and the
        # tail "or `in --kind=impl_of`" sends agents at empty results.
        impl_tail = (" (or `in --kind=impl_of` on interface methods)"
                     if data.get("impl_of_in_count", 0) > 0 else "")
        msg = (f"  hint: {', '.join(bits)} — {cause}. "
               f"AST drops to inferred on dynamic dispatch. "
               f"widen with `--include-inferred --min-confidence 0.85`"
               f"{impl_tail}.")
        _emit_hint(out, "closure_iface_dispatch", msg, cursor, quiet_hints)

    # Module-config: code file focused as a node, 0 contains, file > 50 lines.
    # Likely a `const X = {...}` / data-only module. The current "fully
    # isolated" hint fires when ALL pivots are 0; this catches the case
    # where the file has importers (in > 0) but no decl-shaped contents.
    #
    # Lap-10: gate on focus IS the file hub node, not just `file_type==code`.
    # Symbols inside code files (functions, methods) inherit `file_type==code`
    # too, and a function-leaf naturally has 0 contains/0 methods. The hint
    # was misfiring on `classify_residual_axis()` inside a 749-line file with
    # 21 sibling decls — the decls belong to the FILE, not the function.
    sf = cur.get("source_file") or ""
    label = cur.get("label", "")
    is_file_hub = bool(sf and label == Path(sf).name)
    if (is_code_file and is_file_hub
            and p['contains']['count'] == 0
            and p['methods']['count'] == 0):
        nlines = _file_meta(sf).get("lines") if sf else None
        if isinstance(nlines, int) and nlines > 50:
            _emit_hint(out, "module_config",
                f"  hint: file is {nlines} lines but 0 decls — likely a "
                f"`const X = ...` / data module (config/spec/registry). "
                f"AST extraction skips top-level value-bindings; "
                f"`read [N]` dumps the file inline (cursor already knows "
                f"the path) — no separate Read needed.",
                cursor, quiet_hints)

    # Kind-based steering hints. The header surfaces the [iface]/[impl]/[type]
    # tag, but the tag alone doesn't tell a fresh agent what pivots are
    # productive on each kind. These name the right next op so a one-shot
    # query doesn't waste a turn finding out the hard way. node_kind is
    # already defined at the top of the hint section.
    if node_kind in ("interface", "iface_method"):
        # Adapt the hint to which edge kind actually points to this node.
        # impl_of requires explicit `implements` in source — many TS corpora
        # use structural typing instead. type_ref captures parameter/return
        # type usages and is usually populated for any in-tree type.
        impl_n = data.get("impl_of_in_count", 0)
        type_ref_n = data.get("type_ref_in_count", 0)
        if impl_n > 0:
            iface_msg = (f"  hint: interface — runtime call sites bind to "
                         f"implementations. find them with `in --kind=impl_of` "
                         f"({impl_n}). `methods` lists the contract; `inh` "
                         f"shows interface chains.")
        elif type_ref_n > 0:
            iface_msg = (f"  hint: interface — no explicit `implements` "
                         f"declarations resolved (structural typing). "
                         f"`in --kind=type_ref` ({type_ref_n}) shows where "
                         f"this type is used as a parameter/return/variable "
                         f"annotation. `methods` lists the contract.")
        else:
            iface_msg = (f"  hint: interface — neither `implements` nor "
                         f"type-annotation usages resolved. consider "
                         f"`--include-inferred` for LLM-inferred dispatch, "
                         f"or `methods` to read the contract directly.")
        _emit_hint(out, "interface_kind", iface_msg, cursor, quiet_hints)
    elif node_kind == "type_alias":
        _emit_hint(out, "type_alias_kind",
            "  hint: type alias — usage sites attach as `type_ref` edges. "
            "find them with `in --kind=type_ref`. for runtime impls, find "
            "the class implementing this type.",
            cursor, quiet_hints)
    elif (cur.get("file_type") == "rationale"
          and p['contains']['count'] == 0
          and p['out']['count'] == 0):
        _emit_hint(out, "rationale_node",
            "  hint: rationale node — find the symbol(s) it's anchored to "
            "via `in` (rationale_for edges).",
            cursor, quiet_hints)
    elif (node_kind == "class"
          and p['methods']['count'] == 0
          and p['contains']['count'] == 0):
        inh_n = p.get('inh', {}).get('count', 0)
        impl_of_n = data.get("impl_of_in_count", 0)
        # Only suggest the lookup directions that actually have edges.
        # Recommending `in --kind=impl_of` on a corpus with zero impl_of
        # edges is the same trap the interface_kind hint had.
        suggestions: list[str] = []
        if inh_n:
            suggestions.append(f"`in --kind=inherits` ({inh_n})")
        if impl_of_n:
            suggestions.append(f"`in --kind=impl_of` ({impl_of_n})")
        if suggestions:
            find_clause = f"find implementers via {' or '.join(suggestions)}"
        else:
            find_clause = ("no inherits/impl_of edges resolved — implementers "
                           "may use structural typing; try "
                           "`in --kind=type_ref` for type-annotation usages")
        _emit_hint(out, "empty_class_protocol",
            f"  hint: class with no methods/contains — likely a "
            f"protocol/abstract/marker class. {find_clause}; "
            f"`read` dumps the body inline.",
            cursor, quiet_hints)

    # Docs/rationale-mostly file: a code-file hub whose only contains-children
    # are rationale fragments (doc comments/module docstrings extracted by the
    # python rationale pass). A `contains` listing here returns N rationale
    # rows that look like decls but aren't callable. Saving a wasted pivot.
    is_file_hub = bool(cur.get("source_file") and cur.get("label") == Path(cur.get("source_file") or "").name)
    contains_rat_n = data.get("contains_rationale_count", 0)
    contains_n = p['contains']['count']
    if (is_file_hub and contains_n >= 3 and contains_rat_n == contains_n):
        _emit_hint(out, "docs_mostly_file",
            f"  hint: file's `contains` is {contains_n} rationale fragments "
            f"(doc/comment-extracted, not callable symbols). `read [N]` "
            f"dumps the file inline; for the actual call graph, look at "
            f"another file in this community via `coc`.",
            cursor, quiet_hints)

    # Loose orphan-in-cluster: focus has no in/out and no method/contains
    # structure, but DOES have parent/rat/inh AND a non-trivial community.
    # The hard-orphan branch above only fires when literally everything is
    # zero — which misses the common pattern where a leaf function has an
    # owning parent (so `parent>0`) but no callers (so `in==0`) yet. Without
    # this, the agent gets no signpost between "0 callers" and the cluster
    # context that explains why the function is here.
    has_some_structural = (p['parent']['count'] > 0
                            or p['rat']['count'] > 0
                            or p['inh']['count'] > 0)
    if (p['in']['count'] == 0 and p['out']['count'] == 0
            and p['contains']['count'] == 0 and p['methods']['count'] == 0
            and has_some_structural
            and p.get('coc', {}).get('count', 0) > 0):
        # Already-fired hints (script-leaf, class-shaped, hard-orphan) all
        # require contains/methods OR full emptiness — so this branch is
        # exclusive with them and won't double-emit.
        coc_n = p['coc']['count']
        bits = []
        if p['parent']['count'] > 0:
            bits.append(f"`parent` ({p['parent']['count']}) climbs back up")
        if p['rat']['count'] > 0:
            bits.append(f"`rat` ({p['rat']['count']}) for design notes")
        bits.append(f"`coc` ({coc_n}) for cluster context")
        _emit_hint(out, "loose_orphan_in_cluster",
            f"  hint: 0 in/out/contains/methods but lives in a community — "
            f"{'; '.join(bits)}.",
            cursor, quiet_hints)

    # If any pivot has hidden inferred/low-confidence edges, surface
    # the flag inline once so the affordance is discovered, not just
    # discoverable. Without this `+97inf hidden` is a fact you'd have
    # to know to act on; the hint makes it self-documenting.
    has_hidden_inf = any(
        (p.get(k, {}).get("drops") or {}).get("inferred", 0) > 0
        for k in ("in", "out", "methods", "contains", "coc")
    )
    if has_hidden_inf:
        _emit_hint(out, "hidden_inferred",
            "  hint: hidden inferred edges available — "
            "add `--include-inferred` to widen pivots beyond AST ground truth.",
            cursor, quiet_hints)
    elif data.get("extracted_only") is False:
        # Symmetric case (lap-20 field-report fix): the user opted in with
        # `--include-inferred` but every pivot would render identically to
        # the default. Without this confirmation the flag looks broken
        # (output is byte-identical with/without it). Mirrors the existing
        # asymmetric "hidden inferred edges available" hint above.
        _emit_hint(out, "no_inferred_to_add",
            "  hint: --include-inferred on — no inferred edges to add "
            "(view is identical to AST-only default).",
            cursor, quiet_hints)

    if data.get("last_listing_size") and data.get("last_pivot"):
        out.append(f"  last: {data['last_pivot']}({data['last_listing_size']}) · pick [N]")
    if show_ops:
        # Lap-10: surface `read` in the discoverable ops line. The op shipped
        # in lap-3 (read/body alias, dumps the focused node's source body in-line)
        # but was never exposed in the hint, so consumers kept reaching for
        # `parent → contains --bodies` even when sitting on the function they
        # wanted. The discoverability gap defeated the whole closed-loop intent.
        ops_line = ("  ops: in | out | methods | contains | coc | rat | inh | parent | "
                    "siblings | callers | callees | dependents | dependencies | read | filter <rgx>")
        if data.get("show_history"):
            ops_line += " | back | reset"
        ops_line += " | @<label> | [N]"
        out.append(ops_line)
    return "\n".join(out)


def _render_listing_text(data: dict, *, show_ops: bool, md: bool = False) -> str:
    if data["total"] == 0:
        # An n/a-style sort_label (e.g. "n/a on file nodes — try ...") is
        # how a pivot signals "I don't apply here." Surface that *before*
        # any drop-count text so the agent gets the structural reason
        # rather than a silent empty.
        sort_label = data.get("sort") or ""
        if sort_label.startswith("n/a"):
            return f"  {data['pivot']}: {sort_label}"
        # Even on an empty result, surface the drop count — `in: empty` after
        # extracted-only filtering would otherwise hide that 462 inferred edges
        # were dropped, which is exactly the orientation the agent needs.
        drops = data.get("drops") or {}
        bits = []
        if drops.get("inferred"):
            bits.append(f"+{drops['inferred']} INFERRED hidden")
        if drops.get("low_confidence"):
            bits.append(f"+{drops['low_confidence']} below --min-confidence")
        if drops.get("cross_lang"):
            bits.append(f"+{drops['cross_lang']} cross-lang inferred hidden")
        by_rel = drops.get("by_rel") or {}
        if by_rel:
            total = sum(by_rel.values())
            rels = ",".join(f"{r}:{c}" for r, c in sorted(by_rel.items(), key=lambda x: -x[1]))
            bits.append(f"+{total} hidden via --kind ({rels})")
        if drops.get("archived"):
            bits.append(f"+{drops['archived']} archived hidden")
        if drops.get("external"):
            bits.append(f"+{drops['external']} external (unresolved imports) hidden")
        if drops.get("node_kind"):
            bits.append(f"+{drops['node_kind']} hidden via --node-kind")
        if drops.get("files"):
            bits.append(f"+{drops['files']} files hidden — pass --include-files to widen")
        if drops.get("rationale"):
            bits.append(f"+{drops['rationale']} rationale hidden via --code-only")
        if bits:
            # Name the actual flag rather than say "drop the filter" — the
            # latter misreads as "remove a filter" when the action is
            # actually "add --include-inferred".
            inf_only = (drops.get("inferred") and not drops.get("low_confidence")
                        and not drops.get("by_rel"))
            tail = ("add --include-inferred to show them"
                    if inf_only else "loosen the filter to show")
            suffix = f"  ({', '.join(bits)} — {tail})"
        elif data.get("extracted_only") is False:
            # The --include-inferred flag is on but every filter still
            # returned 0. Flag honored, result genuinely empty — say so
            # so the agent doesn't conclude the flag was dropped silently.
            suffix = "  (--include-inferred on — no edges of this kind exist)"
        else:
            suffix = ""
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
    # Lap-13 field-report fix: on @-disambig listings the `[a-h]` file
    # letters and `[1-N]` pick numbers stack confusingly because both
    # look like indices. The agent picks via [N], not [letter], so the
    # files-table is just visual noise here. Each row shows its full
    # path inline anyway. Suppress on disambig regardless of repeat
    # count.
    pivot_str = data.get("pivot") or ""
    is_disambig = pivot_str.endswith("ambiguous")
    use_table = has_repeat and len(items) >= 3 and not is_disambig

    out: list[str] = []
    kinds_tag = ""
    if data.get("kinds"):
        kinds_tag = f" [--kind={','.join(data['kinds'])}]"
    # When the listing was produced by `filter`, surface the from-count
    # inline as `(M of N)` so the agent immediately sees how many rows
    # were excluded — same place the unfiltered count would be. When the
    # regex failed to compile and fell back to substring match, append
    # `, substring` so the agent knows their pattern wasn't read as a
    # regex (escaping `.[(` etc would be re-tried).
    if data.get("filter"):
        finfo = data["filter"]
        mode_tag = "" if finfo["mode"] == "regex" else f", {finfo['mode']}"
        count_str = f"({data['total']} of {finfo['from']}{mode_tag})"
    else:
        count_str = f"({data['total']})"
    header = f"  {data['pivot']}{kinds_tag} {count_str}"
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
    if drops.get("cross_lang"):
        drop_bits.append(f"+{drops['cross_lang']} cross-lang inferred hidden")
    by_rel = drops.get("by_rel") or {}
    if by_rel:
        total = sum(by_rel.values())
        rels = ",".join(f"{r}:{c}" for r, c in sorted(by_rel.items(), key=lambda x: -x[1]))
        drop_bits.append(f"+{total} hidden via --kind ({rels})")
    if drops.get("archived"):
        drop_bits.append(f"+{drops['archived']} archived hidden")
    if drops.get("external"):
        drop_bits.append(f"+{drops['external']} external (unresolved imports) hidden")
    if drops.get("node_kind"):
        drop_bits.append(f"+{drops['node_kind']} hidden via --node-kind")
    if drops.get("files"):
        drop_bits.append(f"+{drops['files']} files hidden — pass --include-files to widen")
    if drops.get("rationale"):
        drop_bits.append(f"+{drops['rationale']} rationale hidden via --code-only")
    # `where-used` breakdown: split edge-callers from text-mentions in the
    # header so the agent sees the trust split at a glance. Edge hits are
    # AST-grounded; text hits could be string literals, comments, or
    # similarly-named symbols in unrelated code.
    if "edge_hits" in drops or "text_only_hits" in drops:
        eh = drops.get("edge_hits", 0)
        th = drops.get("text_only_hits", 0)
        drop_bits.append(f"{eh} via edges + {th} text-only mentions")
    if drop_bits:
        # Mention the flag inline once when only inferred edges were
        # dropped — discoverable becomes discovered without forcing the
        # agent to recall what flag flips the filter.
        inf_only = (drops.get("inferred") and not drops.get("low_confidence")
                    and not by_rel)
        cue = "  (add --include-inferred to widen)" if inf_only else ""
        header += f"  ({', '.join(drop_bits)}){cue}"
    elif data.get("extracted_only") is False and not drops.get("inferred"):
        # Symmetric case (lap-20 field-report fix): the user passed
        # `--include-inferred` but no inferred edges existed to add to
        # this listing. Confirm the flag was honored so it doesn't look
        # broken (output would otherwise be identical to the default).
        header += "  (--include-inferred on — no inferred edges to add)"
    out.append(header)

    # Cluster hoisting — Lap-3 reported every item line carrying a `cN` tag is
    # opaque integer noise. If all items share one community, hoist it above
    # the table and drop per-item cN entirely; if items span clusters, the
    # per-item tag stays useful as an "out of cluster" marker.
    #
    # Lap-19 fix: when `collapsed > 0`, the surviving rows are first-of-N
    # representatives, so a single-community claim covers ONE row's cluster
    # but not the dupes hidden behind it. Field reporter saw `all in c48`
    # on a `@.compute_metrics` disambig where dupes spanned c5382, c13450,
    # c2806. Don't lie — relabel as primary-cluster-with-sampled-others.
    cids_seen = [it.get("community") for it in items
                 if it.get("community") not in (None, -1)]
    unique_cids = set(cids_seen)
    multi_community = len(unique_cids) > 1
    collapsed_for_cluster = data.get("collapsed", 0)
    if len(unique_cids) == 1:
        cid = next(iter(unique_cids))
        clabel = next((it.get("community_label") for it in items
                       if it.get("community") == cid), None)
        cstr = f"c{cid}={clabel}" if clabel else f"c{cid}"
        # `coc` pivot already names the cluster in its header — don't double-stamp.
        pivot_str = data.get("pivot") or ""
        already_in_header = f"c{cid}" in pivot_str
        if not already_in_header:
            if collapsed_for_cluster:
                out.append(f"  primary cluster: {cstr} "
                           f"(dupes hidden by collapse may span others)")
            else:
                out.append(f"  all in {cstr}")

    # Lap-9 collapse: surface a single-line note announcing the collapse
    # totals. Per-row dupe rendering happens below in the item loop —
    # `dupe_count` items render as `label ×N (samples: ...)`.
    collapsed = data.get("collapsed", 0)
    if collapsed:
        out.append(f"  note: collapsed {collapsed} dupe-label rows — "
                   f"`[N]` picks the first member; pivot via "
                   f"`@<dir>/<file>/<sym>` for a specific dupe. "
                   f"Pass `--no-collapse` to expand.")

    # Auto-widen note: 0 extracted, fell back to inferred. Without this the
    # `(auto-widened)` tag in the pivot header is opaque — the agent doesn't
    # know that the rows below are NOT AST ground truth, just that something
    # got widened. Stating the contract inline ("[inf] tags") plus the manual
    # equivalent (`--include-inferred`) keeps trust calibrated.
    if drops.get("auto_widened_from_extracted"):
        out.append(f"  note: 0 extracted; auto-widened to {data['total']} "
                   f"inferred edge(s). rows below carry [inf@<score>] tags — "
                   f"name-collision risk; pass `--include-inferred` "
                   f"explicitly to silence this auto-widen.")

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
        if item.get("is_test"):
            ft_tag += " [test]"
        if item.get("is_archived"):
            ft_tag += " [archived]"
        kind = item.get("node_kind")
        if kind == "interface" or kind == "iface_method":
            ft_tag += " [iface]"
        elif kind == "impl_method":
            ft_tag += " [impl]"
        elif kind == "type_alias":
            ft_tag += " [type]"
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
        # md-mode wraps the label as `[label](src:line)`. Truncation happens
        # first so the link's anchor text matches what would otherwise be
        # printed; the URL still points at the full file:line target.
        label = _maybe_link(label, item.get("source_file"),
                            item.get("source_location"), md)
        cid = item.get("community", -1)
        deg = item.get("degree", 0)
        # Only show per-item meta when the file-table didn't already absorb it.
        meta = "" if (use_table and src in file_to_letter) else _meta_tag(item)
        # Per-item cluster tag only when items span multiple communities — see
        # the hoist block above. Single-community listings already announced
        # the cluster in the header, so keep item lines tight.
        cstr = f"c{cid:<3} " if multi_community else ""
        # Lap-9 collapsed row: when this item represents a group of >=5
        # same-label dupes, render the count + sample paths inline. The
        # `[N]` index targets the group's first member; an agent who
        # needs a specific dupe pivots via `@<dir>/<file>/<sym>`.
        dupe_count = item.get("dupe_count", 0)
        # `where-used` superop tags rows that came from text-mention scan
        # rather than graph edges. Append `[mentions L<line>...]` so the
        # agent sees the discovery source — without it, edge-discovered
        # and text-discovered rows visually merge.
        mention_lines = item.get("mention_lines")
        if mention_lines:
            line_str = ",".join(f"L{ln}" for ln in mention_lines[:3])
            edge_tag = (edge_tag + f" [mentions {line_str}]"
                        if edge_tag else f" [mentions {line_str}]")
        if dupe_count >= _DUPE_COLLAPSE_THRESHOLD:
            samples = item.get("dupe_samples") or []
            sample_paths = []
            for s in samples:
                sf = s.get("source_file") or ""
                sl = s.get("source_location") or ""
                if sf:
                    short = "/".join(Path(sf).parts[-2:]) if Path(sf).parts else sf
                    if sl:
                        ln = sl[1:] if sl.startswith("L") else sl
                        sample_paths.append(f"{short}:{ln}")
                    else:
                        sample_paths.append(short)
            samples_str = ", ".join(sample_paths) if sample_paths else "—"
            out.append(f"    [{i:>2}] {label:<44} ×{dupe_count}  "
                       f"{cstr}d={deg:<4}  {ft_tag}{edge_tag}".rstrip())
            out.append(f"         samples: {samples_str}")
        else:
            out.append(f"    [{i:>2}] {label:<44} {cstr}d={deg:<4} {src_str}{ft_tag}{edge_tag}{meta}")
        # Optional body preview: surfaces stub/redirect/decorator-only patterns
        # without an actual Read. Truncate long lines so the preview doesn't
        # blow out the line budget; one signal per line is enough.
        for line in (item.get("body_preview") or []):
            shown = line if len(line) <= 90 else line[:89] + "…"
            out.append(f"         | {shown}")

    if data["total"] > data["showing"]:
        out.append(f"    … +{data['total'] - data['showing']} more  · raise --limit to see more")
    # Lap-8 procedural-file footer: when `contains` was called on a file
    # that's mostly inline statements (heuristic in _pivot_data), set
    # expectations. Without this the agent reads "4 contained decls" as
    # "this file has nothing else" when in fact most of the body lives
    # in inline statements that AST-extraction doesn't surface as nodes.
    if drops.get("procedural_lines"):
        lines = drops["procedural_lines"]
        n_decls = drops.get("procedural_decls", 0)
        out.append(f"    note: file is {lines} lines but only {n_decls} top-level decls — "
                   f"~{lines - n_decls * 5} inline statements not extracted as nodes "
                   f"(procedural script). `read [N]` dumps the file inline; "
                   f"the cursor already has the path.")
    # Lap-21 #6: --transitive can silently no-op (focus is a symbol, or a
    # file with direct out-edges). Surface the reason so agents don't
    # wonder whether the flag did anything.
    if drops.get("transitive_noop_reason"):
        out.append(f"    note: {drops['transitive_noop_reason']}")
    if show_ops:
        out.append("  pick: [N] to focus · @<label> jump")
    return "\n".join(out)


# --- pivot data sources ----------------------------------------------------

def _filtered_edges(edges_iter, extracted_only, min_confidence):
    return [e for e in edges_iter if _passes_confidence(e, extracted_only, min_confidence)]


def _focus_locality_keys(G: nx.DiGraph, focus_nid: str) -> tuple[str, set[str]]:
    """Return (focus source_file, parent class nids) for locality scoring.

    Inferred-edge listings get a locality bucket: same-class > same-file >
    elsewhere. Without this, inferred edges with name collisions across
    unrelated classes (e.g. `.destroy()` on five different classes) sort
    by graph degree and the agent picks the wrong one. Lap-6 field report.
    """
    src = G.nodes[focus_nid].get("source_file") or ""
    parents = {u for u in G.predecessors(focus_nid)
               if G.edges[u, focus_nid].get("relation") in ("contains", "method")}
    return src, parents


def _locality_bucket(G: nx.DiGraph, nid: str,
                     focus_src: str, focus_parents: set[str]) -> int:
    """0=same-parent (class/file), 1=same-source-file, 2=elsewhere.

    `focus_parents` is a set, not a single id, so a class with multiple
    structural parents (rare but possible) doesn't randomly pick one
    representative. A node sharing ANY parent with the focus counts as
    same-class for ranking.
    """
    nid_parents = {u for u in G.predecessors(nid)
                   if G.edges[u, nid].get("relation") in ("contains", "method")}
    if focus_parents and nid_parents & focus_parents:
        return 0
    nid_src = G.nodes[nid].get("source_file") or ""
    if focus_src and nid_src == focus_src:
        return 1
    return 2


def _rank_key(G: nx.DiGraph, nid: str, edge: dict,
              focus_src: str = "", focus_parents: set[str] | None = None
              ) -> tuple[int, int, int, int]:
    """Sort key for `in`/`out` listings.

    Order: (1) extracted before inferred, (2) for inferred edges only,
    locality bucket (same-class > same-file > elsewhere), (3) public
    over rationale, (4) higher degree.

    Locality only kicks in for inferred edges — extracted edges are
    AST-derived ground truth and shouldn't be reordered by where the
    target lives.
    """
    is_extracted = edge.get("confidence") == "EXTRACTED"
    ext_first = 0 if is_extracted else 1
    locality = (0 if is_extracted
                else _locality_bucket(G, nid, focus_src,
                                       focus_parents or set()))
    is_rat = 1 if G.nodes[nid].get("file_type") == "rationale" else 0
    deg = G.in_degree(nid) + G.out_degree(nid)
    return (ext_first, locality, is_rat, -deg)


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
                depth: int = 1,
                include_files: bool = False,
                code_only: bool = False,
                transitive: bool = False
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

    # Focus locality keys: precomputed once so per-candidate rank calls
    # don't repeat the predecessor walk. Used by inferred-edge ranking
    # to push same-class/same-file matches above name-collision noise.
    focus_src, focus_parents = _focus_locality_keys(G, nid)

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
            preds.sort(key=lambda x: _rank_key(G, x, edge_for[x],
                                                focus_src, focus_parents))
            sort_label = f"transitive (depth≤{depth}), extracted-first, then locality, then degree desc"
        else:
            preds = [u for u in G.predecessors(nid)
                     if _semantic_pass(G.edges[u, nid].get("relation") or "", kinds)
                     and _passes_confidence(G.edges[u, nid], extracted_only, min_confidence)]
            edge_for = {u: G.edges[u, nid] for u in preds}
            preds.sort(key=lambda x: _rank_key(G, x, edge_for[x],
                                                focus_src, focus_parents))
            sort_label = "extracted-first, then locality, then degree desc"
        drops = _drop_breakdown(all_in, extracted_only, min_confidence)
        kd = _kind_drops(full_semantic_in, kinds)
        if kd:
            drops["by_rel"] = kd
        return ("↗in", preds, edge_for, sort_label, drops)

    if key == "out":
        # Script-leaf transitive route. When `out` from a file node is empty
        # (the file has no direct outbound semantic edges — the call graph
        # lives one hop in via `contains`) and `--transitive` is set, walk
        # through contained children and aggregate their out edges. Without
        # this the script-leaf hint advises the manual two-call drill;
        # `--transitive` collapses it into one. We do this BEFORE the
        # normal out path so a file with 0 out and >0 contains routes
        # through; a file with >0 direct out gets normal handling and
        # transitive is a no-op.
        # Lap-21 #6: track whether --transitive was a no-op so the renderer
        # can surface a notice. The flag silently degrades on (a) symbols,
        # which already have direct out, and (b) files that already have
        # direct out-edges. Agents shouldn't have to wonder if the flag
        # did anything.
        transitive_noop_reason: str | None = None
        # Use a stricter check than _is_file_node here (which incorrectly
        # classifies low-degree function stubs as files via its "single
        # contains edge" heuristic). For --transitive the question is
        # specifically "is this a file-as-script-leaf where the call
        # graph lives one hop deep via contains?" That's strictly
        # `label == basename(source_file)`.
        focus_attrs = G.nodes[nid]
        focus_sf = focus_attrs.get("source_file") or ""
        focus_label = focus_attrs.get("label", "")
        is_real_file = bool(focus_sf and focus_label == Path(focus_sf).name)
        if transitive and not is_real_file:
            transitive_noop_reason = (
                "--transitive: only applies to file nodes — focus is a "
                "symbol; `out` is already its direct outbound edges"
            )
        if transitive and is_real_file:
            direct_out = [v for v in G.successors(nid)
                          if G.edges[nid, v].get("relation") not in _STRUCTURAL
                          and _passes_confidence(G.edges[nid, v], extracted_only,
                                                 min_confidence)]
            contained = [v for v in G.successors(nid)
                         if G.edges[nid, v].get("relation") == "contains"
                         and _passes_confidence(G.edges[nid, v], extracted_only,
                                                 min_confidence)]
            if direct_out:
                transitive_noop_reason = (
                    f"--transitive: not applied — file already has "
                    f"{len(direct_out)} direct out-edge(s); script-leaf "
                    f"transitive only fires when out=0"
                )
            elif not direct_out and contained:
                collected: list[str] = []
                tedge_for: dict[str, dict] = {}
                seen: set[str] = {nid, *contained}
                aggregated_dropbreak: list[dict] = []
                for child in contained:
                    for v in G.successors(child):
                        e = G.edges[child, v]
                        rel = e.get("relation") or ""
                        if rel in _STRUCTURAL:
                            continue
                        if not _semantic_pass(rel, kinds):
                            continue
                        aggregated_dropbreak.append(e)
                        if not _passes_confidence(e, extracted_only, min_confidence):
                            continue
                        if v in seen:
                            continue
                        seen.add(v)
                        collected.append(v)
                        tedge_for[v] = e
                collected.sort(key=lambda x: -(G.in_degree(x) + G.out_degree(x)))
                drops = _drop_breakdown(aggregated_dropbreak, extracted_only,
                                         min_confidence)
                kd = _kind_drops(aggregated_dropbreak, kinds)
                if kd:
                    drops["by_rel"] = kd
                return ("↘out (via contains)", collected, tedge_for,
                        f"transitive via {len(contained)} contained, "
                        f"then degree desc",
                        drops)
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
            succs.sort(key=lambda x: _rank_key(G, x, edge_for[x],
                                                focus_src, focus_parents))
            sort_label = f"transitive (depth≤{depth}), extracted-first, then locality, then degree desc"
        else:
            succs = [v for v in G.successors(nid)
                     if _semantic_pass(G.edges[nid, v].get("relation") or "", kinds)
                     and _passes_confidence(G.edges[nid, v], extracted_only, min_confidence)]
            edge_for = {v: G.edges[nid, v] for v in succs}
            succs.sort(key=lambda x: _rank_key(G, x, edge_for[x],
                                                focus_src, focus_parents))
            sort_label = "extracted-first, then locality, then degree desc"
        drops = _drop_breakdown(all_out, extracted_only, min_confidence)
        kd = _kind_drops(full_semantic_out, kinds)
        if kd:
            drops["by_rel"] = kd
        if transitive_noop_reason:
            drops["transitive_noop_reason"] = transitive_noop_reason
        return ("↘out", succs, edge_for, sort_label, drops)

    if key == "methods":
        all_m = [G.edges[nid, v] for v in G.successors(nid)
                 if G.edges[nid, v].get("relation") == "method"]
        succs = [v for v in G.successors(nid)
                 if G.edges[nid, v].get("relation") == "method"
                 and _passes_confidence(G.edges[nid, v], extracted_only, min_confidence)]
        # Lap-8: file nodes don't carry methods. The pivot returns 0 either
        # way, but a file-shaped n/a directive points the agent at `contains`
        # so a chained pick (`methods 1`) doesn't blindly error on the empty
        # listing. Renderer surfaces sort_label starting with "n/a" as the
        # message, replacing the empty body.
        # Function check first: `_is_file_node` mis-claims module-level
        # `name()` nodes with degree<=1 as file nodes (its `is structurally
        # isolated by definition` heuristic) — would route function queries
        # to the wrong directive. Function-kind is more specific.
        if not succs and _is_function_kind(G, nid):
            return ("◉methods", [], {},
                    "n/a on function nodes — try `out` for callees, `in` for callers, or `read` for the body", {})
        from graphify.analyze import _is_file_node as _isf
        if not succs and _isf(G, nid):
            return ("◉methods", [], {},
                    "n/a on file nodes — try `contains` for top-level decls", {})
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
        # Function/method nodes don't `contain` anything — they have
        # callees and callers. Same redirect as `methods` so the agent
        # learns the right pivot for the kind.
        if not succs and _is_function_kind(G, nid):
            return ("◇contains", [], {},
                    "n/a on function nodes — try `out` for callees, `in` for callers, or `read` for the body", {})
        succs.sort(key=lambda x: -(G.in_degree(x) + G.out_degree(x)))
        drops = _drop_breakdown(all_c, extracted_only, min_confidence)
        # Lap-8: procedural-file footer. `@cartography-build.ts contains`
        # returned 4 helpers but the file was 270 lines mostly inline
        # statements with no AST decl node. Set expectations: when the file
        # has substantially more lines than declarations would account for,
        # flag it as procedural so the agent doesn't read "almost empty".
        # Heuristic: file with >100 lines AND <10 contained decls AND
        # ratio (lines/contains) > 30 is mostly inline. The signal is
        # intentionally coarse — better to under-flag than to false-positive
        # on dense library files.
        from graphify.analyze import _is_file_node as _isf
        if _isf(G, nid):
            sf = G.nodes[nid].get("source_file") or ""
            lines = _file_meta(sf).get("lines") if sf else None
            n_decls = len(succs)
            if (isinstance(lines, int) and lines > 100 and n_decls < 10
                    and (n_decls == 0 or lines / max(1, n_decls) > 30)):
                drops["procedural_lines"] = lines
                drops["procedural_decls"] = n_decls
        return ("◇contains", succs, {v: G.edges[nid, v] for v in succs}, "degree desc",
                drops)

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

    if key in ("dependents", "dependencies"):
        # Sugar for transitive callers/callees: `dependents` answers
        # "what depends on this?" by walking N hops back through call edges;
        # `dependencies` answers "what does this depend on?" walking forward.
        # The naming maps onto how engineers talk about coupling — they
        # don't think in graph direction, they think in dependency direction.
        # Defaults to depth=3 unless the caller passed --depth explicitly.
        # Routes through the callers/callees codepath so kind=calls and
        # the rank/drop logic stay in one place.
        base_key = "in" if key == "dependents" else "out"
        eff_depth = max(depth, 3)
        _pname, ids, edges, sort_label, drops = _pivot_data(
            G, communities, cursor, base_key,
            extracted_only=extracted_only,
            min_confidence=min_confidence,
            kinds={"calls"},
            depth=eff_depth,
        )
        glyph = ("◀dependents" if key == "dependents" else "▶dependencies")
        # Append depth marker so the consumer sees it walked transitively.
        glyph = f"{glyph}(depth≤{eff_depth})"
        return (glyph, ids, edges, sort_label, drops)

    if key == "siblings":
        # File nodes don't have structural siblings in the way symbols do —
        # the parent of a file is "the project", which has thousands of
        # peers. Return an empty listing with a directive sort_label so
        # the renderer can surface the pivot mismatch instead of a silent
        # empty. The frontier card showed sib counts > 0 elsewhere; an
        # empty here without explanation looked like a bug.
        if _is_file_node(G, nid):
            return ("◈sib", [], {},
                    "n/a on file nodes — try `contains` (symbols inside) or `in` (importers)",
                    {})
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
        # No edge filter applies (members are nodes, not edges) — but lap-8
        # adds a node-shape filter: hide file-level hubs by default. Reporter
        # said `@DecodeEngine coc` returned 7/25 file nodes, which is a
        # different kind of answer than "what symbols co-occur with this".
        # File hubs sit in every community their members do; under degree-
        # sort they crowd the top. `include_files=True` opts them back in.
        cid = G.nodes[nid].get("community", -1)
        if cid == -1:
            return ("⊕coc", [], {}, "", {})
        all_members = [n for n in communities.get(cid, []) if n != nid]
        # Filter file-level hubs (label == filename basename). We use this
        # tight test rather than analyze._is_file_node because the latter
        # also flags method stubs and isolated module funcs, which DO
        # belong in coc.
        from pathlib import Path as _Path
        def _is_file_hub(n: str) -> bool:
            attrs = G.nodes[n]
            sf = attrs.get("source_file") or ""
            return bool(sf and attrs.get("label") == _Path(sf).name)
        if include_files:
            members = list(all_members)
            file_drops: dict[str, int] = {}
        else:
            members = [n for n in all_members if not _is_file_hub(n)]
            file_count = len(all_members) - len(members)
            file_drops = {"files": file_count} if file_count else {}
        # Lap-16 Python field-report: a 514-member coc was mostly rationale
        # nodes (extracted docstring fragments). `--code-only` strips them
        # so the listing is the actual code-symbol neighbourhood. Off by
        # default to preserve current callers; agents who want orientation
        # rather than the megablob pass the flag.
        if code_only:
            before = len(members)
            members = [n for n in members
                       if G.nodes[n].get("file_type") == "code"]
            rat_count = before - len(members)
            if rat_count:
                file_drops = dict(file_drops)
                file_drops["rationale"] = rat_count
        members.sort(key=lambda x: -(G.in_degree(x) + G.out_degree(x)))
        labels = G.graph.get("community_labels") if hasattr(G, "graph") else None
        clabel = (labels or {}).get(cid)
        pname = f"⊕coc(c{cid}={clabel})" if clabel else f"⊕coc(c{cid})"
        return (pname, members, {}, "degree desc", file_drops)

    return ("", [], {}, "", {})


# --- focus resolution ------------------------------------------------------


# --- body-text search ------------------------------------------------------

def _attribute_match_to_node(match_line: int,
                              node_starts: list[tuple[int, str]]) -> str | None:
    """Pick the deepest node whose start_line <= match_line.

    `node_starts` is a list of (start_line, nid) pre-sorted ascending by
    start_line. Returns the nid of the LAST (highest start_line) node
    whose start_line <= match_line — that's the most-deeply-nested
    enclosing decl in the typical AST emission order.
    """
    chosen: str | None = None
    for start_line, nid in node_starts:
        if start_line <= match_line:
            chosen = nid
        else:
            break
    return chosen


def search_bodies(G: nx.DiGraph,
                  pattern: str, *,
                  kind: str = "code",
                  archived_mode: str = "no",
                  limit: int = 50,
                  context: int = 0,
                  by_symbol: bool = False) -> dict:
    """Body-text search across all nodes that have source_file + source_location.

    Returns hits with the containing node's metadata (label, file:line,
    community, degree). Eliminates the grep fallback for "where does
    spectral_coherence appear?" — the agent stays in symbol-aware
    coordinates without dropping to file:line tuples.

    `kind`: filter on file_type.
        - "code" (default): code-file nodes only. Skips rationale fragments
          since they're doc-comment text — usually noise for the agent's
          "where is this used in code" question.
        - "rationale": rationale nodes only.
        - "all": both.
    `archived_mode`: "no" (skip archived, default), "all", "only".
    `limit`: max hits returned. Per-file scan continues past the limit
        only to compute the truncated count.
    `context`: lines of pre/post context around each match line. Default 0
        (just the match line). Up to 3 saves a separate `read` round-trip.
    `by_symbol`: lap-21 #7. When True, collapse same-symbol hits into a
        single row with `match_count` + `match_lines`. The default
        per-line emission is right when you want every site; `--by-symbol`
        is right when "where is X used?" — e.g. 4 hits all inside
        `stagedDecode()` should read as 1 symbol with 4 lines.

    The pattern is compiled as a case-insensitive regex; on `re.error` we
    fall back to a literal case-insensitive substring match. The mode is
    surfaced in the result so the agent knows whether their `[(` would
    be treated as regex or substring.
    """
    try:
        rx = re.compile(pattern, re.IGNORECASE)
        match_fn = rx.search
        mode = "regex"
    except re.error:
        needle = pattern.lower()
        match_fn = lambda s: needle in s.lower()  # noqa: E731
        mode = "substring"

    # Group nodes by source_file with parsed start lines. We only consider
    # nodes that have BOTH source_file and a parseable source_location;
    # nodes without source coordinates can't carry a body match.
    by_file: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for nid, attrs in G.nodes(data=True):
        sf = attrs.get("source_file")
        loc = attrs.get("source_location") or ""
        if not sf:
            continue
        ft = attrs.get("file_type") or ""
        if kind == "code" and ft != "code":
            continue
        if kind == "rationale" and ft != "rationale":
            continue
        if archived_mode == "no" and _is_archived_path(sf):
            continue
        if archived_mode == "only" and not _is_archived_path(sf):
            continue
        if not loc.startswith("L"):
            continue
        try:
            start_line = int(loc[1:].split("-", 1)[0].split(":", 1)[0])
        except ValueError:
            continue
        by_file[sf].append((start_line, nid))

    hits: list[dict] = []
    truncated_count = 0
    files_scanned = 0
    files_read_failed = 0

    for sf, node_starts in by_file.items():
        node_starts.sort(key=lambda x: x[0])
        try:
            with open(sf, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            files_read_failed += 1
            continue
        files_scanned += 1
        for i, raw in enumerate(lines, 1):
            line = raw.rstrip("\n")
            if not match_fn(line):
                continue
            owner_nid = _attribute_match_to_node(i, node_starts)
            if owner_nid is None:
                continue
            if len(hits) >= limit:
                truncated_count += 1
                continue
            owner_attrs = G.nodes[owner_nid]
            ctx_pre: list[str] = []
            ctx_post: list[str] = []
            if context > 0:
                for k in range(max(0, i - 1 - context), i - 1):
                    ctx_pre.append(lines[k].rstrip("\n"))
                for k in range(i, min(len(lines), i + context)):
                    ctx_post.append(lines[k].rstrip("\n"))
            hits.append({
                "id": owner_nid,
                "label": owner_attrs.get("label", owner_nid),
                "source_file": sf,
                "source_location": owner_attrs.get("source_location"),
                "match_line": i,
                "snippet": line.strip(),
                "ctx_pre": ctx_pre,
                "ctx_post": ctx_post,
                "community": owner_attrs.get("community"),
                "degree": G.degree(owner_nid),
                "node_kind": owner_attrs.get("node_kind"),
                "file_type": owner_attrs.get("file_type"),
            })

    # Rank: archived last, then degree desc (load-bearing first), then label.
    def _rank(h: dict) -> tuple[int, int, str]:
        is_arch = 1 if _is_archived_path(h["source_file"]) else 0
        return (is_arch, -h["degree"], h["label"])
    hits.sort(key=_rank)

    grouped_total = 0
    if by_symbol:
        # Group hits by owner node id, preserving the rank order
        # established above. The representative per group is the
        # first-encountered hit (which keeps the smallest match_line
        # naturally because file scans are line-ascending). Carry
        # `match_count` + `match_lines` on the representative; the
        # renderer prints them as `× N` and shows the first few line
        # numbers as a flag-without-cost orientation aid.
        by_owner: dict[str, dict] = {}
        for h in hits:
            owner = h["id"]
            if owner not in by_owner:
                rep = dict(h)
                rep["match_count"] = 1
                rep["match_lines"] = [h["match_line"]]
                by_owner[owner] = rep
            else:
                rep = by_owner[owner]
                rep["match_count"] += 1
                if len(rep["match_lines"]) < 8:
                    rep["match_lines"].append(h["match_line"])
        grouped_total = len(hits) - len(by_owner)
        hits = list(by_owner.values())

    return {
        "type": "search",
        "pattern": pattern,
        "mode": mode,
        "kind": kind,
        "archived_mode": archived_mode,
        "limit": limit,
        "hits": hits,
        "total": len(hits),
        "truncated": truncated_count,
        "files_scanned": files_scanned,
        "files_read_failed": files_read_failed,
        "by_symbol": by_symbol,
        "grouped": grouped_total,
    }


def _render_search_text(data: dict, *, md: bool = False) -> str:
    """Plain-text render for `graphify search` results.

    Header names the pattern, mode, scan stats. Each hit shows
    `[N] @<label>  file:match_line  d=K  cN`  with the matched line
    shown indented below. With context>0, pre/post lines are interleaved.
    """
    pat = data["pattern"]
    mode = data["mode"]
    total = data["total"]
    truncated = data["truncated"]
    files_scanned = data["files_scanned"]
    files_failed = data["files_read_failed"]
    kind = data["kind"]

    out: list[str] = []
    grouped = data.get("grouped", 0)
    by_symbol = data.get("by_symbol", False)
    if by_symbol:
        # `total` post-grouping = symbol count; surface the underlying
        # line-hit total (= symbols + grouped) so the agent sees both.
        line_total = total + grouped
        header = (f"  search /{pat}/ ({mode}, kind={kind}, --by-symbol): "
                  f"{total} symbol(s) covering {line_total} line(s)")
    else:
        header = f"  search /{pat}/ ({mode}, kind={kind}): {total} hit(s)"
    if total == 0:
        if files_scanned == 0:
            return f"{header}  (no files scanned — graph may have no source-located nodes)"
        return f"{header}  ({files_scanned} files scanned)"
    if truncated:
        header += f" — showing first {data['limit']}, +{truncated} more"
    header += f"  ({files_scanned} files scanned"
    if files_failed:
        header += f", {files_failed} unreadable"
    header += ")"
    # Lap-20 field-report fix: when truncation hides >50% of results OR
    # the hidden count is large in absolute terms, promote a top-of-output
    # banner. The footer-only `+M more` is easy to miss when scanning
    # `8 hit(s)` and reading down — the banner reframes the listing as
    # truncated up front so the agent narrows the regex or raises --limit
    # before iterating on possibly-irrelevant top matches. Keeps the
    # footer fallback for mild truncation. `⚠` matches the disambig
    # `⚠ ambiguous:` glyph for cross-verb consistency.
    grand_total = total + truncated
    if truncated and (truncated > 100 or truncated > total):
        out.append(
            f"  ⚠ showing {total} of {grand_total} — narrow the regex "
            f"or use --limit {grand_total} to see more"
        )
    out.append(header)

    # File-letter table when files repeat.
    file_to_letter: dict[str, str] = {}
    file_use_count: dict[str, int] = defaultdict(int)
    for h in data["hits"]:
        sf = h["source_file"]
        file_use_count[sf] += 1
        if sf not in file_to_letter:
            file_to_letter[sf] = _file_letter(len(file_to_letter))
    use_table = any(c >= 2 for c in file_use_count.values()) and len(data["hits"]) >= 3
    if use_table:
        out.append("  files:")
        for sf, letter in file_to_letter.items():
            count = file_use_count[sf]
            out.append(f"    [{letter}] {sf}  ×{count}")

    for i, h in enumerate(data["hits"], 1):
        sf = h["source_file"]
        cid = h.get("community")
        cstr = f"c{cid}" if cid not in (None, -1) else ""
        kind_tag = ""
        nk = h.get("node_kind") or ""
        if nk == "interface" or nk == "iface_method":
            kind_tag = " [iface]"
        elif nk == "impl_method":
            kind_tag = " [impl]"
        elif nk == "type_alias":
            kind_tag = " [type]"
        elif h.get("file_type") == "rationale":
            kind_tag = " [rat]"
        if use_table and sf in file_to_letter:
            loc_str = f"{file_to_letter[sf]}:{h['match_line']}"
        else:
            loc_str = f"{sf}:{h['match_line']}"
        label = h.get("label") or h["id"]
        if md:
            label = _maybe_link(label, sf, f"L{h['match_line']}", md)
        # Lap-21 #7: with --by-symbol, append `×N (lines: a,b,c)` so a
        # row that collapses 4 hits inside one symbol shows that fact.
        match_count = h.get("match_count", 1)
        line_a = (f"    [{i:>2}] @{label:<40} d={h['degree']:<4} {cstr:<5} "
                  f"{loc_str}{kind_tag}").rstrip()
        if match_count > 1:
            mlines = h.get("match_lines") or []
            preview = ",".join(str(n) for n in mlines[:5])
            tail = "…" if len(mlines) > 5 or match_count > len(mlines) else ""
            line_a += f"  ×{match_count} (lines: {preview}{tail})"
        out.append(line_a)
        # Pre-context, then the matched line, then post-context.
        for ctx in h.get("ctx_pre") or []:
            out.append(f"          | {ctx[:120]}")
        snip = h["snippet"]
        if len(snip) > 160:
            snip = snip[:159] + "…"
        out.append(f"        > {snip}")
        for ctx in h.get("ctx_post") or []:
            out.append(f"          | {ctx[:120]}")

    return "\n".join(out)


# --- file shape summary ----------------------------------------------------

_SHAPE_DEFAULT_LIMIT = 8


def shape_file(G: nx.DiGraph, file_nid: str, *,
               limit: int | None = _SHAPE_DEFAULT_LIMIT) -> dict:
    """Summarize a file's structure: class/fn/const/import counts + the
    longest top-level function by line span.

    The agent's `wc -l + ctags` equivalent: "what's in this file at a
    glance, before I commit to drilling into it?" Saves a `contains`
    pivot + a Read for orientation. Operates on the existing graph
    nodes/edges plus a one-shot file read for line spans.

    Returns a dict with `classes`, `fns`, `consts`, `imports`,
    `longest_fn` (None or {label, lines}), `total_lines`,
    `source_file`, `label`.
    """
    fattrs = G.nodes[file_nid]
    sf = fattrs.get("source_file") or ""
    label = fattrs.get("label") or file_nid

    # Direct children via contains/method edges. methods are emitted as a
    # separate relation by some extractors, so include both.
    children: list[tuple[str, dict]] = []
    for v in G.successors(file_nid):
        e = G.edges[file_nid, v]
        if e.get("relation") in ("contains", "method"):
            children.append((v, G.nodes[v]))

    classes: list[str] = []
    iface_or_type: list[str] = []
    fns: list[str] = []
    consts: list[str] = []
    rationale: list[str] = []

    for nid, attrs in children:
        kind = attrs.get("node_kind") or ""
        ft = attrs.get("file_type") or ""
        lab = attrs.get("label") or ""
        if ft == "rationale":
            rationale.append(nid)
            continue
        # Prefer node_kind when set (TS extractor populates it). When the
        # extractor doesn't tag node_kind (Python pass leaves it empty),
        # fall back to label-shape: `Capitalized` no-parens → class,
        # `name()` or `.name()` → function, anything else → const/other.
        if kind == "class":
            classes.append(nid)
        elif kind in ("interface", "type_alias"):
            iface_or_type.append(nid)
        elif kind in ("impl_method", "iface_method") or kind == "function":
            fns.append(nid)
        elif lab.endswith("()"):
            fns.append(nid)
        elif (lab and not lab.startswith(".") and lab[0:1].isalpha()
              and lab[0].isupper() and "(" not in lab and " " not in lab
              and any(c.islower() for c in lab)):
            # Class-shape fallback: PascalCase identifier (leading upper
            # AND at least one lowercase letter) with no parens. Catches
            # Python classes whose extractor doesn't set node_kind.
            # Excludes `.method()` (leading dot), rationale text (spaces),
            # and ALL_CAPS constants like `MAX_RETRIES` / `DEBUG`.
            classes.append(nid)
        else:
            # Anything else: ALL_CAPS constants, snake_case bindings,
            # registry dicts, etc. Useful as a "this file has N non-fn
            # nodes worth a `read`" signal, even though AST extraction
            # only surfaces a subset of these.
            consts.append(nid)

    # imports edges out of the file
    imports = sum(1 for v in G.successors(file_nid)
                  if G.edges[file_nid, v].get("relation") == "imports")

    # Compute longest fn by line span. Read the file once, sort all
    # children by start_line, attribute spans by next-sibling-start.
    file_lines: list[str] | None = None
    try:
        with open(sf, "r", encoding="utf-8", errors="replace") as f:
            file_lines = f.readlines()
    except OSError:
        file_lines = None

    fn_spans: dict[str, int] = {}
    if file_lines:
        # Two-tier span resolution. When the extractor stamped a precise
        # end-line via `L<start>-<end>` (TS extractor for functions/methods),
        # use that — the actual closing brace, not next-sibling-start.
        # Otherwise fall back to next-sibling-start as an approximation.
        # Without this, the LAST top-level fn in a procedural file absorbs
        # all trailing module-level statements (`const config = {...}`,
        # `console.log(...)`, etc.) and reports inflated lengths.
        starts: list[tuple[int, str, int | None]] = []
        for nid, attrs in children:
            loc = attrs.get("source_location") or ""
            if not loc.startswith("L"):
                continue
            try:
                rest = loc[1:].split(":", 1)[0]
                if "-" in rest:
                    start_str, end_str = rest.split("-", 1)
                    start = int(start_str)
                    explicit_end: int | None = int(end_str)
                else:
                    start = int(rest)
                    explicit_end = None
            except ValueError:
                continue
            starts.append((start, nid, explicit_end))
        starts.sort(key=lambda x: x[0])
        for idx, (start, nid, explicit_end) in enumerate(starts):
            if explicit_end is not None:
                end = explicit_end
            elif idx + 1 < len(starts):
                end = starts[idx + 1][0] - 1
            else:
                end = len(file_lines)
            fn_spans[nid] = max(1, end - start + 1)

    longest_fn: dict | None = None
    if fns and fn_spans:
        candidates = [(fn_spans.get(nid, 0), nid) for nid in fns
                      if nid in fn_spans]
        if candidates:
            best_lines, best_nid = max(candidates, key=lambda x: x[0])
            longest_fn = {
                "label": G.nodes[best_nid].get("label", best_nid),
                "lines": best_lines,
                "source_location": G.nodes[best_nid].get("source_location"),
            }

    total_lines = len(file_lines) if file_lines else None
    return {
        "type": "shape",
        "label": label,
        "source_file": sf,
        "classes": len(classes),
        "interfaces_or_types": len(iface_or_type),
        "fns": len(fns),
        "consts": len(consts),
        "rationale": len(rationale),
        "imports": imports,
        "longest_fn": longest_fn,
        "total_lines": total_lines,
        # `limit=None` (`--all`) returns the full lists; default 8 keeps the
        # one-screen summary tight. The `+N more` line in the renderer still
        # surfaces what was truncated, per the omission-counts rule.
        "class_labels": [G.nodes[n].get("label", n) for n in classes][:limit] if limit else [G.nodes[n].get("label", n) for n in classes],
        "fn_labels": [G.nodes[n].get("label", n) for n in fns][:limit] if limit else [G.nodes[n].get("label", n) for n in fns],
    }


def doc_node(G: nx.DiGraph, nid: str, *, max_rationale_lines: int = 40) -> dict:
    """Return signature + rationale body for a node — the "what does this mean?"
    one-shot.

    Reuses the rationale_for edge convention emitted by extractors: rationale
    nodes hang off the symbol they describe. Walks both directions because
    extractors disagree on emit-direction (Python AST emits
    rationale --rationale_for--> symbol; some LLM passes invert it).
    """
    attrs = G.nodes[nid]
    sf = attrs.get("source_file") or ""
    loc = attrs.get("source_location") or ""

    header_line = ""
    if sf and loc:
        # Lap-21 #4: read enough lines to capture a multi-line signature,
        # then collapse via _signature_end so TS norms like
        # `function foo(\n  a,\n): T {` render as one `sig:` line instead
        # of truncating after the open-paren.
        body, _ln, _trunc = _read_body_full(sf, loc, max_lines=20, flat=True)
        if body:
            sig_end_idx = _signature_end(body, 0, max_search=20)
            header_line = " ".join(
                s.strip() for s in body[:sig_end_idx] if s.strip()
            ) or body[0]

    rationale_blocks: list[dict] = []
    seen: set[str] = set()
    for u in G.predecessors(nid):
        e = G.edges[u, nid]
        if (G.nodes[u].get("file_type") == "rationale"
                or e.get("relation") == "rationale_for"):
            if u not in seen:
                seen.add(u)
                rationale_blocks.append({"id": u, "node": G.nodes[u]})
    for v in G.successors(nid):
        if G.edges[nid, v].get("relation") == "rationale_for":
            if v not in seen:
                seen.add(v)
                rationale_blocks.append({"id": v, "node": G.nodes[v]})

    body_blocks: list[dict] = []
    for r in rationale_blocks:
        rsrc = r["node"].get("source_file") or sf
        rloc = r["node"].get("source_location") or ""
        body, ln, trunc = _read_body_full(rsrc, rloc,
                                          max_lines=max_rationale_lines,
                                          flat=True)
        body_blocks.append({
            "label": r["node"].get("label", r["id"]),
            "source_file": rsrc,
            "source_location": rloc,
            "start_line": ln,
            "lines": body,
            "truncated": trunc,
        })

    return {
        "type": "doc",
        "label": attrs.get("label", nid),
        "id": nid,
        "node_kind": attrs.get("node_kind", ""),
        "source_file": sf,
        "source_location": loc,
        "header": header_line,
        "rationale": body_blocks,
    }


def _render_doc_text(data: dict, *, md: bool = False) -> str:
    parts: list[str] = []
    label = data["label"]
    sf = data.get("source_file") or ""
    loc = data.get("source_location") or ""
    loc_short = loc[1:] if loc.startswith("L") else loc
    head_label = (f"[{label}]({sf}:{loc_short})"
                  if md and sf and loc_short else label)
    kind_str = f" [{data['node_kind']}]" if data.get("node_kind") else ""
    parts.append(f"  doc @{head_label}{kind_str}  {sf}:{loc_short}".rstrip())
    if data.get("header"):
        parts.append(f"    sig: {data['header'].strip()}")
    rats = data.get("rationale") or []
    if not rats:
        parts.append("    no rationale attached. "
                     "peek for body, navigate for context.")
        return "\n".join(parts)
    for i, r in enumerate(rats, 1):
        rloc = r.get("source_location") or ""
        rloc_short = rloc[1:] if rloc.startswith("L") else rloc
        rsrc = r.get("source_file") or ""
        head = (f"[{r['label']}]({rsrc}:{rloc_short})"
                if md and rsrc and rloc_short else r['label'])
        suffix = f"  (truncated, raise with --lines N)" if r.get("truncated") else ""
        parts.append(f"    [{i}] {head}{suffix}")
        for line in r.get("lines") or []:
            parts.append(f"        {line}")
    return "\n".join(parts)


def _render_shape_text(data: dict) -> str:
    """Compact one-screen shape summary: counts + samples + longest fn."""
    parts: list[str] = []
    parts.append(f"  shape @{data['label']}  {data.get('source_file') or '?'}")
    bits: list[str] = []
    if data["classes"]:
        bits.append(f"{data['classes']} class(es)")
    if data["interfaces_or_types"]:
        bits.append(f"{data['interfaces_or_types']} iface/type(s)")
    if data["fns"]:
        bits.append(f"{data['fns']} fn(s)")
    if data["consts"]:
        bits.append(f"{data['consts']} const/other")
    if data["rationale"]:
        bits.append(f"{data['rationale']} rationale")
    if data["imports"]:
        bits.append(f"{data['imports']} import(s)")
    if data["total_lines"]:
        bits.append(f"{data['total_lines']} ln total")
    parts.append("    " + (" · ".join(bits) if bits else "(empty file)"))

    if data.get("class_labels"):
        more = data["classes"] - len(data["class_labels"])
        sfx = f" +{more} more" if more > 0 else ""
        parts.append(f"    classes: {', '.join(data['class_labels'])}{sfx}")
    if data.get("fn_labels"):
        more = data["fns"] - len(data["fn_labels"])
        sfx = f" +{more} more" if more > 0 else ""
        parts.append(f"    fns: {', '.join(data['fn_labels'])}{sfx}")
    if data.get("longest_fn"):
        lf = data["longest_fn"]
        loc = lf.get("source_location") or ""
        loc_str = (f":{loc[1:]}" if loc.startswith("L") else "")
        parts.append(f"    longest fn: {lf['label']}  {lf['lines']} ln{loc_str}")

    return "\n".join(parts)


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
    # Transitive call-graph aliases: dependents/dependencies walk N hops
    # via call edges (default 3, override with --depth). Engineers reach
    # for "what depends on X?" — these expose the depth-walk feature
    # via verbs that map onto coupling rather than graph direction.
    "dependents": "dependents", "deps-up": "dependents",
    "dependencies": "dependencies", "deps": "dependencies", "deps-down": "dependencies",
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
             show_ops_hint: bool = False,
             limit: int | None = None,
             kinds: set[str] | None = None,
             bodies: int | None = None,
             depth: int = 1,
             archived_mode: str = "all",
             include_files: bool = False,
             code_only: bool = False,
             collapse_dupes: bool = True,
             explain_cost: bool = False,
             md: bool = False,
             transitive: bool = False,
             node_kinds: set[str] | None = None,
             show_session: str | None = None,
             quiet_hints: bool = False) -> str:
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
    show_ops_hint: append the ops cheat-sheet line. Default False — the
        cheat-sheet is repetitive noise after the first few calls. Pass
        True (CLI: `--ops-hint`) when bootstrapping a new agent that
        hasn't seen the op vocabulary yet. The cheat-sheet still surfaces
        unconditionally on the empty-cursor message so first-contact
        callers see how to focus a node.
    limit: max items per listing (default 25).
    kinds: optional allowlist of edge relation names (e.g. {"calls", "uses"}).
        When set, `in`/`out` pivots include only edges whose relation is in
        the set; counts of kind-excluded edges are surfaced in the drop
        breakdown so the agent can choose to widen.
    bodies: optional N. When set, `contains`/`methods` listings render the
        first N non-blank source lines under each item — surfaces dead/
        redirect/`raise NotImplementedError` stubs without a separate Read.
    archived_mode: "all" (default), "no" (hide archived), "only" (show only
        archived). Lap-7: in repos with many archived siblings, the agent
        wants to either focus on active code or specifically pivot to the
        legacy variant. Filters listing output post-sort; counts hidden
        items in the listing's drop breakdown.
    include_files: when False (default) `coc` excludes file-level hubs from
        the listing. Lap-8: a consumer reported `@DecodeEngine coc` returned
        7/25 file nodes which crowded out the symbol-level co-occurrence
        result they actually wanted. The hidden file count is surfaced as
        "+N files hidden — pass --include-files to widen".
    collapse_dupes: when True (default) groups of >=5 adjacent same-label
        items in a listing render as one collapsed row with sample paths.
        Lap-9: 41 callers of `embeddingGenerate` were all `stagedDecode()`
        across experiment files — full enumeration drowned the
        differentiating field. `[N]` picks land on the group's first
        member; pivot via path-qualifier for a specific dupe.
    explain_cost: when True (CLI: `--explain-cost`), pivots return a
        compact `would return N nodes ≈ K bytes` preview instead of
        rendering the listing. Lets the agent gate large pivots (`coc`
        on 1000-node communities) before paying the token cost.
    md: when True (CLI: `--md`), labels and src:line citations render as
        `[label](src:line)` markdown links. Cheap affordance for IDE/Claude
        Code consumers that auto-link the click target.
    transitive: when True (CLI: `--transitive`), `out` from a file node with
        0 direct out edges routes through `contains` children and
        aggregates their out edges. Collapses the script-leaf "drill via
        contained nodes" workflow into one call. No-op when `out` already
        has direct edges or focus isn't a file node.
    show_session: when set (CLI: `--show-session <id>`), load the cursor
        saved under that id, render its current frontier, and exit. No
        ops processed, no cursor mutation, no persist. Lets an agent ask
        "where am I in this session?" without committing a navigate step.
    """
    if isinstance(ops, str):
        ops = ops.strip().split() if ops.strip() else []

    # `limit=None` means "no override" — resolve to the standard default for
    # most pivots, but `coc` listings get a tighter window since communities
    # of 100+ members flood context. The cursor caches the result, so a
    # follow-up `coc --limit 30` is cheap.
    user_limit_explicit = limit is not None
    effective_limit = limit if user_limit_explicit else LIST_LIMIT

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

    # --- show-session: read-only cursor introspection ------------------------
    # `--show-session <id>` is a peek, not a navigate step. Load the cursor,
    # render its frontier, exit. No ops processed, no mutation, no persist.
    # Useful for picking up a long-running session after a context break, or
    # asking "where did I leave the cursor?" without committing a step.
    if show_session:
        cpath = _cursor_path(gpath, show_session)
        if not cpath.exists():
            msg = (f"error: session `{show_session}` not found. "
                   f"sessions live under {gpath.parent / CURSOR_DIR}/.")
            return json.dumps({"type": "error", "message": msg}) if fmt == "json" else msg
        cursor = Cursor.load(cpath)
        cursor.graph_path = str(gpath)
        if not cursor.current:
            msg = f"session `{show_session}` exists but has no cursor (empty history)."
            if fmt == "json":
                return json.dumps({"type": "status", "message": msg})
            return f"  note: {msg}"
        last_data = _frontier_data(G, communities, cursor,
                                   extracted_only=extracted_only,
                                   min_confidence=min_confidence,
                                   show_history=True)
        if fmt == "json":
            last_data["session"] = show_session
            return json.dumps(last_data)
        body = _render_frontier_text(last_data, cursor,
                                     show_ops=show_ops_hint, md=md,
                                     quiet_hints=quiet_hints)
        # Header pins this output as a peek (not a step) so the agent
        # doesn't read the frontier as the result of an op they didn't run.
        return f"  show-session: {show_session}\n{body}"

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
        # Drain any queue from a prior disambig abort. If the caller's
        # first op is a pick (`3` / `[3]`), append the queue *after*
        # their ops so the pick happens first, then the queued tail
        # replays. If the caller's first op is anything else (a new
        # focus, a different pivot, `back`, etc.), they've moved on —
        # drop the queue silently so it can't surprise them downstream.
        if cursor.queued_ops:
            first = ops[0].strip() if ops else ""
            is_pick = first.isdigit() or (first.startswith("[")
                                           and first.endswith("]")
                                           and first[1:-1].isdigit())
            if is_pick:
                queued = list(cursor.queued_ops)
                # Defensive: if the caller retyped some-or-all of the
                # queue verbatim (taking the "queued for auto-replay:
                # 2 in" message as instructions), don't double-fire.
                # Anything in `ops` after the pick that's already at
                # the queue's head gets stripped from the queue replay.
                user_tail = [o.strip() for o in ops[1:]]
                while user_tail and queued and user_tail[0] == queued[0]:
                    user_tail.pop(0)
                    queued.pop(0)
                if queued:
                    trace.append(
                        f"  > resuming queued ops: {' '.join(queued)}"
                    )
                ops = list(ops) + queued
            cursor.queued_ops = []
        # Index-based iteration so ops can consume a following arg
        # (currently `read N` / `body N` for an explicit line cap).
        op_i = 0
        while op_i < len(ops):
            op = ops[op_i]
            op_i += 1
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
                        # Lap-21 #3: when match_type=="prefix" with a single
                        # chosen, the alternatives are by construction
                        # fuzzy near-misses (the resolver only enters this
                        # branch when len(prefix_hits)==1, so any other
                        # prefix-shaped label would have made it 2+ and
                        # routed elsewhere). `get_close_matches` returns
                        # similarity-ranked labels regardless of prefix
                        # shape, so they're always noise here. Suppress
                        # the glyph and "also near" line. Substring/fuzzy
                        # remain loud — those branches DO have plausible
                        # ambiguity.
                        suppress_alts = match_type == "prefix"
                        loud = ((bool(alternatives) and not suppress_alts)
                                or match_type in ("substring", "fuzzy"))
                        glyph = "⚠ ambiguous:" if loud else ""
                        head = f"{glyph} matched" if glyph else "matched"
                        trace.append(f"  > {head} `{op_str}` → {chosen_label} ({match_type})")
                        if alternatives and not suppress_alts:
                            alt_labels = [G.nodes[a].get("label", a) for a in alternatives]
                            trace.append(
                                f"  > also near: {', '.join(alt_labels)}  "
                                f"(pivot with @<label> if wrong pick)"
                            )
                    cursor.push(chosen)
                    cursor.last_listing = []
                    cursor.last_pivot = None
                    last_data = _frontier_data(G, communities, cursor,
                                               extracted_only=extracted_only,
                                               min_confidence=min_confidence,
                                               show_history=show_history)
                elif candidates:
                    candidates, archived_hidden = _filter_archived_ids(G, candidates, archived_mode)
                    drops = {"archived": archived_hidden} if archived_hidden else None
                    # Encode match_type in the pivot label so the listing
                    # header carries it directly. The redundant
                    # `> N {match_type} matches for X — pick [N]` trace line
                    # was dropped: the listing header below already shows
                    # `@X ambiguous (prefix)` with count + collapse note,
                    # and that's what leads into the action.
                    if match_type == "no_match_in_file":
                        # Lap-20b: prefix matched a real file but no
                        # symbol in it matched the basename. Render a
                        # clear "<basename> not in <file>" pivot rather
                        # than the generic ambiguous header. Surface
                        # which file matched so the agent can compare
                        # against what they typed.
                        first_sf = G.nodes[candidates[0]].get("source_file", "") if candidates else ""
                        _, _, basename_part = op_str[1:].rpartition("/")
                        pivot_label = (f"`{basename_part}` not in {first_sf}; "
                                       f"available {len(candidates)}")
                    else:
                        pivot_label = (f"@{op_str[1:]} ambiguous"
                                       if not match_type or match_type == "exact"
                                       else f"@{op_str[1:]} ambiguous ({match_type})")
                    last_data = _listing_data(G, candidates,
                                              pivot_label, None,
                                              total=len(candidates),
                                              sort_label="match relevance",
                                              limit=effective_limit,
                                              drops=drops,
                                              collapse_dupes=collapse_dupes,
                                              node_kinds=node_kinds)
                    cursor.last_listing = [it["id"] for it in last_data["items"]]
                    cursor.last_pivot = "@-disambig"
                else:
                    # Suggest near-misses, but require enough lexical overlap
                    # to look like a typo rather than a corpus dump. Lap-6
                    # field report: `@nonexistentSymbolXYZ` returned a grab
                    # bag of names with no shared prefix/substring with the
                    # query — pure difflib similarity at cutoff=0.5 is
                    # noise on long queries. Tighten cutoff and require a
                    # 3-char shared substring so the suggestions are
                    # actionable typos, not random graph members.
                    key = _norm(op_str.lstrip("@").strip())
                    all_labels = {_norm(G.nodes[n].get("label", n)): n
                                  for n in G.nodes()}
                    raw_near = get_close_matches(key, list(all_labels.keys()),
                                                 n=10, cutoff=0.65)
                    def _has_shared_chunk(a: str, b: str, k: int = 3) -> bool:
                        if len(a) < k or len(b) < k:
                            return a == b
                        chunks = {a[i:i + k] for i in range(len(a) - k + 1)}
                        return any(b[i:i + k] in chunks for i in range(len(b) - k + 1))
                    near = [s for s in raw_near if _has_shared_chunk(key, s)][:5]
                    near_labels = [G.nodes[all_labels[s]].get("label", s)
                                   for s in near]
                    if near_labels:
                        msg = (f"no node matches `{op_str}`. "
                               f"did you mean: {', '.join(near_labels)}?")
                    else:
                        msg = (f"no node matches `{op_str}`. "
                               "graph may be stale — try `graphify update .` "
                               "if you've added code since the last extract.")
                    last_data = {"type": "error", "message": msg}
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
                        # User stepped back — any queued ops from a prior
                        # disambig are no longer on the intended path.
                        cursor.queued_ops = []
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
            elif op_str in ("read", "body"):
                # Lap-3 wishlist: fold node-find + body-read into one nav call.
                # The graph already knows file:line; serving the body inline
                # closes the loop and avoids a separate Read of the same file.
                #
                # Optional positional arg: `read N` raises the line cap to N
                # (default 200). `read 0` or omitted uses the default. The
                # body walker still bails at the natural dedent first, so
                # this is a *cap*, not a forced length — useful when the
                # natural dedent ends too early (e.g. nested closures whose
                # baseline confuses the indent walker).
                next_arg = ops[op_i] if op_i < len(ops) else None
                requested_max = None
                if next_arg is not None and next_arg.strip().isdigit():
                    requested_max = int(next_arg.strip())
                    if requested_max > 0:
                        op_i += 1  # consume the arg
                    else:
                        requested_max = None
                max_lines = requested_max if requested_max else 200
                if not cursor.current:
                    last_data = {"type": "error",
                                 "message": "no cursor. focus with @<label> first."}
                else:
                    nattrs = G.nodes[cursor.current]
                    sf = nattrs.get("source_file")
                    loc = nattrs.get("source_location")
                    # File nodes have no enclosing body — flat-dump the
                    # leading lines instead of letting the indent walker
                    # bail after the shebang.
                    is_file = _is_file_node(G, cursor.current)
                    body, ln, trunc = _read_body_full(sf, loc,
                                                      max_lines=max_lines,
                                                      flat=is_file)
                    last_data = {
                        "type": "body",
                        "label": nattrs.get("label", cursor.current),
                        "source_file": sf,
                        "source_location": loc,
                        "lines": body,
                        "start_line": ln,
                        "truncated": trunc,
                    }
            elif (op_str in ("coc", "⊕coc", "⊕", "community")
                  and op_i < len(ops)
                  and ops[op_i].strip() == "summary"):
                # `coc summary` two-token op. Consume the trailing `summary`
                # token and route to the structural-shape builder instead
                # of enumerating members. Cheaper-by-far on big communities.
                op_i += 1  # consume "summary"
                if not cursor.current:
                    last_data = {"type": "error",
                                 "message": "no cursor. focus with @<label> first."}
                else:
                    last_data = _coc_summary_data(G, communities, cursor)
                    # Non-modifying op: don't disturb last_listing/last_pivot
                    # so a chained `coc summary` then [N] doesn't blow up
                    # any prior listing context.
            elif op_str in ("filter", "f"):
                # Narrow `cursor.last_listing` by a label regex. Works after
                # any listing producer (methods, siblings, in/out, contains,
                # callers/callees, coc, @-disambig). Renumbers picks 1-based
                # against the filtered view so `methods filter "_deficit" 2`
                # picks the second match without arithmetic. Falls back to a
                # case-insensitive substring match when the pattern fails to
                # compile as a regex — saves the agent from escaping a stray
                # `(`/`.` for a quick narrow.
                next_arg = ops[op_i] if op_i < len(ops) else None
                pattern = next_arg.strip() if next_arg else ""
                if not pattern:
                    last_data = {"type": "error",
                                 "message": "filter needs a pattern: `filter <regex>`."}
                elif not cursor.last_listing:
                    last_data = {"type": "error",
                                 "message": "no listing to filter. run a pivot first "
                                            "(methods, siblings, in/out, contains, …)."}
                else:
                    op_i += 1  # consume the pattern
                    try:
                        rx = re.compile(pattern, re.IGNORECASE)
                        match_label = lambda s: bool(rx.search(s))  # noqa: E731
                        mode = "regex"
                    except re.error:
                        needle = pattern.lower()
                        match_label = lambda s: needle in s.lower()  # noqa: E731
                        mode = "substring"
                    prior_count = len(cursor.last_listing)
                    prior_pivot = cursor.last_pivot or "(listing)"
                    kept_ids = [nid for nid in cursor.last_listing
                                if match_label(G.nodes[nid].get("label", nid))]
                    last_data = _listing_data(
                        G, kept_ids,
                        f"{prior_pivot} · filter /{pattern}/",
                        edge_for=None,
                        total=len(kept_ids),
                        sort_label=f"label {mode}",
                        limit=effective_limit,
                        # Already collapsed at the upstream listing step;
                        # `cursor.last_listing` only carries representative
                        # ids, so re-collapsing is a no-op that adds risk.
                        collapse_dupes=False,
                        extracted_only=extracted_only,
                        node_kinds=node_kinds,
                    )
                    last_data["filter"] = {
                        "pattern": pattern,
                        "mode": mode,
                        "matched": len(kept_ids),
                        "from": prior_count,
                    }
                    cursor.last_listing = [it["id"] for it in last_data["items"]]
                    cursor.last_pivot = f"{prior_pivot}·filter"
                    if not kept_ids:
                        mode_note = "" if mode == "regex" else f" ({mode})"
                        trace.append(
                            f"  > filter /{pattern}/{mode_note} matched 0 of {prior_count}"
                        )
            elif op_str in ("where-used", "wu", "⚯"):
                # `where-used` superop: edge-callers (`in`) ∪ literal-string
                # mentions of the symbol's name in code bodies. Combines the
                # "who calls X?" graph query with "where is X mentioned by
                # name?" text query into one call. Common navigation when
                # the agent suspects dynamic dispatch / string-keyed lookup
                # would hide some callers from the AST graph.
                if not cursor.current:
                    last_data = {"type": "error",
                                 "message": "no cursor. focus with @<label> first."}
                else:
                    # 1. Edge-discovered callers via `in`. Reuse _pivot_data
                    #    so confidence/kind filtering is consistent with the
                    #    bare `in` op the agent would otherwise run.
                    pname_in, ids_in, edge_for_in, sort_in, drops_in = _pivot_data(
                        G, communities, cursor, "in",
                        extracted_only=extracted_only,
                        min_confidence=min_confidence,
                        kinds=kinds,
                        depth=depth,
                        include_files=include_files,
                        code_only=code_only,
                        transitive=False,
                    )
                    # 2. Text-discovered mentions. Strip method/property
                    #    decoration (`.foo()` → `foo`) so we search for the
                    #    bare identifier with word-boundary anchors. That
                    #    keeps `init` from matching `__init__` everywhere.
                    cur_label = G.nodes[cursor.current].get("label") or ""
                    bare = cur_label.lstrip(".").rstrip("()").strip()
                    text_hits: list[str] = []
                    text_hit_lines: dict[str, list[int]] = {}
                    if bare and bare.replace("_", "").isalnum():
                        # Word-boundary regex; case-sensitive (use exact
                        # name to reduce false positives like `name` →
                        # `Name`/`getName`/etc).
                        pat = rf"\b{re.escape(bare)}\b"
                        sres = search_bodies(
                            G, pat, kind="code",
                            archived_mode=("no" if archived_mode == "no" else "all"),
                            limit=200,
                        )
                        seen = set(ids_in) | {cursor.current}
                        for h in sres["hits"]:
                            if h["id"] in seen:
                                # Already discovered via edge (or it's the
                                # def itself) — track the line for context
                                # rendering but don't double-list as a row.
                                text_hit_lines.setdefault(h["id"], []).append(h["match_line"])
                                continue
                            seen.add(h["id"])
                            text_hits.append(h["id"])
                            text_hit_lines.setdefault(h["id"], []).append(h["match_line"])
                    # Combined ids: edges first (ranked by `in` already),
                    # then text-only.
                    combined = list(ids_in) + text_hits
                    combined, archived_hidden = _filter_archived_ids(
                        G, combined, archived_mode)
                    drops = dict(drops_in or {})
                    if archived_hidden:
                        drops["archived"] = archived_hidden
                    drops["text_only_hits"] = len(text_hits)
                    drops["edge_hits"] = len(ids_in)
                    last_data = _listing_data(
                        G, combined, "⚯where-used",
                        edge_for_in,  # only edge-discovered rows carry edge tags
                        total=len(combined),
                        sort_label=f"{sort_in}, then text-mention",
                        limit=effective_limit,
                        drops=drops,
                        kinds=kinds,
                        bodies=None,
                        extracted_only=extracted_only,
                        collapse_dupes=collapse_dupes,
                        node_kinds=node_kinds,
                    )
                    # Annotate text-only rows with the line number(s) of
                    # the mention(s). Without this the agent doesn't know
                    # which row is edge vs text or where to look.
                    edge_id_set = set(ids_in)
                    for it in last_data["items"]:
                        nid_it = it["id"]
                        if nid_it not in edge_id_set:
                            it["mention_lines"] = text_hit_lines.get(nid_it, [])[:3]
                    cursor.last_listing = [it["id"] for it in last_data["items"]]
                    cursor.last_pivot = "where-used"
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
                        include_files=include_files,
                        code_only=code_only,
                        transitive=transitive,
                    )
                    # Auto-widen empty in/out to inferred edges. When extracted-only
                    # returns 0 but inferred>0, the agent is staring at a misleading
                    # "0 callers" that actually has the answer one flag away. Re-run
                    # with extracted_only=False, mark the result as auto-widened so
                    # the renderer surfaces "auto-widened" + INFERRED tags per row.
                    # Scope: only `in`/`out` (where the empty result is actually
                    # misleading), no `--kind` filter (the user is filtering on
                    # purpose), no `--depth>1` (transitive inferred walks explode),
                    # no `--transitive` (already widening structurally).
                    auto_widened = False
                    if (pkey in ("in", "out")
                            and extracted_only
                            and not ids
                            and depth == 1
                            and not transitive
                            and not kinds
                            and (drops.get("inferred") or 0) > 0):
                        wname, wids, wedge_for, wsort_label, wdrops = _pivot_data(
                            G, communities, cursor, pkey,
                            extracted_only=False,
                            min_confidence=min_confidence,
                            kinds=kinds,
                            depth=depth,
                            include_files=include_files,
                            transitive=transitive,
                        )
                        if wids:
                            pname = f"{wname} (auto-widened)"
                            ids, edge_for, sort_label = wids, wedge_for, wsort_label
                            drops = dict(wdrops or {})
                            drops["auto_widened_from_extracted"] = True
                            auto_widened = True
                    ids, archived_hidden = _filter_archived_ids(G, ids, archived_mode)
                    if archived_hidden:
                        # Track in drops so the renderer surfaces the count rather
                        # than letting the agent see a silently-shrunk listing.
                        drops = dict(drops or {})
                        drops["archived"] = archived_hidden
                    # `coc` listings tighten the default window since
                    # communities of 100+ members flood context. User-passed
                    # --limit always wins.
                    pivot_limit = (
                        COC_LIST_LIMIT_DEFAULT
                        if pkey == "coc" and not user_limit_explicit
                        else effective_limit
                    )
                    if explain_cost:
                        # Cost preview short-circuits the listing build — the
                        # caller asked "what would I get?" and we answer that
                        # directly. Cursor state is unchanged: no last_listing,
                        # no last_pivot. A subsequent call without
                        # --explain-cost runs the same query and commits.
                        last_data = {
                            "type": "preview",
                            "pivot": pname,
                            "total": len(ids),
                            "showing": min(len(ids), pivot_limit),
                            "estimated_bytes": _estimate_listing_bytes(G, ids, pivot_limit),
                            "drops": drops or {},
                        }
                    else:
                        last_data = _listing_data(G, ids, pname, edge_for,
                                                  total=len(ids),
                                                  sort_label=sort_label,
                                                  limit=pivot_limit,
                                                  drops=drops,
                                                  kinds=kinds,
                                                  bodies=bodies if pkey in ("contains", "methods") else None,
                                                  extracted_only=extracted_only,
                                                  collapse_dupes=collapse_dupes,
                                                  node_kinds=node_kinds)
                        # Persist only the rendered window — keeps the cursor file
                        # small (coc on a 1000-node community would otherwise be ~40KB).
                        # Post-collapse, [N] indices map to the first member of each
                        # visible row, so we drive last_listing off the rendered
                        # items rather than the raw `ids` list.
                        cursor.last_listing = [it["id"] for it in last_data["items"]]
                        cursor.last_pivot = pkey
            else:
                handled = False
                # Path-shaped first arg without `@` is the most common
                # mistake — `graphify navigate "tools/x/y" deps` lands
                # here today. Suggest the @-form so the next call is
                # one-shot rather than another guess at the verb list.
                hint = ""
                if "/" in op_str or (op_i == 1 and "." in op_str
                                     and not op_str.startswith(".")):
                    hint = (f" did you mean `@{op_str}`? "
                            f"(path-qualified focus needs the @ prefix.) ")
                last_data = {"type": "error",
                             "message": (f"unknown op `{op_str}`.{hint}"
                                         "ops: @<label> · "
                                         "in · out · methods · contains · coc · rat · inh · "
                                         "parent · siblings · callers · callees · "
                                         "dependents · dependencies · read · "
                                         "back · reset · [N]")}
            _summarize_step(op_str, last_data)

            # Abort the chain on hard failures so a downstream op doesn't
            # silently overwrite the listing/error and mask the failure.
            # Two cases:
            #   1. error: nothing useful for downstream to build on.
            #   2. @-disambig: a pick is required before pivots make sense.
            if last_data and last_data.get("type") == "error":
                remaining = ops[op_i:]
                if len(ops) > 1 and remaining:
                    trace.append(
                        f"  chain aborted at `{op_str}` — fix and rerun "
                        f"(remaining: {' '.join(remaining)})"
                    )
                break
            if cursor.last_pivot == "@-disambig":
                # If the user already supplied the pick inline (`@compile
                # 2 in` — pick is op_i, the very next op), don't pause:
                # let the loop continue so the pick fires this turn and
                # the rest of the chain runs from the resolved cursor.
                # Pausing in that case both confused the agent and caused
                # a double-replay when they retyped the queued tail.
                next_inline = ops[op_i].strip() if op_i < len(ops) else ""
                is_inline_pick = next_inline.isdigit() or (
                    next_inline.startswith("[")
                    and next_inline.endswith("]")
                    and next_inline[1:-1].isdigit()
                )
                if is_inline_pick:
                    # Don't break — fall through, next loop iter handles
                    # the pick op. last_pivot stays "@-disambig" until
                    # the pick handler clears it.
                    continue
                # No inline pick → pause. Queue the remaining ops so
                # the next session call (with just `N`) replays them.
                # Note: `ops[op_i:]` is the tail AFTER the @-op (the
                # current iter has already advanced op_i past it).
                remaining = ops[op_i:]
                if remaining:
                    cursor.queued_ops = list(remaining)
                    trace.append(
                        f"  chain paused at `{op_str}` — next call: just `[N]` "
                        f"(queued ops `{' '.join(remaining)}` will replay automatically)"
                    )
                break

    # Render a chain summary line when there were 2+ ops — mid-chain results
    # otherwise vanish ("output is the last op's result"), leaving the agent
    # to guess what got skipped.
    if len(chain_summary) >= 2:
        trace.append(f"  chain: " + " → ".join(chain_summary))

    # Persist cursor only when a future call could plausibly resume it —
    # same gate that decides whether the session id is printed below.
    # One-shot focus calls with no chain depth and no explicit --session
    # used to leave the cursor as write-once garbage swept 30 minutes
    # later. Lap-13 field-report fix: skip the write entirely so
    # `.navigate/` only accumulates resumable sessions.
    #
    # Lap-15: the save deferred until AFTER render so renderer-side
    # cursor mutations (hints_emitted bookkeeping, future bits) get
    # persisted with the rest of the cursor state. Previously save ran
    # before render and the per-session hint dedup never carried across
    # calls because the appended hint keys never reached disk.
    can_resume = (
        session_id is not None
        and (
            (isinstance(session, str) and bool(session))  # explicit --session
            or bool(cursor.queued_ops)                    # chain paused, must resume
            or len(cursor.history) >= 1                   # walked > 1 step
        )
    )

    # Log surfaced source paths so the PreToolUse hook can suppress its
    # "scout cheaper" nudge on files this session just navigated to.
    # Best-effort; failures must not affect the rendered output.
    # Path tracking still runs on every persist-eligible call — the hook
    # reads a separate paths log, not the cursor file, so suppressing
    # the cursor write doesn't disturb hook behaviour.
    if persist:
        _record_session_paths(gpath, _collect_session_paths(last_data, cursor, G))

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
        parts.append(_render_frontier_text(last_data, cursor,
                                           show_ops=show_ops_hint, md=md,
                                           quiet_hints=quiet_hints))
    elif last_data.get("type") == "listing":
        parts.append(_render_listing_text(last_data, show_ops=show_ops_hint, md=md))
    elif last_data.get("type") == "body":
        parts.append(_render_body_text(last_data, md=md))
    elif last_data.get("type") == "preview":
        parts.append(_render_preview_text(last_data))
    elif last_data.get("type") == "coc_summary":
        parts.append(_render_coc_summary_text(last_data, md=md))
    elif last_data.get("type") == "status":
        parts.append(last_data["message"])
    elif last_data.get("type") == "error":
        parts.append(last_data["message"])
    else:
        parts.append(json.dumps(last_data, default=str))

    # Only print the session id when chaining is plausibly useful (same
    # `can_resume` gate computed above for the cursor write — keeping
    # the two in lockstep means a printed id always corresponds to a
    # persisted cursor and vice versa).
    if can_resume:
        parts.append(f"  session: {session_id}  (resume with --session {session_id})")

    # Persist cursor AFTER render so renderer-side mutations (hints_emitted)
    # land on disk. This must run after the body of work that might mutate
    # cursor state but before returning the result.
    if persist and can_resume:
        cursor.save(_cursor_path(gpath, session_id))

    return "\n".join(parts)
