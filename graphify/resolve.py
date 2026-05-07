"""Label resolution and ranking for graphify graphs.

Owns `@<label>` → node-id lookup used across the CLI (`query`, `path`,
`explain`, `peek`, `shape`, `search`, `navigate`) plus the supporting
machinery for ranking ambiguous matches: archived-path detection,
recency bucketing, private-name demotion, per-file metadata caching.

Decoupled from `navigate.py` so any tool reading `graphify-out/graph.json`
can resolve labels the same way without dragging in the navigator's
cursor/output layer.
"""

from __future__ import annotations
import re
import time
import unicodedata
from collections import defaultdict
from difflib import get_close_matches
from pathlib import Path

import networkx as nx


# --- caches ----------------------------------------------------------------

# In-process cache for per-file metadata (mtime, line count, git mtime). The
# same file appears under many nodes — `_node_summary` is called once per
# rendered item, but we only want one stat per file per `navigate` call.
_META_CACHE: dict[str, dict] = {}
_GIT_LOG_CACHE: dict[str, dict[str, int]] = {}  # repo_root → {file: last_commit_unixtime}


# --- normalization ---------------------------------------------------------

def _norm(s: str) -> str:
    """Casefold + NFC-normalize for resolution. macOS filesystems hand back NFD
    (`H` + combining acute), but agents type NFC (`Hé`) — without normalization,
    `Hénon` from a path won't match `Hénon` typed by hand and `difflib` silently
    fails. Normalize on both sides.
    """
    return unicodedata.normalize("NFC", s).lower()


# --- index -----------------------------------------------------------------

def label_index(G: nx.DiGraph) -> dict[str, list[str]]:
    """Normalized label/id → list of node ids. Used to resolve `@<label>`.

    Both `_norm(label)` and `_norm(nid)` are indexed so an agent can
    resolve via either form. When they normalize to the same key (a
    common case: extractor sets `label="Atlas"` and id="atlas"), the
    same nid would land in the list twice — making `@Atlas` look
    ambiguous to `resolve_focus`. Track per-key membership to avoid
    that duplicate; preserve insertion order so callers that depend
    on it (e.g. tie-breakers) stay deterministic.
    """
    idx: dict[str, list[str]] = defaultdict(list)
    seen: dict[str, set[str]] = defaultdict(set)
    for nid, attrs in G.nodes(data=True):
        label = attrs.get("label", nid)
        for key in (_norm(label), _norm(nid)):
            if nid not in seen[key]:
                seen[key].add(nid)
                idx[key].append(nid)
    return dict(idx)


# --- file metadata ---------------------------------------------------------

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


# --- archived-path detection -----------------------------------------------

# Lap-7 narrowing: dropped `experiments?|experimental` and bare `old`/`_old`.
# A consumer-Claude reported `src/experiments/cartography-build.ts` (actively
# in development) was being flagged — false `[archived]` signal made the
# subagent write off live code. We now only match path segments that are
# nearly-always archive markers: `frozen`, `legacy`, `deprecated`, `archive`,
# `archived`. Patterns are anchored on path *segments* (not substrings) so a
# `kgarchive` directory isn't falsely tagged.
_ARCHIVE_RE = re.compile(
    r"(^|/)(?:frozen|legacy|deprecated|archive|archived)(/|$)",
    re.IGNORECASE,
)


def _is_archived_path(src: str | None) -> bool:
    """Heuristic: does this path live under a clearly-archived directory?
    Used to push such nodes to the end of disambig listings and tag them
    so the agent doesn't accidentally land on dead code when an active
    sibling exists with the same label."""
    if not src:
        return False
    return bool(_ARCHIVE_RE.search(src))


# --- vendor / generated detection -----------------------------------------

# Lap-27 #2: third-party-imports / build-output paths universal across the
# major package-managed languages. Anchored on path *segments* (same as
# `_ARCHIVE_RE`) so a directory called `mybuild` isn't falsely tagged.
# These are auto-detected — users shouldn't have to enumerate them in
# `.graphifyignore` for the dependency case to work right.
#
# `target/` (Rust) is here even though it's a common dir name in other
# contexts; the false-positive cost is much lower than the false-negative
# (users on Cargo projects burn entry-point ranking on `target/debug/...`).
# `dist/`/`build/` similar — the vast majority of repos that use these
# names use them for build output.
_VENDOR_PATH_RE = re.compile(
    r"(^|/)(?:vendor|node_modules|\.venv|venv|env|site-packages|"
    r"third_party|third-party|Godeps|dist|build|\.next|target|"
    r"\.tox|__pycache__|coverage|"
    r"\.pytest_cache|\.mypy_cache|\.ruff_cache|\.gradle|\.dart_tool|"
    r"bower_components|jspm_packages|out|"
    r"DerivedData|Pods)(/|$)",
)


# Filename suffixes that identify generated code. Pure pattern match on
# the filename component — no path needed. Conservative set: each suffix
# is a near-universal generator convention with very low false-positive
# rate. Add cautiously; a too-broad suffix masks first-party code.
_GENERATED_SUFFIX_RE = re.compile(
    r"\.(?:pb|generated|min)\.(?:go|ts|js|css|tsx|jsx|py)$|"
    r"_(?:gen|pb2|pb2_grpc)\.(?:go|py)$|"
    r"\.g\.dart$|"
    r"\.designer\.cs$",
)


def _is_vendored_path(src: str | None) -> bool:
    """Path lives under a third-party / package-manager / build-output dir.
    See `_VENDOR_PATH_RE` for the segment list."""
    if not src:
        return False
    return bool(_VENDOR_PATH_RE.search(src))


def _is_generated_path(src: str | None) -> bool:
    """Filename matches a generator-convention suffix."""
    if not src:
        return False
    # Match the basename only — full path can contain unrelated dots.
    bn = src.rsplit("/", 1)[-1]
    return bool(_GENERATED_SUFFIX_RE.search(bn))


def vendor_class(src: str | None) -> str:
    """Classify a source file path into one of:
        - `archived`    — under frozen/legacy/deprecated/archive dir
        - `generated`   — filename matches a known generator suffix
        - `vendored`    — under vendor/node_modules/.venv/dist/build/etc.
        - `first_party` — none of the above
    Order matters: archived > vendored > generated. Archived means the
    user explicitly shelved it (still their code, just shelved); vendored
    means the code isn't theirs at all; generated means it lives in their
    tree but didn't write it. The first match wins so a path like
    `vendor/lib/foo.pb.go` reports as vendored (not generated) — the
    outer dir tells you who owns it.
    """
    if _is_archived_path(src):
        return "archived"
    if _is_vendored_path(src):
        return "vendored"
    if _is_generated_path(src):
        return "generated"
    return "first_party"


# --- ranking helpers -------------------------------------------------------

def _is_private_label(label: str) -> bool:
    """Treat leading underscore (after stripping `.` and `()`) as a private/helper marker.

    Used to push `_axes_info()` below `AXES` in fuzzy/substring ranking — public
    names are almost always what the agent meant when typing a short query.
    """
    s = label.strip("()").lstrip(".")
    return s.startswith("_")


def _recency_bucket(src: str | None) -> int:
    """Bucket file age into 0=<7d, 1=<30d, 2=<90d, 3=older, 4=unknown.

    Used as a substring/fuzzy-rank tier so recently-edited matches float
    above stale code with the same label shape — Lap-3 reported `@residual`
    returning 14 hits with no recency awareness, leaving the agent to pick
    by file size rather than relevance.
    """
    if not src:
        return 4
    meta = _file_meta(src)
    age_src = meta.get("git_mtime") or meta.get("mtime")
    if not age_src:
        return 4
    days = (time.time() - age_src) / 86400.0
    if days < 7:
        return 0
    if days < 30:
        return 1
    if days < 90:
        return 2
    return 3


def _rank_files_by_prefix(files: list[str], prefix: str) -> list[tuple[int, float, str]]:
    """Rank candidate `source_file` paths by how well they match a
    user-typed prefix. Lap-20: the prefix may itself be partial
    (`multianti.ts` for `experiments/ref-id-logit-delta-multianti.ts`).
    Tier-rank so substring-of-filename beats dash-stem prefix beats
    generic difflib similarity — without this, fuzzy similarity rates
    `multi-entity.ts` (entirely different file) higher than the
    long-named substring match.

    Tiers (lower=better):
      0  exact path match (sf == prefix or endswith /prefix, with stem fallback)
      1  prefix is substring of basename (e.g. 'multianti.ts' in
         'ref-id-logit-delta-multianti.ts')
      2  basename starts with prefix (e.g. 'multi' prefix-of 'multi-entity.ts')
      3  difflib similarity ≥ 0.6
      4  no match (filtered out before return)
    Within a tier, finer score (e.g. similarity ratio) breaks ties;
    shorter basenames win on equal similarity.
    """
    from difflib import SequenceMatcher
    out: list[tuple[int, float, str]] = []
    pn = prefix.lower()
    pn_basename = pn.rsplit("/", 1)[-1]
    for sf in files:
        sfn = sf.lower()
        sf_base = sfn.rsplit("/", 1)[-1]
        # Tier 0: exact endswith match (with extension elision)
        if sfn == pn or sfn.endswith("/" + pn):
            out.append((0, 1.0, sf))
            continue
        parts = sfn.split("/")
        last = parts[-1]
        stem, dot, _ext = last.partition(".")
        sf_stem = "/".join(parts[:-1] + [stem]) if dot and len(parts) > 1 else (stem if dot else last)
        if dot and (sf_stem == pn or sf_stem.endswith("/" + pn)):
            out.append((0, 1.0, sf))
            continue
        # Tier 1: prefix substring of basename
        if pn_basename and pn_basename in sf_base:
            # Shorter basename = stronger evidence (less padding).
            score = len(pn_basename) / max(1, len(sf_base))
            out.append((1, -score, sf))
            continue
        # Tier 2: basename startswith prefix-basename (dash-stem case)
        if pn_basename and sf_base.startswith(pn_basename):
            score = len(pn_basename) / max(1, len(sf_base))
            out.append((2, -score, sf))
            continue
        # Tier 3: generic similarity, only if reasonably close
        ratio = SequenceMatcher(None, pn_basename or pn, sf_base).ratio()
        if ratio >= 0.6:
            out.append((3, -ratio, sf))
    out.sort()
    return out


def _rank_match(G: nx.Graph, key: str, nid: str) -> tuple[int, int, int, int, int, int, int]:
    """Sort key for fuzzy/substring matches. Prefer
    (1) active code over archived (frozen/, legacy/, deprecated/, archive/, archived/),
    (2) symbol nodes over rationale (docstring) nodes — lap-7: `@FOO_BAR` was
        landing on the docstring above the dict because it tied on substring,
    (3) public names,
    (4) connected nodes over orphans — lap-16: `@MöbiusS3` landed on
        `TestMobiusS3` (orphan, length_pad=4) ahead of `MobiusS3Geometry`
        (deg=13+, length_pad=8) because shorter-label-pad won the tie. An
        orphan with an exactly-matching name is almost always less useful
        than a connected near-match; demote orphans before label length
        decides.
    (5) shorter labels (less padding around the key),
    (6) recently-touched files (mtime/git_mtime bucketed),
    (7) higher degree (load-bearing).

    Use `G.degree(nid)` — it works on both DiGraph and undirected Graph
    (and on DiGraph equals `in_degree + out_degree`). The `path` and
    `explain` subcommands load via `node_link_graph` which returns an
    undirected Graph; without this, both crash inside `resolve_focus`.
    """
    label = G.nodes[nid].get("label", nid)
    src = G.nodes[nid].get("source_file")
    # Lap-27 #2: vendored/generated paths are demoted to the same tier as
    # archived. The rank tuple's first slot is "1 = push to bottom"; archived,
    # vendored, and generated all share this fate, with the existing
    # `[archived]` tag preserved for back-compat. `vendor_class` is stamped
    # at build time; when missing (older graph not yet loaded through
    # build_from_json), fall back to the archived check alone.
    vc = G.nodes[nid].get("vendor_class")
    if vc is None:
        is_demoted = 1 if _is_archived_path(src) else 0
    else:
        is_demoted = 0 if vc == "first_party" else 1
    is_rat = 1 if G.nodes[nid].get("file_type") == "rationale" else 0
    is_priv = 1 if _is_private_label(label) else 0
    deg = G.degree(nid)
    is_orphan = 1 if deg == 0 else 0
    length_pad = max(0, len(label) - len(key))
    bucket = _recency_bucket(src)
    return (is_demoted, is_rat, is_priv, is_orphan, length_pad, bucket, -deg)


# --- main resolver ---------------------------------------------------------

# Fuzzy candidate cap: max difflib results to consider. Same default as
# navigate.LIST_LIMIT (25) but kept independent — this is a resolver concern
# (search breadth), not a display concern.
_FUZZY_LIMIT = 25


def _files_in_directory(G: nx.DiGraph, dir_prefix: str) -> list[str]:
    """Lap-27 #6: file-kind nodes whose immediate parent directory matches
    `dir_prefix`. Test the parent path so the match works on both
    relative (`tools/foo.py`) and absolute (`/abs/.../tools/foo.py`)
    source_file shapes — extractor stamps absolute paths but tests
    use relative ones, and `graphify/graphify/x.py` would otherwise
    collide with itself on a startswith-style check.

    Immediate children only: `<dir>/<sub>/<file>` is excluded because
    the user typed `<dir>/` not `<dir>/<sub>/`.

    Used by `resolve_focus` to interpret `@<dir>/` (explicit) and
    `@<dir>` (last-resort, before fuzzy) as a directory listing rather
    than substring noise.
    """
    hits: list[str] = []
    for nid, attrs in G.nodes(data=True):
        if attrs.get("node_kind") != "file":
            continue
        sf = _norm(attrs.get("source_file") or "")
        if "/" not in sf:
            continue
        parent = sf.rsplit("/", 1)[0]
        if parent == dir_prefix or parent.endswith("/" + dir_prefix):
            hits.append(nid)
    return hits


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

    # Lap-27 #6: explicit directory query — `@<dir>/` (trailing slash).
    # Slash is unambiguous "I mean a directory, not a label." Resolve
    # to files-in-dir or fail; do NOT fall through to substring/fuzzy
    # which would otherwise match labels containing "<dir>/" literally
    # (file-shaped labels in tools that surface them) or fuzzy-pick a
    # nearby symbol typo.
    if key.endswith("/"):
        dir_prefix = key.rstrip("/")
        if dir_prefix:
            dir_hits = _files_in_directory(G, dir_prefix)
            if len(dir_hits) == 1:
                return dir_hits[0], [], "exact", []
            if len(dir_hits) > 1:
                dir_hits.sort(key=lambda n: _rank_match(G, key, n))
                return None, dir_hits, "exact", []
        return None, [], "fuzzy", []

    # 1. exact match (label or id)
    matches = idx.get(key, [])
    if len(matches) == 1:
        return matches[0], [], "exact", []
    if len(matches) > 1:
        # Rank multiple exact-match candidates by public-first / degree
        matches_sorted = sorted(matches, key=lambda n: _rank_match(G, key, n))
        return None, matches_sorted, "exact", []

    # 1b. path-qualified query. Two shapes the agent reaches for:
    #
    #   `tools/metric_diagnostic.py` — disambiguate by directory because
    #     basename collides with sibling files in other dirs. We match
    #     where label == basename AND source_file ends with the path.
    #
    #   `crates/foo/bar.rs/VectorIndex` — disambiguate a symbol that
    #     collides across many files (10 `VectorIndex` in larql crates).
    #     We match where label == basename (the symbol) AND source_file
    #     of that node ends with the path-prefix segment.
    #
    # Without this branch a `/`-bearing query never hits step 1 (idx is
    # keyed on basename, not path), substring fails (key is longer than
    # any single label), and we fall through to fuzzy — which returns
    # basename-similar typos and calls them "ambiguous (3)" even though
    # the user told us exactly which file.
    if "/" in key:
        prefix, _, basename = key.rpartition("/")

        def _label_matches_basename(label_norm: str, target: str) -> bool:
            """Match a node label to a path-qualifier basename, tolerating
            the decoration the AST extractor adds: trailing `()` on
            functions/methods, leading `.` on bound methods, leading
            `_` for private. Without this, `kg/compile-v2.ts/compile`
            never matches the function labeled `compile()` and falls
            through to fuzzy — which picks the file `compile-v2.ts`
            on similarity. The whole reason the user typed the path
            qualifier was to NOT pick the file.

            Lap-10: strip leading `_` from BOTH sides symmetrically. Before,
            label `_classify_file()` lstripped `_` → `classify_file`, but
            target `_classify_file` was compared verbatim — so the user
            typing the underscore-form (the actual label) silently failed.
            Now both `tools/x.py/_classify_file` and `tools/x.py/classify_file`
            resolve to the same node.

            Lap-20b: object-literal method nodes have labels like
            `config.run()`, where the `<owner>.` prefix encodes the
            binding name. A user typing `<file>/run` should resolve to
            that node when the file portion narrows it. Accept a
            dotted-suffix match: if the stripped label contains `.`,
            also try the after-last-dot suffix against the target.
            Without this, every TS experiment file's `config.run` fell
            through to global fuzzy on `/run` queries."""
            stripped = label_norm.rstrip("()").lstrip(".").lstrip("_")
            target_stripped = target.lstrip("_")
            if (stripped == target
                    or stripped == target_stripped
                    or label_norm == target):
                return True
            if "." in stripped:
                suffix = stripped.rsplit(".", 1)[-1]
                if suffix == target or suffix == target_stripped:
                    return True
            return False

        def _path_segment_matches(sf: str, segment: str) -> bool:
            """Match `source_file` against a path segment, tolerating
            extension elision on the file portion. `src/kg/compile-v2.ts`
            matches both `kg/compile-v2.ts` (current) AND `kg/compile-v2`
            (stem-only, lap-7 fix). Without the stem fallback, the form
            the disambig hint suggested (`<dir>/<filename-without-ext>/<symbol>`)
            never resolved — only the verbatim source_file path worked."""
            if sf == segment or sf.endswith("/" + segment):
                return True
            # Stem fallback: strip the extension off the last segment of sf
            # and retry. Required because `idx` keys on basename and the
            # extractor records source_file with extension, so the natural
            # qualifier shape `compile-v2/compile` (without `.ts`) had no
            # match-path before this branch.
            parts = sf.split("/")
            last = parts[-1]
            stem, dot, _ext = last.partition(".")
            if not dot:
                return False
            sf_stem = "/".join(parts[:-1] + [stem]) if len(parts) > 1 else stem
            return sf_stem == segment or sf_stem.endswith("/" + segment)

        path_hits = []
        # Walk every node — the previous `idx.get(basename)`
        # short-circuit only matched literally-equal labels and
        # missed the parenthesized function shape that's actually
        # what the agent meant. ~O(N) per resolver call is fine
        # for graphs in the 10K-node range.
        for nid, attrs in G.nodes(data=True):
            label_norm = _norm(attrs.get("label", nid))
            sf = _norm(attrs.get("source_file") or "")
            # File-shape: full path matches source_file AND the label
            # is the basename (no decoration). `kg/compile-v2.ts` →
            # node labeled `compile-v2.ts`.
            if _path_segment_matches(sf, key) and label_norm == basename:
                path_hits.append(nid)
                continue
            # Symbol-shape: source_file matches the prefix segment AND
            # label matches basename (with decoration stripping).
            # `kg/compile-v2.ts/compile` and `kg/compile-v2/compile`
            # both → node `compile()` in `kg/compile-v2.ts`.
            if prefix and _path_segment_matches(sf, prefix) \
                    and _label_matches_basename(label_norm, basename):
                path_hits.append(nid)
        if len(path_hits) == 1:
            return path_hits[0], [], "exact", []
        if len(path_hits) > 1:
            path_hits.sort(key=lambda n: _rank_match(G, key, n))
            return None, path_hits, "exact", []

        # 1b'. Symbol-restricted file fuzzy. Lap-20: strict path matching
        # above requires `source_file` endswith `prefix` (or stem-elided
        # form). When the agent types a partial filename — `multianti.ts/unit`
        # for actual file `experiments/ref-id-logit-delta-multianti.ts` —
        # endswith fails (`-multianti.ts` ≠ `/multianti.ts`). Old fall-through
        # ran fuzzy on labels globally and matched semantically-unrelated
        # basenames (e.g. `multi-entity.ts`), since difflib similarity on
        # the `multi*.ts` shape ignored that the file in question doesn't
        # contain the symbol the user asked for.
        #
        # Restrict candidate files to those whose nodes contain a
        # label-matching basename. Then rank by how well the file's name
        # matches `prefix`: substring > dash-stem prefix > generic fuzzy.
        # If a unique file wins, return its symbol node. Without this,
        # path-qualified queries fall back to the same global fuzzy that
        # produced the brittleness in the first place.
        if prefix:
            sym_files: dict[str, list[str]] = defaultdict(list)
            for nid, attrs in G.nodes(data=True):
                label_norm = _norm(attrs.get("label", nid))
                if _label_matches_basename(label_norm, basename):
                    sf = _norm(attrs.get("source_file") or "")
                    if sf:
                        sym_files[sf].append(nid)
            if sym_files:
                ranked = _rank_files_by_prefix(list(sym_files.keys()), prefix)
                # Keep only files at the best score tier — substring beats
                # stem-prefix beats generic fuzzy. If multiple files tie at
                # the top tier, present them as disambig.
                if ranked:
                    top_tier = ranked[0][0]
                    top_files = [sf for tier, _, sf in ranked if tier == top_tier]
                    if len(top_files) == 1:
                        nids = sym_files[top_files[0]]
                        if len(nids) == 1:
                            return nids[0], [], "exact", []
                        nids_sorted = sorted(nids, key=lambda n: _rank_match(G, key, n))
                        return None, nids_sorted, "exact", []
                    if len(top_files) > 1:
                        all_nids = [n for sf in top_files for n in sym_files[sf]]
                        all_nids.sort(key=lambda n: _rank_match(G, key, n))
                        return None, all_nids, "exact", []

        # 1d. Negative-case fallback. Lap-20b TS-Claude field report:
        # `<file>/<sym>` falls through to global fuzzy when symbol portion
        # misses, landing on a similar-named file in a different directory
        # (`@multianti.ts/nonexistent` → `multi-entity.ts`). Detect "prefix
        # resolves confidently to one or more real files, but no node in
        # those files matches the basename" and surface those files'
        # actual symbols rather than letting global fuzzy pick a wrong
        # file. Match_type `no_match_in_file` lets navigate render a
        # clear pivot label ("`<basename>` not in <file>; available:")
        # instead of the generic disambig header.
        if prefix:
            all_files = {_norm(attrs.get("source_file") or "")
                         for _nid, attrs in G.nodes(data=True)
                         if attrs.get("source_file")}
            ranked = _rank_files_by_prefix([f for f in all_files if f], prefix)
            if ranked:
                top_tier = ranked[0][0]
                if top_tier <= 2:
                    top_files = {sf for tier, _, sf in ranked if tier == top_tier}
                    file_symbols: list[str] = []
                    for nid, attrs in G.nodes(data=True):
                        sf = _norm(attrs.get("source_file") or "")
                        if sf in top_files \
                                and attrs.get("file_type") == "code" \
                                and attrs.get("node_kind") != "file":
                            file_symbols.append(nid)
                    if file_symbols:
                        file_symbols.sort(key=lambda n: _rank_match(G, key, n))
                        return None, file_symbols, "no_match_in_file", []

    # 1c. dotted Class.method qualifier. `Runner.__init__`, `Klein.compute()`,
    # or `Cell.bar` should resolve to the method node directly. Today the
    # natural dotted form (the one the agent reaches for from Python/JS/TS
    # idiom) falls through to fuzzy and returns "no node matches" because
    # the label is `.bar()` (method-shape, no class qualifier in the
    # label itself). The fix: when target has exactly one `.` (and isn't
    # a path), split into class + method, resolve the class(es), walk
    # method/contains edges, match by normalized method name.
    if "." in key and "/" not in key and not key.startswith("."):
        cls_part, _, meth_part = key.partition(".")
        # Guard: only fire when both halves look like names (no extra dots,
        # no spaces, non-empty). Multi-segment paths like `pkg.mod.Class`
        # are ambiguous between "pkg.mod" being a class with method "Class"
        # and "pkg" being a class with method "mod.Class". Defer those.
        if cls_part and meth_part and "." not in meth_part:
            # Strip method decoration on the user-typed form so a user
            # who typed `compute()` resolves the same as `compute`.
            meth_target = meth_part.rstrip("()").lstrip("_")
            cls_matches = idx.get(cls_part, [])
            # Lap-20c (EGF head-to-head field report): walk ALL matching
            # classes when the class name is shared across files (e.g.
            # `SpectralGraphGeometry` exists in both production and
            # `investigations/`). Previously we bailed here and let
            # global fuzzy pick — which silently landed on the draft
            # because the rank tie-break on long similar labels is
            # subtle. Walking all candidates and emitting a disambig
            # gives the agent the choice and surfaces the duplication
            # honestly.
            if cls_matches:
                method_hits: list[str] = []
                for cls_nid in cls_matches:
                    for v in G.successors(cls_nid):
                        e = G.edges[cls_nid, v]
                        rel = e.get("relation") or ""
                        if rel not in ("method", "contains"):
                            continue
                        child_label = _norm(G.nodes[v].get("label", v))
                        child_stripped = child_label.rstrip("()").lstrip(".").lstrip("_")
                        if child_stripped == meth_target or child_label == meth_part:
                            method_hits.append(v)
                if len(method_hits) == 1:
                    return method_hits[0], [], "exact", []
                if len(method_hits) > 1:
                    method_hits.sort(key=lambda n: _rank_match(G, key, n))
                    return None, method_hits, "exact", []

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

    # 2b. directory-name fallback (lap-27 #6). Before fuzzy guesses from
    # labels, try interpreting the bare input as a directory name. Useful
    # when the agent reaches for `@<dir>` to scope (e.g., `@tests`) and
    # there's no symbol with that name. Trailing-slash form was handled
    # up top; this branch is the last-resort for slash-less input that
    # missed every prior resolver stage.
    if "/" not in key and "." not in key:
        dir_hits = _files_in_directory(G, key)
        if len(dir_hits) == 1:
            return dir_hits[0], [], "exact", []
        if len(dir_hits) > 1:
            dir_hits.sort(key=lambda n: _rank_match(G, key, n))
            return None, dir_hits, "exact", []

    # 3. fuzzy (typo) fallback — labels only. difflib returns close matches in
    # similarity-desc order; preserve that ordering rather than re-ranking,
    # since fuzzy similarity is the dominant signal for typos.
    all_labels = {_norm(G.nodes[n].get("label", n)): n for n in G.nodes()}
    close = get_close_matches(key, list(all_labels.keys()), n=_FUZZY_LIMIT, cutoff=0.7)
    fuzzy_ids = [all_labels[lbl] for lbl in close]
    if not fuzzy_ids:
        return None, [], "fuzzy", []
    if len(fuzzy_ids) == 1:
        return fuzzy_ids[0], [], "fuzzy", []
    # Multiple fuzzy hits: present as disambig listing rather than auto-picking
    # — the score gap with many candidates isn't reliable enough to silently
    # commit. The disambig itself acts as the pivot menu.
    return None, fuzzy_ids, "fuzzy", []
