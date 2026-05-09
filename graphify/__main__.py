"""graphify CLI - `graphify install` sets up the Claude Code skill."""
from __future__ import annotations
import json
import platform
import re
import shutil
import sys
from pathlib import Path

try:
    from importlib.metadata import version as _pkg_version
    __version__ = _pkg_version("graphifyy")
except Exception:
    __version__ = "unknown"


def _check_skill_version(skill_dst: Path) -> None:
    """Warn if the installed skill is from an older graphify version."""
    version_file = skill_dst.parent / ".graphify_version"
    if not version_file.exists():
        return
    installed = version_file.read_text(encoding="utf-8").strip()
    if installed != __version__:
        print(f"  warning: skill is from graphify {installed}, package is {__version__}. Run 'graphify install' to update.")


def _refresh_all_version_stamps() -> None:
    """After a successful install, update .graphify_version in all other known skill dirs.

    Prevents stale-version warnings from platforms that were installed previously
    but not explicitly re-installed during this upgrade.
    """
    for cfg in _PLATFORM_CONFIG.values():
        vf = Path.home() / cfg["skill_dst"]
        vf = vf.parent / ".graphify_version"
        if vf.exists():
            vf.write_text(__version__, encoding="utf-8")

# Per-subcommand help blocks. Defined once and reused both by the top-level
# `graphify --help` and by `graphify <cmd> --help` so subcommand help stays
# in sync with the global usage screen and a confused agent typing
# `graphify navigate --help` doesn't have its `--help` parsed as an op.
_HELP_BLOCKS: dict[str, list[str]] = {
    "path": [
        "  path \"A\" \"B\"            shortest path between two nodes in graph.json",
        "    --graph <path>          path to graph.json (default graphify-out/graph.json)",
        "    --include-inferred      include LLM-inferred edges (default: AST-extracted only)",
        "    --edges <mode>          reach (default): exclude type_ref/rationale_for and (symbol-to-symbol) contains/imports — semantic reachability.  calls: strict call-graph (calls/method/impl_of/inherits only) — \"how does X reach Y at runtime?\".  all: every edge type (literal connectedness, may route through type signatures).",
    ],
    "explain": [
        "  explain \"X\"             plain-language explanation of a node and its neighbors",
        "    --graph <path>          path to graph.json (default graphify-out/graph.json)",
        "    --include-inferred      include LLM-inferred edges (default: AST-extracted only)",
        "    --limit N               max neighbors to list (default 20)",
    ],
    "changed": [
        "  changed [ref]           list code files added/modified/removed since graph extract (default: walk working tree, compare mtime to graph.json)",
        "    --since-graph           explicit form of the default mode — files modified since the graph extract, regardless of working-tree clean state",
        "    --since-commit <ref>    diff vs a known git baseline (e.g. `--since-commit ae96912` or branch name); same as the positional [ref] but discoverable",
        "    --since <ref>           shorter alias for --since-commit",
        "    --graph <path>          path to graph.json (default graphify-out/graph.json)",
    ],
    "summarize": [
        "  summarize @<Class>      one-call class summary — signature + method list + used-by (cross-file callers) + inheritance (parent + siblings + children). Targets the body-heavy class-comprehension failure mode where agents fall back to read_file because no graphify verb fuses class info.",
        "    --methods N             max methods listed (default 30; +N more tail if truncated)",
        "    --callers N             max cross-file callers listed (default 8)",
        "    --md                    render labels as `[label](file:line)` markdown links",
        "    --no-archived           skip archived (frozen/legacy/deprecated/archive) candidates in disambig (default)",
        "    --archived-only         restrict candidates to archived paths (inspecting legacy code)",
        "    --all-archived          show all candidates, archived included",
        "    --graph <path>          path to graph.json (default graphify-out/graph.json)",
        "    Resolves the same as `peek`/`blast` (Class.method, path-qualified, fuzzy fallback). Errors if target isn't a class/interface; suggests `peek` or `blast` for other shapes.",
        "    Disambig auto-picks the unique non-archived match when `--no-archived` (default) leaves exactly one candidate; the count is surfaced as `+N archived hidden`.",
        "  summarize               (no target) repo-wide architectural overview: top communities, cross-file entry points, edge mix, language counts, freshness. Mostly useful for first-contact orientation.",
        "    --graph <path>          path to graph.json (default graphify-out/graph.json)",
    ],
    "peek": [
        "  peek <symbol>           one-shot body read — resolve, dump body, touch no cursor/session/history",
        "    --lines N               max lines of body to dump (default 200; walker bails at natural dedent first)",
        "    --tail N                last N source lines of the body (skips earlier lines — useful for return values / cleanup of large fns)",
        "    --range A-B             keep file-absolute lines A..B of the body (1-indexed, inclusive). Use the `L<x>-<y>` shown by `shape`/`navigate` to pick a window. Mutually exclusive with --tail.",
        "    --no-docstring          drop a leading docstring/JSDoc block from the body. Pairs with `doc` — after `doc @foo` already showed the rationale, `peek --no-docstring @foo` skips re-reading it. Surfaces `−N docstring` in the header.",
        "    --bodies N              when peeking a class, body lines per method (default 3)",
        "    --md                    render the focus label as a clickable `[label](file:line)` link",
        "    --graph <path>          path to graph.json (default graphify-out/graph.json)",
        "    Pairs with `navigate ... read` — use peek when you don't want to commit to a session.",
        "    Accepts `Class.method` and `dir/file/Symbol` qualifiers, same as navigate.",
        "    On a class node, peek emits a curated dump (class header + each method's sig + N body lines) instead of the full class body — saves the per-method walk.",
        "    Brace-expand `peek @Class.{m1,m2,m3}` to dump several method bodies in one call (collapses N peek calls — common shape when comprehending a class). Misses print inline; exit 1 only when every target misses.",
    ],
    "locate": [
        "  locate <s1> [<s2> ...]  multi-symbol file:line lookup, no body — find where many things live in one call",
        "    --graph <path>          path to graph.json (default graphify-out/graph.json)",
        "    Resolves each arg the same way `peek` does (Class.method, path-qualified, fuzzy fallback). Output is one row per target with `<label>  <file>:<line-range>`. Ambiguous and missed symbols print a per-row note but don't fail the batch; exit 1 only when every symbol misses.",
        "    Use when you'd otherwise run 3+ `peek`/`navigate` calls just to find file:line for several symbols you already know by name.",
    ],
    "files": [
        "  files <glob>            list source-file nodes whose basename (or full path, if glob has `/`) matches",
        "    --graph <path>          path to graph.json (default graphify-out/graph.json)",
        "    Bare patterns (`*test*.py`, `*.rs`) match basename. Path patterns (`tools/*.py`, `tests/test_*.py`) match the full source_file. fnmatch syntax — `*`, `?`, `[seq]` — case-sensitive.",
        "    Use when you'd otherwise run `find -name <glob>` to scope which files exist before pivoting. Pairs with `shape <file>` (orient on one) and `@<dir>/` (list a directory). Exits 1 on no-match so callers can branch.",
    ],
    "scripts": [
        "  scripts [<glob>]        list CLI-script files (script_kind tagged) with entry-point line(s)",
        "    --kind <name>           filter by kind: main_block / main, top_level / tl, shebang / sh",
        "    --graph <path>          path to graph.json (default graphify-out/graph.json)",
        "    Output: one row per script-tagged file, kind-grouped (main first, then tl, then sh) with `<path>  script:<short>  L<entry-lines>`. Short kinds: `main` (canonical `if __name__ == \"__main__\":` / `require.main` / `import.meta.main`), `tl` (top-level statement / call to an own-defined fn — the no-clunky-main investigation style), `sh` (shebang only).",
        "    Optional glob filter same as `files`: bare = basename (`*.py`), path = full path (`tools/*.py`).",
        "    Use when you'd otherwise run `grep -rn \"if __name__\"` or scan a directory for runnable scripts. One call returns every CLI script in the indexed corpus — including the `top_level` style that no `__main__` grep would catch.",
    ],
    "blast": [
        "  blast <symbol>          one-shot blast radius — callers + callees of a symbol, side-by-side, cursor-free",
        "    --limit N               max items per side (default 30)",
        "    --include-inferred      include LLM-inferred edges (default: AST-extracted only)",
        "    --md                    render labels and locations as `[label](src:line)` markdown links",
        "    --graph <path>          path to graph.json (default graphify-out/graph.json)",
        "    Resolves the same as `peek` (Class.method, path-qualified, fuzzy fallback). Emits two sections — `## Callers` and `## Callees` — each restricted to call edges. Pairs with refactor-planning prompts (\"what calls X and what does X call?\"). Cursor-free; touches no session.",
        "    Use when you'd otherwise run `navigate @sym in` then re-focus and `navigate @sym out --kind=calls` — that's three calls; blast is one.",
        "    Brace-expand `blast @Class.{m1,m2,m3}` to dump callers+callees for each in one call (mirrors multi-peek). Misses print inline; exit 1 only when every target misses.",
    ],
    "doc": [
        "  doc <symbol>            one-shot signature + docstring/rationale dump — \"what does this metric/method/class mean?\" without pulling the implementation",
        "    --lines N               max lines per rationale block (default 40)",
        "    --md                    render labels as `[label](file:line)` markdown links",
        "    --json                  structured JSON output (incompatible with brace-expand multi-doc)",
        "    --graph <path>          path to graph.json (default graphify-out/graph.json)",
        "    Resolves the same as `peek` (Class.method, path-qualified, fuzzy fallback). When no rationale is attached, the signature still prints + a hint to `peek`/`navigate`.",
        "    Brace-expand `doc @Class.{m1,m2,m3}` to dump signatures + docstrings for several methods in one call (mirrors multi-peek/multi-blast). Misses print inline; exit 1 only when every target misses.",
    ],
    "shape": [
        "  shape <file> [<file> ...]   file structure summary: N classes / M fns / K consts / X imports / longest fn — orientation without committing to a `contains` pivot",
        "    --limit N               max class/fn names listed in the summary (default 8; the `+N more` tail still surfaces what was truncated)",
        "    --all                   list every class/fn name (no truncation; pairs well with `shape large_file.ts --all` when you already know the file is the target)",
        "    --json                  structured JSON output (single target: dict; multi: list of dicts)",
        "    --graph <path>          path to graph.json (default graphify-out/graph.json)",
        "    Resolves the same as `peek` (path-qualified, fuzzy fallback). Errors if target isn't a file.",
        "    Multi-target: `shape f1.py f2.py f3.py` runs shape on each and emits one section per file. Brace-expand `shape {f1,f2,f3}.py` is supported (mirrors peek/blast/doc). Misses print inline; exit 1 only when every target misses.",
    ],
    "search": [
        "  search <pattern>        body-text grep across nodes — return hits with symbol context (label, file:line, container, community, degree)",
        "    --kind code|rationale|all   restrict by node file_type (default code: skips doc-comment fragments)",
        "    --no-archived           skip archived paths (default)",
        "    --archived-only         show only matches in archived/legacy code",
        "    --all-archived          include both active and archived",
        "    --limit N               max hits (default 50)",
        "    --context N             N lines of pre/post context around each match (default 1; pass 0 to disable)",
        "    --by-symbol             collapse same-symbol hits — one row per containing node with `×N (lines: ...)` (good for `where is X used?`)",
        "    --files-only            grep -l analog: one row per file with match count + first match lines, no per-line snippets. Coarsest grouping; wins over --by-symbol if both set.",
        "    --in-files <glob>       restrict the file scan to source_files matching this fnmatch glob. Patterns with `/` match the full path; bare patterns match basename. Closes the `grep -r --include=<glob>` pattern.",
        "    --idents                treat the pattern as an identifier; auto-OR all 5 casings (snake_case, kebab-case, camelCase, PascalCase, SCREAMING_SNAKE) with word-boundary anchors. Cuts the rename-audit OR by hand.",
        "    --md                    label rendered as `[label](file:line)` markdown link",
        "    --json                  structured JSON output",
        "    --graph <path>          path to graph.json (default graphify-out/graph.json)",
        "    Pattern is a case-insensitive regex; falls back to literal substring on `re.error`. The mode is surfaced in the header so unintended substring fallbacks are visible.",
    ],
    "navigate": [
        "  navigate [ops...]       cursor-based graph navigation (LLM-friendly)",
        "    @<label>                focus on a node by label/id (fuzzy fallback for typos)",
        "    @<Class>.<method>       method-on-class shortcut (`@Runner.compute` resolves to the class's `.compute()` method, not a free function named `compute`)",
        "    @.<method>()            method-label form — leading dot marks it as a method (matches `.compute()` across classes via substring; pick from disambig if multiple)",
        "    @<dir/file>             path-qualified file resolution (`@tools/foo.py` or `@tools/foo` — extension optional)",
        "    @<dir/file/Symbol>      path-qualified symbol (`@tools/foo.py/_classify_file` — leading `_` works either way)",
        "    in | out | methods | contains    list typed pivots",
        "    callers | callees       sugar for `in --kind=calls` / `out --kind=calls`",
        "    dependents              transitive callers (default depth=3 via call edges; --depth N overrides)",
        "    dependencies            transitive callees (default depth=3 via call edges; --depth N overrides)",
        "    coc                     co-community siblings (same Leiden cluster)",
        "    coc summary             structural shape (top hubs / composition / edge mix) instead of enumeration — cheap on big communities",
        "    rat | inh | parent      rationale anchors / inherits / structural parent",
        "    siblings                structural peers (same parent file/class)",
        "    read | body [N]         dump full body of focused node (N caps lines, default 200; walker bails at natural dedent first)",
        "    [N] | N                 focus on Nth item from previous listing in the chain",
        "    back | reset            pop history / clear cursor",
        "    --session <id>          resume a prior session (id printed when chaining is in flight: --session was passed, a chain paused at disambig, or the cursor walked >1 step)",
        "    --no-session            disable session entirely (no disk, no id printed)",
        "    --show-session <id>     render the saved cursor's frontier without mutating it (peek where you left off; no ops processed)",
        "    --quiet-hints           suppress all `hint:` lines (also: per-session dedup means each hint kind shows once when --session <id> is passed)",
        "    --json                  structured JSON output",
        "    --include-inferred      include LLM-inferred edges (default: AST-extracted only)",
        "    --min-confidence X      drop edges below score X (only meaningful with --include-inferred)",
        "    --kind <rel[,rel,...]>  restrict in/out listings to edges of these relations (e.g. calls,uses)",
        "    --node-kind <k[,k,...]> restrict listings to nodes of these kinds (e.g. function,method,class). Useful on `@<key>` substring disambig: `--node-kind=function` lops file/iface/external rows.",
        "    --bodies N              show first N source lines under each contains/methods item",
        "    --depth N               for in/out, walk N hops via non-structural edges (default 1)",
        "    --limit N               max items per listing (default 25)",
        "    --legend                prepend column-key legend",
        "    --ops-hint              append the ops cheat-sheet line (default: omitted; cheat-sheet still surfaces on first-contact empty-cursor calls)",
        "    --no-ops-hint           back-compat no-op (cheat-sheet is now off by default)",
        "    --no-archived           hide nodes under frozen/legacy/deprecated/archive(d)/ paths",
        "    --archived-only         show only archived nodes (inverse of --no-archived)",
        "    --include-files         widen `coc` to include file-level hubs (default: symbols only)",
        "    --code-only             filter rationale nodes from `coc` listings — orient on the code-symbol neighbourhood without docstring fragments",
        "    --no-collapse           expand dupe-label groups in listings (default: collapse ≥5 same-label rows into one)",
        "    --explain-cost          on a pivot, return `would-show N nodes ≈ K bytes` preview without rendering the listing (lets you gate `coc` against a 1000-node community before committing)",
        "    --md                    render labels and src:line as `[label](src:line)` markdown links (clickable in IDE / Claude Code)",
        "    --transitive            on `out` from a file node with 0 direct out edges, route through `contains` children and aggregate their outbound semantic edges (collapses the script-leaf drill into one call)",
        "    --graph <path>          path to graph.json (default graphify-out/graph.json)",
        "    Chain ops in one call: graphify navigate @Foo methods 1 in",
    ],
}


def _print_subcmd_help(cmd: str, long: bool = False) -> None:
    """Print `<cmd> --help` (short) or `<cmd> --help-long` (full).

    Lap-27 #5: short by default — just the verb's signature line(s),
    which is what an LLM scanning for "what does this verb do?"
    actually needs. `--help-long` keeps the full flag dump for the
    times the agent is committing to a specific call.

    Each block's first line (and any other 2-space-indented line, like
    summarize's `@<Class>` and `(no target)` shapes) is a top-level
    signature; 4-space-indented lines are flags or descriptive notes.
    """
    block = _HELP_BLOCKS.get(cmd)
    if not block:
        # Unknown subcommand → defer to the caller; top-level help screen
        # already prints every subcommand so we don't synthesize anything here.
        return
    print(f"Usage: graphify {cmd} ...")
    print()
    if long:
        for line in block:
            print(line)
    else:
        for line in block:
            if line.startswith("    "):
                continue
            print(line)
        print()
        print(f"  (use `graphify {cmd} --help-long` for flags + examples)")
    print()


def _print_top_help_short() -> None:
    """Lap-27 #5: short top-level help. One line per verb, compact
    install zoo, decision rule deferred to --help-long. The agent
    scanning for verb names doesn't pay 5K tokens for flags they're
    not about to use.
    """
    print("Usage: graphify <command>")
    print()
    print("Common workflows:")
    print("  Orient on a repo      graphify summarize")
    print("  Orient on a file      graphify shape <file>")
    print("  Read a function body  graphify peek <symbol>")
    print("  Find callers of X     graphify navigate \"@X\" in")
    print("  Find a string         graphify search \"<regex>\"")
    print("  Stale graph?          graphify changed   (then `graphify update .`)")
    print()
    # First-line-of-block per verb in display order. 2-space-indented
    # lines (verb signatures with brief descriptions); 4-space lines
    # (flag entries) are filtered.
    print("Commands:")
    for verb in ("navigate", "peek", "shape", "doc", "blast", "locate",
                 "files", "scripts", "summarize", "search", "path", "explain", "changed"):
        block = _HELP_BLOCKS.get(verb, [])
        for line in block:
            if line.startswith("    "):
                continue
            print(line)
    print("  diff <old> <new>        compare two graph snapshots")
    print("  update <path>           re-extract source after edits (no LLM)")
    print("  watch <path>            live rebuild on code changes")
    print("  query \"<question>\"       BFS traversal of graph.json")
    print("  benchmark               token reduction vs naive full-corpus")
    print("  add <url>               fetch URL into ./raw, then update graph")
    print("  cluster-only <path>     rerun clustering on existing graph.json")
    print("  save-result             save Q&A to graphify-out/memory/")
    print()
    print("Install (per platform):")
    print("  install [--platform P]  copy skill to platform config dir")
    print("                          (claude|gemini|codex|opencode|aider|cursor|")
    print("                           windows|copilot|vscode|claw|droid|trae|")
    print("                           trae-cn|antigravity|hermes|kiro)")
    print("  <platform> install      per-platform skill + hook setup")
    print("  <platform> uninstall    remove platform-specific config")
    print("  hook install / uninstall    post-commit/post-checkout git hooks")
    print()
    print("Run `graphify --help-long` for flags, decision rule, and examples.")
    print("Run `graphify <command> --help-long` for one command's full reference.")


_SETTINGS_HOOK = {
    "matcher": "Read|Glob|Grep",
    "hooks": [
        {
            "type": "command",
            # Defer the gating decision to `graphify _hook` so we can suppress
            # the nudge on non-code Reads (graphify only indexes source —
            # nudging on a .md/.json/.yaml read is noise) without ballooning
            # this shell line into something unmaintainable. The shell guard
            # avoids paying python startup when no graph exists.
            "command": (
                "[ -f graphify-out/graph.json ] && "
                "graphify _hook 2>/dev/null || true"
            ),
        }
    ],
}


# --- PreToolUse hook handler ---------------------------------------------

# Lap-27 #9 anti-spam guardrails. The hook is invoked by Claude Code on
# every Read / Glob / Grep — without these gates the same nudge text fires
# dozens of times per session, conditioning the agent to filter it out.
# Each gate is conservative (false-positive cheap; one extra silent call is
# fine) and they stack so a single positive signal kills the nudge.
_HOOK_QUIET_ENV = "GRAPHIFY_HOOK_QUIET"
# Lap-27 followup: smart-mode opt-in. When `GRAPHIFY_HOOK_MODE=smart`,
# the hook runs the graphify verb the agent should have used (shape /
# search / files) IN-PROCESS and BLOCKS the original Read/Grep/Glob,
# returning the rendered output as the block reason. The agent gets
# the structured data it would have asked for next anyway, without
# paying the bytes for a full file Read. Subsequent calls on the same
# (tool, target) pass through silently — see the cli-stamp + recent-
# paths dedup. Default mode is `nudge` (additionalContext only, tool
# still runs). Anything else (unset / "nudge" / typo) → nudge mode.
_HOOK_MODE_ENV = "GRAPHIFY_HOOK_MODE"
# Recent CLI use TTL: how long after a graphify command finishes does the
# hook stay silent. 5 min keeps the lid on while an agent is actively
# pivoting; longer would hide the nudge from agents who briefly used
# graphify and then drifted back to raw Read.
_HOOK_RECENT_USE_TTL = 5 * 60
# Read-side TTL for the recent-paths log. Bumped from 600 to 1800 (30 min)
# in lap-27 #9 to match navigate.RECENT_PATHS_TTL — the navigate-side
# write keeps lines for 30 min, and reading with a shorter window
# accidentally re-nudges on files that navigate had already surfaced
# 11-30 min ago.
_HOOK_RECENT_PATHS_TTL = 30 * 60
# File-size floor: don't nudge for tiny files. graphify's value prop is
# "scout structure cheaper than reading" — a 50-line module is already
# read-cheap and the agent doesn't need orientation. 8 KB ≈ 100-150 lines
# of typical code; raises the bar for the nudge on small reads.
_HOOK_MIN_FILE_BYTES = 8 * 1024
# Source-code extensions the nudge applies to. Mirror of
# graphify.detect.CODE_EXTENSIONS — kept inline so the hook stays
# stdlib-only (importing detect pulls in the rest of the package).
# Update both lists if either changes.
_HOOK_CODE_EXTS = frozenset({
    ".py", ".ts", ".js", ".jsx", ".tsx", ".mjs", ".ejs", ".go",
    ".rs", ".java", ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp",
    ".rb", ".swift", ".kt", ".kts", ".cs", ".scala", ".php",
    ".lua", ".toc", ".zig", ".ps1", ".ex", ".exs", ".m", ".mm",
    ".jl", ".vue", ".svelte", ".dart", ".v", ".sv",
})


def _stamp_recent_files(graph_path: Path, files) -> None:
    """Write source_files to recent-paths so the PreToolUse hook stays
    out of the way after a graphify CLI verb has touched these files.

    Lap-27 followup: closes the gap between cli-stamp (5-min global
    "graphify is in use") and per-file dedup. If an agent runs
    `graphify shape /a.py` from terminal at minute 0 then Reads /a.py
    at minute 10, cli-stamp has expired but recent-paths still
    contains /a.py — hook bails. Best-effort; failures swallowed
    because absent stamps just mean a possible repeat nudge, not a
    correctness bug.
    """
    try:
        from graphify.navigate import _record_session_paths
    except Exception:  # pragma: no cover - defensive
        return
    normed: set[str] = set()
    for sf in files:
        if not sf:
            continue
        try:
            normed.add(str(Path(sf).resolve()))
        except OSError:
            normed.add(str(sf))
    if not normed:
        return
    try:
        _record_session_paths(graph_path, normed)
    except Exception:
        return


def _hook_run_substitute(tool: str, fp: str, tool_input: dict,
                         root: Path) -> str | None:
    """Smart-mode substitute: run the graphify verb that mirrors `tool`
    in-process and return the rendered text. Returns None on any error
    (caller falls through to silent passthrough or nudge).

    Mappings:
      Read fp        → graphify shape fp   (one-screen file structure)
      Grep pattern   → graphify search pat (--files-only for cheap output)
      Glob pattern   → graphify files pat  (file-node listing)
    """
    try:
        graph_path = root / "graphify-out" / "graph.json"
        if not graph_path.exists() or graph_path.stat().st_size < 32:
            return None
        from graphify.navigate import (
            load_graph, shape_file, _render_shape_text,
            search_bodies, _render_search_text,
        )
        from graphify.resolve import label_index, resolve_focus
        from graphify.analyze import _is_file_node
        if tool == "Read":
            if not fp:
                return None
            G, _comm = load_graph(graph_path, freshness_check=False)
            idx = label_index(G)
            chosen, _cands, _mt, _alts = resolve_focus(G, idx, fp)
            if not chosen or not _is_file_node(G, chosen):
                return None
            data = shape_file(G, chosen, limit=8)
            return _render_shape_text(data)
        if tool == "Grep":
            pat = (tool_input.get("pattern") or "").strip()
            if not pat:
                return None
            G, _comm = load_graph(graph_path, freshness_check=False)
            data = search_bodies(G, pat, kind="code", archived_mode="no",
                                  limit=20, context=1, files_only=True)
            text = _render_search_text(data)
            # search_bodies on no-match returns a header-only block;
            # treat as failure so the agent isn't blocked on an empty
            # response (their Grep might have hit a different corpus).
            if not text or "no hits" in text.lower():
                return None
            return text
        if tool == "Glob":
            pat = (tool_input.get("pattern") or "").strip()
            if not pat:
                return None
            import fnmatch as _fm
            G, _comm = load_graph(graph_path, freshness_check=False)
            path_glob = "/" in pat
            hits: list[str] = []
            for nid, attrs in G.nodes(data=True):
                if attrs.get("node_kind") != "file":
                    continue
                sf = attrs.get("source_file") or ""
                if not sf:
                    continue
                target = sf if path_glob else sf.rsplit("/", 1)[-1]
                if _fm.fnmatch(target, pat):
                    hits.append(sf)
            if not hits:
                return None
            hits.sort()
            lines = [f"files: {len(hits)} matching `{pat}`"]
            for sf in hits[:30]:
                lines.append(f"  {sf}")
            if len(hits) > 30:
                lines.append(f"  +{len(hits) - 30} more")
            return "\n".join(lines)
    except Exception:
        return None
    return None


def _handle_pretool_hook(payload: dict, root: Path) -> dict | None:
    """Decide whether to emit the navigate-nudge for one PreToolUse event.

    Returns the JSON output dict on fire, None to suppress. `root` is the
    project root (where `graphify-out/` lives). Tested directly via
    test_hooks.py — no subprocess needed.

    Gates (any one suppresses):
      1. `GRAPHIFY_HOOK_QUIET` env var set → silent.
      2. graphify CLI was used in the last `_HOOK_RECENT_USE_TTL` sec →
         silent (the agent is already in the flow).
      3. Tool isn't Read/Glob/Grep → silent (matcher should already prevent
         this, but defense-in-depth).
      4. Read-specific:
           a. file extension isn't in `_HOOK_CODE_EXTS` → silent.
           b. file lives under `graphify-out/` → silent.
           c. file size < `_HOOK_MIN_FILE_BYTES` → silent (small files
              don't benefit from orientation).
           d. file is in the recent-paths log within
              `_HOOK_RECENT_PATHS_TTL` → silent (already navigated /
              already nudged-on).

    Side effect on fire (Read only): append (timestamp, real_path) to
    recent-paths so subsequent reads of the same file are quiet.
    """
    import os
    import os.path as _osp
    import time as _t

    if os.environ.get(_HOOK_QUIET_ENV, "").strip() not in ("", "0", "false", "False"):
        return None

    tool = payload.get("tool_name") or ""
    if tool not in ("Read", "Glob", "Grep"):
        return None

    session_dir = root / "graphify-out" / ".session"
    cli_stamp = session_dir / "cli-stamp"
    now = _t.time()

    # Recent CLI use suppression — applies to all three tool types.
    try:
        if cli_stamp.exists():
            try:
                stamp_ts = float(cli_stamp.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                stamp_ts = cli_stamp.stat().st_mtime
            if now - stamp_ts <= _HOOK_RECENT_USE_TTL:
                return None
    except OSError:
        pass

    inp = payload.get("tool_input") or {}
    fp = (inp.get("file_path") or "").strip()
    file_size: int | None = None

    if tool == "Read":
        # Lap-27 followup: precise-Read short-circuit. If the agent
        # already specified --offset / --limit they're targeting a
        # known line range — they almost certainly already shaped or
        # peeked. Don't fight; let the precise Read through.
        if inp.get("offset") is not None or inp.get("limit") is not None:
            return None
        ext = _osp.splitext(fp)[1].lower()
        if ext not in _HOOK_CODE_EXTS:
            return None
        if "graphify-out/" in fp:
            return None
        # File-size floor: skip the nudge on small files. Stat may fail
        # (file outside graph cwd, permissions); on any error fall through
        # to the nudge so we don't silently drop legitimate cases. The
        # size we read here is also reused below to anchor the nudge in
        # a concrete number ("Reading 12 KB") rather than a generic
        # "before reading unfamiliar code".
        try:
            file_size = Path(fp).stat().st_size
            if file_size < _HOOK_MIN_FILE_BYTES:
                return None
        except OSError:
            pass
        # Per-file dedup via the navigate recent-paths log. Both sides
        # realpath because macOS aliases /tmp → /private/tmp; raw abspath
        # would miss.
        recent_log = session_dir / "recent-paths"
        target = ""
        try:
            target = _osp.realpath(fp) if fp else ""
        except OSError:
            target = fp
        if recent_log.exists():
            try:
                for line in recent_log.read_text(encoding="utf-8").splitlines():
                    ts_str, _, path = line.partition("\t")
                    try:
                        if now - float(ts_str) > _HOOK_RECENT_PATHS_TTL:
                            continue
                    except ValueError:
                        continue
                    if path and target and path == target:
                        return None
            except OSError:
                pass

    # Lap-27 followup: smart-mode dispatch. When `GRAPHIFY_HOOK_MODE=smart`,
    # run the substitute graphify verb in-process and BLOCK the original
    # tool with the rendered output as the block reason. Agent gets the
    # structured data it would have asked for next, without paying the
    # bytes for the original Read/Grep/Glob. Smart-mode falls through
    # to nudge mode silently if the substitute can't help (no graph,
    # target not in graph, empty search hits) — don't block the agent
    # on a bad substitute.
    mode = (os.environ.get(_HOOK_MODE_ENV) or "nudge").strip().lower()
    if mode == "smart":
        sub_text = _hook_run_substitute(tool, fp, inp, root)
        if sub_text:
            # Record dedup so the retry passes through. Per-file for
            # Read; cli-stamp covers Grep/Glob retry within 5 min
            # (load_graph in the substitute touched it).
            if tool == "Read" and fp:
                try:
                    session_dir.mkdir(parents=True, exist_ok=True)
                    recent_log = session_dir / "recent-paths"
                    try:
                        target = _osp.realpath(fp)
                    except OSError:
                        target = fp
                    existing: list[str] = []
                    if recent_log.exists():
                        try:
                            existing = recent_log.read_text(
                                encoding="utf-8").splitlines()
                        except OSError:
                            existing = []
                    existing = [
                        line for line in existing
                        if line.partition("\t")[2] != target
                    ]
                    existing.append(f"{now:.0f}\t{target}")
                    if len(existing) > 200:
                        existing = existing[-200:]
                    recent_log.write_text(
                        "\n".join(existing) + "\n", encoding="utf-8")
                except OSError:
                    pass
            # Build the smart-mode block message. Tells the agent we
            # ran the verb on their behalf, shows the output, and
            # explicitly says the hook will stay out of the way for
            # follow-up calls — so an honest retry isn't punished.
            if tool == "Read":
                size_hint = ""
                if file_size is not None:
                    kb = max(1, file_size // 1024)
                    size_hint = f" ({kb} KB Read)"
                header = (
                    f"graphify shape \"{fp}\" ran on your behalf"
                    f"{size_hint} — output below. Retry your Read "
                    f"if the body is still needed; the hook stays "
                    f"silent for follow-up Reads of this file.")
            elif tool == "Grep":
                pat = (inp.get("pattern") or "").strip()
                header = (
                    f"graphify search \"{pat}\" --files-only ran on "
                    f"your behalf — output below (symbol-attributed "
                    f"file list). Retry your Grep if you still need "
                    f"raw match lines; the hook stays silent for "
                    f"follow-ups within ~5 min.")
            else:  # Glob
                pat = (inp.get("pattern") or "").strip()
                header = (
                    f"graphify files \"{pat}\" ran on your behalf — "
                    f"output below. Retry your Glob if needed; the "
                    f"hook stays silent for follow-ups within ~5 min.")
            block_msg = f"{header}\n\n{sub_text}"
            # Emit BOTH legacy `decision/reason` AND the newer
            # hookSpecificOutput.permissionDecision form so the block
            # works across Claude Code versions.
            return {
                "decision": "block",
                "reason": block_msg,
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": block_msg,
                },
            }
        # Substitute returned None — fall through to nudge mode below.

    # Lap-27 #9 follow-up: tailor the nudge to the tool. We know which
    # verb actually fits the shape of what the agent is about to do —
    # `shape` for a Read (file-level orientation), `search` for a Grep
    # (regex with symbol attribution), `files` for a Glob (file listing
    # against the indexed set). Naming the right verb in the nudge is
    # higher-signal than the old generic "use navigate" — and including
    # the actual file/pattern they typed makes the suggestion
    # copy-pasteable.
    if tool == "Read":
        size_hint = ""
        if file_size is not None:
            kb = max(1, file_size // 1024)
            size_hint = f"Reading {kb} KB. "
        # Quote the path as-typed so the suggested command is directly
        # runnable. graphify shape resolves both relative and absolute
        # paths, so whichever form the agent passed Read works here.
        msg = (
            f"{size_hint}Cheaper first: `graphify shape \"{fp}\"` returns "
            f"a one-screen summary (classes / fns / longest fn / entry "
            f"points / line ranges) so the follow-up Read can use "
            f"--offset/--limit instead of dumping the whole file. For a "
            f"single declaration, `graphify peek \"@<symbol>\"` is a "
            f"cursor-free body dump. (Hook stays silent on follow-up "
            f"Reads of this file.) See ~/.claude/skills/graphify/SKILL.md."
        )
    elif tool == "Grep":
        pat = (inp.get("pattern") or "").strip()
        if pat:
            msg = (
                f"`graphify search \"{pat}\"` runs the same regex but "
                f"returns hits with symbol attribution (file:line + "
                f"enclosing fn/class) and ±1 line of context — strictly "
                f"more info for the same query. See "
                f"~/.claude/skills/graphify/SKILL.md."
            )
        else:
            msg = (
                "`graphify search \"<pattern>\"` runs the same regex but "
                "returns hits with symbol attribution (file:line + "
                "enclosing fn/class) and ±1 line of context. See "
                "~/.claude/skills/graphify/SKILL.md."
            )
    elif tool == "Glob":
        pat = (inp.get("pattern") or "").strip()
        if pat:
            msg = (
                f"`graphify files \"{pat}\"` mirrors the glob over the "
                f"indexed file set — each match is a graph node you can "
                f"`shape`/`navigate` from in one more call. See "
                f"~/.claude/skills/graphify/SKILL.md."
            )
        else:
            msg = (
                "`graphify files \"<glob>\"` mirrors a glob over the "
                "indexed file set — each match is a graph node you can "
                "`shape`/`navigate` from. See "
                "~/.claude/skills/graphify/SKILL.md."
            )
    else:
        # Defensive fallback — the gate above should already exclude this.
        return None
    # Staleness banner: piggybacks on the same fire when the graph is
    # conspicuously behind the working tree. Per-30min stamp prevents
    # banner-spam without affecting the main nudge cadence.
    try:
        graph_p = root / "graphify-out" / "graph.json"
        if graph_p.exists():
            graph_age = now - graph_p.stat().st_mtime
            if graph_age > 86400:  # > 1 day
                stamp = session_dir / "banner-stamp"
                last_banner = 0.0
                if stamp.exists():
                    try:
                        last_banner = float(stamp.read_text().strip())
                    except (OSError, ValueError):
                        last_banner = 0.0
                if now - last_banner > 1800:  # 30 min
                    days = int(graph_age // 86400)
                    days_str = (f"{days}d" if days >= 1
                                else f"{int(graph_age // 3600)}h")
                    banner = (f"⚠ graph was extracted {days_str} ago — "
                              f"results may be stale. `graphify update .` "
                              f"refreshes incrementally. ")
                    msg = banner + msg
                    try:
                        stamp.parent.mkdir(parents=True, exist_ok=True)
                        stamp.write_text(f"{now:.0f}", encoding="utf-8")
                    except OSError:
                        pass
    except Exception:
        pass

    # Dedup-on-fire: write this Read's path into recent-paths so the same
    # file doesn't re-nudge within the TTL. Best-effort — failure here just
    # means a possible repeat nudge, not a correctness bug.
    if tool == "Read" and fp:
        try:
            session_dir.mkdir(parents=True, exist_ok=True)
            recent_log = session_dir / "recent-paths"
            try:
                target = _osp.realpath(fp)
            except OSError:
                target = fp
            existing: list[str] = []
            if recent_log.exists():
                try:
                    existing = recent_log.read_text(encoding="utf-8").splitlines()
                except OSError:
                    existing = []
            existing = [
                line for line in existing
                if line.partition("\t")[2] != target
            ]
            existing.append(f"{now:.0f}\t{target}")
            # Cap matches RECENT_PATHS_MAX in navigate.py.
            if len(existing) > 200:
                existing = existing[-200:]
            recent_log.write_text("\n".join(existing) + "\n",
                                  encoding="utf-8")
        except OSError:
            pass

    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": msg,
    }}

_SKILL_REGISTRATION = (
    "\n# graphify\n"
    "- **graphify** (`~/.claude/skills/graphify/SKILL.md`) "
    "- any input to knowledge graph. Trigger: `/graphify`\n"
    "When the user types `/graphify`, invoke the Skill tool "
    "with `skill: \"graphify\"` before doing anything else.\n"
)


_PLATFORM_CONFIG: dict[str, dict] = {
    "claude": {
        "skill_file": "skill.md",
        "skill_dst": Path(".claude") / "skills" / "graphify" / "SKILL.md",
        "claude_md": True,
    },
    "codex": {
        "skill_file": "skill-codex.md",
        "skill_dst": Path(".agents") / "skills" / "graphify" / "SKILL.md",
        "claude_md": False,
    },
    "opencode": {
        "skill_file": "skill-opencode.md",
        "skill_dst": Path(".config") / "opencode" / "skills" / "graphify" / "SKILL.md",
        "claude_md": False,
    },
    "aider": {
        "skill_file": "skill-aider.md",
        "skill_dst": Path(".aider") / "graphify" / "SKILL.md",
        "claude_md": False,
    },
    "copilot": {
        "skill_file": "skill-copilot.md",
        "skill_dst": Path(".copilot") / "skills" / "graphify" / "SKILL.md",
        "claude_md": False,
    },
    "claw": {
        "skill_file": "skill-claw.md",
        "skill_dst": Path(".openclaw") / "skills" / "graphify" / "SKILL.md",
        "claude_md": False,
    },
    "droid": {
        "skill_file": "skill-droid.md",
        "skill_dst": Path(".factory") / "skills" / "graphify" / "SKILL.md",
        "claude_md": False,
    },
    "trae": {
        "skill_file": "skill-trae.md",
        "skill_dst": Path(".trae") / "skills" / "graphify" / "SKILL.md",
        "claude_md": False,
    },
    "trae-cn": {
        "skill_file": "skill-trae.md",
        "skill_dst": Path(".trae-cn") / "skills" / "graphify" / "SKILL.md",
        "claude_md": False,
    },
    "hermes": {
        "skill_file": "skill-claw.md",
        "skill_dst": Path(".hermes") / "skills" / "graphify" / "SKILL.md",
        "claude_md": False,
    },
    "kiro": {
        "skill_file": "skill-kiro.md",
        "skill_dst": Path(".kiro") / "skills" / "graphify" / "SKILL.md",
        "claude_md": False,
    },
    "antigravity": {
        "skill_file": "skill.md",
        "skill_dst": Path(".agent") / "skills" / "graphify" / "SKILL.md",
        "claude_md": False,
    },
    "windows": {
        "skill_file": "skill-windows.md",
        "skill_dst": Path(".claude") / "skills" / "graphify" / "SKILL.md",
        "claude_md": True,
    },
}


def install(platform: str = "claude") -> None:
    if platform == "gemini":
        gemini_install()
        return
    if platform == "cursor":
        _cursor_install(Path("."))
        return
    if platform not in _PLATFORM_CONFIG:
        print(
            f"error: unknown platform '{platform}'. Choose from: {', '.join(_PLATFORM_CONFIG)}, gemini, cursor",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg = _PLATFORM_CONFIG[platform]
    skill_src = Path(__file__).parent / cfg["skill_file"]
    if not skill_src.exists():
        print(f"error: {cfg['skill_file']} not found in package - reinstall graphify", file=sys.stderr)
        sys.exit(1)

    skill_dst = Path.home() / cfg["skill_dst"]
    skill_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(skill_src, skill_dst)
    (skill_dst.parent / ".graphify_version").write_text(__version__, encoding="utf-8")
    print(f"  skill installed  ->  {skill_dst}")

    if cfg["claude_md"]:
        # Register in ~/.claude/CLAUDE.md (Claude Code only)
        claude_md = Path.home() / ".claude" / "CLAUDE.md"
        if claude_md.exists():
            content = claude_md.read_text(encoding="utf-8")
            if "graphify" in content:
                print(f"  CLAUDE.md        ->  already registered (no change)")
            else:
                claude_md.write_text(content.rstrip() + _SKILL_REGISTRATION, encoding="utf-8")
                print(f"  CLAUDE.md        ->  skill registered in {claude_md}")
        else:
            claude_md.parent.mkdir(parents=True, exist_ok=True)
            claude_md.write_text(_SKILL_REGISTRATION.lstrip(), encoding="utf-8")
            print(f"  CLAUDE.md        ->  created at {claude_md}")

    if platform == "opencode":
        _install_opencode_plugin(Path("."))

    # Refresh version stamps in all other previously-installed skill dirs so
    # stale-version warnings don't fire for platforms not explicitly re-installed.
    _refresh_all_version_stamps()

    print()
    print("Done. Open your AI coding assistant and type:")
    print()
    print("  /graphify .")
    print()


_CLAUDE_MD_SECTION = """\
## graphify

This project has a graphify knowledge graph at `graphify-out/graph.json`. Use it to keep your context window from collapsing under a heavy codebase.

### When you should reach for graphify

Before any of these moves, scout the graph first — it's 50–500x cheaper than the alternative:

- **About to `Read` a source-code file you don't already know.** Run `graphify shape "<file>"` for a one-screen summary, or `graphify navigate "@<symbol>"` for the affordance frame. The frontier shows you whether the file is a leaf, hub, or router, and what shape of context you actually need.
- **About to chain `Grep` / `Glob` calls to trace a call graph or find who-uses-X.** That's literally what `graphify navigate` `in`/`out`/`path` are for.
- **About to grep for a string in source code.** Reach for `graphify search "<pattern>"` over `grep -n` when (a) you don't already know the containing symbol, or (b) you want to see parallel definitions across the repo. Search returns hits with symbol attribution (label, file:line, container, community) and ±1 line of context, repo-wide by default. Use raw `grep` for non-source files (markdown, JSON, configs) and right after recent edits when the graph is stale.
- **About to read a single function to remind yourself what it does.** `graphify peek "<symbol>"` is a one-shot body dump — no cursor, no session.
- **About to implement, change, or debug something in unfamiliar territory.** Map the blast radius first: focus the entry point, run `in --depth=2 --kind=calls` to see callers two hops out, decide what's actually load-bearing.
- **You don't know where to start.** `graphify navigate "@<best-guess-label>"` is a free probe — a hit returns a frontier, a miss returns real names you can grab onto.

**Don't reach for graphify when reading**: `.json` / `.yaml` / `.toml` / `.csv` / `.md` / `.txt` / `.log` / lockfiles / build output / your own memory or scratch files. graphify only indexes source code — for data, configs, prose, and machine output, just `Read` directly.

### Verbs

```
graphify navigate "@<symbol>"                       # focus + frontier (~200 tok)
graphify navigate "@<symbol>" methods 6 in          # chain: focus, list methods, pick 6th, show callers
graphify peek "<symbol>"                            # one-shot body dump, no cursor
graphify shape "<file>" [<file> ...]                # N classes / M fns / longest fn (multi-target + brace-expand `{a,b}.py`)
graphify search "<pattern>"                         # body-text grep, hits attributed to symbol
graphify search "<pat>" --files-only                # grep -l analog: one row per file with match count (no per-line snippets)
graphify search "<pat>" --in-files "<glob>"         # grep -r --include analog: restrict to source_files matching glob
graphify files "<glob>"                             # list source-file nodes by basename or path glob (find -name analog)
graphify locate <s1> <s2> ...                       # multi-symbol file:line, no body
graphify blast "<symbol>"                           # callers + callees side-by-side, cursor-free
graphify doc "<symbol>"                             # signature + docstring/rationale dump
graphify path "A" "B"                               # reachability between two nodes (~50 tok)
graphify explain "<symbol>"                         # one-shot summary of one node (~350 tok)
graphify diff old.json new.json                     # what changed: added/removed nodes/edges
graphify update .                                   # AST re-extract after edits, no LLM cost
graphify changed [git-ref]                          # files added/modified since last extract or vs ref
```

### Resolution forms (the `@` target)

- `@<label>` — by symbol name, fuzzy-fallback for typos.
- `@<Class>.<method>` — method-on-class shortcut. `@Runner.compute` lands on the method, *not* a free function `compute()`.
- `@.<method>()` — method-label form (`.compute()`); use when you don't know the owning class. Disambig listing if more than one class has it.
- `@<dir/file>` or `@<dir/file/Symbol>` — path-qualified. Extension optional; leading `_` works either way.

### Useful flags on `navigate`

| flag | when |
|---|---|
| `--include-inferred` | widen to LLM-inferred edges (default: AST-only) |
| `--depth N` | walk N hops via non-structural edges; pairs with `--kind=calls` for blast-radius |
| `--kind <rel>[,...]` | restrict edges (e.g. `--kind=calls`) |
| `--bodies N` | first N source lines under each `contains`/`methods` row — catches dead stubs / pass-throughs |
| `--explain-cost` | preview "would-show N nodes ≈ K bytes" before committing on a big pivot |
| `--transitive` | from a script-leaf file with no direct out-edges, walk via `contains` and aggregate children's outbound |
| `--code-only` | filter rationale (docstring) nodes out of `coc` listings |
| `--md` | render labels and src:line as markdown links for IDE click-through |
| `--show-session <id>` | peek a saved session's frontier without mutating it (great for parallel exploration) |
| `--limit N` | raise per-listing cap from 25 |

Per-id cursor files mean parallel calls don't race. The session id only prints when chaining is in flight (chain paused at disambig, `--session` was passed, or cursor walked >1 step) — pass it via `--session <id>` to resume.

### Dispatching sub-agents (Agent / Explore / Plan)

Sub-agents run with their own system prompts that hard-code `find` / `grep` / `glob` workflows. **They do not reliably inherit graphify guidance from CLAUDE.md** — verified A/B: an Explore sub-agent in a graphify-indexed project ran 30 `find` / `grep` / `awk` / `head` calls for a question graphify answers in 2 (`shape <file>` then `navigate "@<symbol>"`).

When you dispatch a sub-agent in this directory, paste this into the prompt:

> This project has a graphify knowledge graph at `graphify-out/graph.json`. Reach for graphify BEFORE find/grep when the question is about code structure:
> - Where is X defined? → `graphify locate X` (multi-symbol: `locate X Y Z`)
> - What's in this file? → `graphify shape <file>` (multi: `shape f1.py f2.py`)
> - Which files match a pattern? → `graphify files "<glob>"` (e.g. `*test*.py`, `tools/*.py`)
> - Who calls X? → `graphify navigate "@X" in --kind=calls`
> - Who uses X (incl. typed dispatch)? → `graphify navigate "@X" wu`
> - What strings match? → `graphify search "<pat>"` — add `--idents` for cross-casing, `--files-only` for grep-l, `--in-files "<glob>"` for grep-r --include
> - What's in this directory? → `graphify navigate "@<dir>/"` (trailing slash matters)
>
> **Don't bail after one empty graphify call.** Empty `search` is usually a regex/casing miss — try `shape <file>` or `navigate "@<best-guess-symbol>"` before falling back to grep. graphify is 50–500x cheaper than chained find/grep when the question is about source-code structure.

Without this, the sub-agent burns ~20 calls on what graphify answers in 2–3.

### What NOT to do

- Don't read `GRAPH_REPORT.md` end-to-end — it's a 40KB+ overview that costs ~10K tokens and the community-list section is filler in AST-only mode. Use `graphify navigate` instead.
- Don't run `graphify query` on a question you haven't narrowed yet — it caps at ~2K tokens of flat node listings, mostly noise. Narrow with `navigate` first.
"""

_CLAUDE_MD_MARKER = "## graphify"

# Lap-27 #10: write corpus-specific guidance to a separate file the user
# imports, instead of owning the user's top-level CLAUDE.md. The user's
# CLAUDE.md keeps a single short import line; the file we manage lives
# in .claude/ and is safe to overwrite on update.
_GRAPHIFY_MD_RELPATH = ".claude/graphify.md"
_CLAUDE_MD_IMPORT_LINE = "@.claude/graphify.md"

# AGENTS.md section for Codex, OpenCode, and OpenClaw.
# All three platforms read AGENTS.md in the project root for persistent instructions.
_AGENTS_MD_SECTION = """\
## graphify

This project has a graphify knowledge graph at `graphify-out/graph.json`. Use it to keep your context window from collapsing under a heavy codebase.

### When you should reach for graphify

Before any of these moves, scout the graph first — it's 50–500x cheaper:

- **About to read a source-code file you don't already know.** `graphify shape "<file>"` for a one-screen summary, or `graphify navigate "@<symbol>"` for the affordance frame.
- **About to chain greps to trace a call graph or find who-uses-X.** That's what `graphify navigate` `in`/`out`/`path` are for.
- **About to grep for a string in source code.** `graphify search "<pattern>"` when you don't already know the containing symbol, or you want to see parallel defs across the repo. Hits come with symbol attribution (label, file:line, container) and ±1 line of context, repo-wide by default. Raw `grep` for non-source files / right after edits when the graph is stale.
- **About to read one function.** `graphify peek "<symbol>"` is a one-shot body dump.
- **About to implement, change, or debug in unfamiliar territory.** Map the blast radius first: focus the entry point, `in --depth=2 --kind=calls`.
- **You don't know where to start.** `graphify navigate "@<best-guess-label>"` is a free probe.

**Don't reach for graphify when reading**: `.json` / `.yaml` / `.toml` / `.csv` / `.md` / `.txt` / `.log` / lockfiles / build output / scratch files. graphify only indexes source code.

### Verbs

```
graphify navigate "@<symbol>" methods 6 in   # focus, list methods, pick 6th, show callers
graphify peek "<symbol>"                     # one-shot body dump
graphify shape "<file>" [<file> ...]         # multi-target: class/fn counts, longest fn (brace-expand `{a,b}.py`)
graphify search "<pat>" [--files-only|--in-files <glob>]   # body-text grep, attributed to symbol
graphify files "<glob>"                      # find -name analog: source files by basename or path glob
graphify locate <s1> <s2> ...                # multi-symbol file:line, no body
graphify path "A" "B"                        # reachability (~50 tok)
graphify explain "<symbol>"                  # one-shot node summary (~350 tok)
graphify update .                            # AST re-extract, no LLM cost
```

### Resolution forms (the `@` target)

- `@<label>` — by name, fuzzy on typos.
- `@<Class>.<method>` — method shortcut (`@Runner.compute` resolves to the method, not a free `compute()`).
- `@.<method>()` — method-label form when you don't know the owning class.
- `@<dir/file>` or `@<dir/file/Symbol>` — path-qualified.
- `@<dir>/` — directory listing (trailing slash is required for unambiguous dir intent).

### Useful flags on `navigate`

`--include-inferred` widens to LLM-inferred edges; `--depth N` walks N hops via non-structural edges (`out --depth=2 --kind=calls` for blast-radius); `--bodies N` shows N source lines under each row; `--explain-cost` previews node/byte count before committing; `--code-only` filters rationale nodes from `coc`; `--md` renders labels as markdown links; `--show-session <id>` peeks a saved cursor without mutating it.

Chain ops left-to-right; output is the last op's result. When chaining is in flight (chain paused, `--session` passed, or cursor walked >1 step) the output prints `session: <id>` — pass it via `--session <id>` to resume.

### Dispatching sub-agents

Sub-agents do NOT reliably inherit graphify guidance from project instructions. When dispatching a sub-agent in this directory, paste this into the prompt:

> `graphify-out/graph.json` exists. Reach for graphify before find/grep:
> `locate <sym>` (file:line), `shape <file>` (file structure, multi-target), `files <glob>` (filename pattern), `search <pat>` (`--files-only` for grep -l, `--in-files <glob>` for grep -r --include), `navigate "@X" in --kind=calls` (callers), `navigate "@<dir>/"` (dir listing). Don't bail after one empty graphify call — try `shape` or `navigate` before falling back to grep.

### What NOT to do

- Don't read `GRAPH_REPORT.md` end-to-end — ~10K tokens of overview. Use `graphify navigate` instead.
- Don't run `graphify query` on a question you haven't narrowed yet — flat node dumps, mostly noise.
"""

_AGENTS_MD_MARKER = "## graphify"

_GEMINI_MD_SECTION = """\
## graphify

This project has a graphify knowledge graph at `graphify-out/graph.json`. Use it to keep your context window from collapsing under a heavy codebase.

### When you should reach for graphify

Before any of these moves, scout the graph first — it's 50–500x cheaper:

- **About to read a source-code file you don't already know.** `graphify shape "<file>"` for a one-screen summary, or `graphify navigate "@<symbol>"` for the affordance frame.
- **About to chain greps to trace a call graph or find who-uses-X.** `graphify navigate` `in`/`out`/`path`.
- **About to grep for a string in source code.** `graphify search "<pattern>"` when you don't already know the containing symbol, or you want parallel defs across the repo. Hits come with symbol attribution (label, file:line, container) and ±1 line of context, repo-wide by default. Raw `grep` for non-source files / right after edits when the graph is stale.
- **About to read one function.** `graphify peek "<symbol>"` is a one-shot body dump.
- **About to implement, change, or debug in unfamiliar territory.** Map the blast radius: focus + `in --depth=2 --kind=calls`.
- **You don't know where to start.** `graphify navigate "@<best-guess-label>"` is a free probe.

**Don't reach for graphify when reading**: `.json` / `.yaml` / `.toml` / `.csv` / `.md` / `.txt` / `.log` / lockfiles / build output / scratch files. graphify only indexes source code.

### Verbs

```
graphify navigate "@<symbol>" methods 6 in   # chain: focus, methods, pick 6th, show callers
graphify peek "<symbol>"                     # one-shot body dump
graphify shape "<file>" [<file> ...]         # multi-target: class/fn counts + longest fn (brace-expand `{a,b}.py`)
graphify search "<pat>" [--files-only|--in-files <glob>]   # body-text grep, attributed to symbol
graphify files "<glob>"                      # find -name analog: source files by basename or path glob
graphify locate <s1> <s2> ...                # multi-symbol file:line, no body
graphify path "A" "B"                        # reachability (~50 tok)
graphify explain "<symbol>"                  # node summary (~350 tok)
graphify update .                            # AST re-extract after edits
```

### Resolution forms (the `@` target)

- `@<label>` — by name, fuzzy on typos.
- `@<Class>.<method>` — method shortcut. `@Runner.compute` lands on the method, not a free `compute()`.
- `@.<method>()` — method-label form, owner-class agnostic.
- `@<dir/file>` or `@<dir/file/Symbol>` — path-qualified.
- `@<dir>/` — directory listing (trailing slash is required).

### Useful flags on `navigate`

`--include-inferred`, `--depth N` (multi-hop), `--kind <rel>[,...]`, `--bodies N` (preview lines under each row), `--explain-cost` (preview before committing), `--code-only` (filter rationale from `coc`), `--md` (markdown links), `--show-session <id>` (peek without mutating).

Chain ops left-to-right; output is the last op's result. When chaining is in flight, the output prints `session: <id>` — pass it via `--session <id>` to resume.

### Dispatching sub-agents

Sub-agents do NOT reliably inherit graphify guidance from project instructions. Paste this into the dispatch prompt:

> `graphify-out/graph.json` exists. Reach for graphify before find/grep:
> `locate <sym>` (file:line), `shape <file>` (file structure, multi-target), `files <glob>` (filename pattern), `search <pat>` (`--files-only` for grep -l, `--in-files <glob>` for grep -r --include), `navigate "@X" in --kind=calls` (callers), `navigate "@<dir>/"` (dir listing). Don't bail after one empty graphify call — try `shape` or `navigate` before falling back to grep.

### What NOT to do

- Don't read `GRAPH_REPORT.md` end-to-end — ~10K tokens of overview. Use `graphify navigate` instead.
- Don't run `graphify query` on a question you haven't narrowed yet.
"""

_GEMINI_MD_MARKER = "## graphify"

_GEMINI_HOOK = {
    "matcher": "read_file|list_directory",
    "hooks": [
        {
            "type": "command",
            "command": (
                "[ -f graphify-out/graph.json ] && "
                r"""echo '{"decision":"allow","additionalContext":"graphify-out/graph.json exists. Before reading/listing unfamiliar code, scout it cheaper: `graphify navigate \"@<symbol>\"` returns a dense affordance frame (~200 tok). Then pivot with in/out/methods/coc/parent/[N], or jump to file:line once a node is load-bearing. See ~/.gemini/skills/graphify/SKILL.md."}' """
                r"""|| echo '{"decision":"allow"}'"""
            ),
        }
    ],
}


def gemini_install(project_dir: Path | None = None) -> None:
    """Copy skill file to ~/.gemini/skills/graphify/, write GEMINI.md section, and install BeforeTool hook."""
    # Copy skill file to ~/.gemini/skills/graphify/SKILL.md
    # On Windows, Gemini CLI prioritises ~/.agents/skills/ over ~/.gemini/skills/
    skill_src = Path(__file__).parent / "skill.md"
    if platform.system() == "Windows":
        skill_dst = Path.home() / ".agents" / "skills" / "graphify" / "SKILL.md"
    else:
        skill_dst = Path.home() / ".gemini" / "skills" / "graphify" / "SKILL.md"
    skill_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(skill_src, skill_dst)
    (skill_dst.parent / ".graphify_version").write_text(__version__, encoding="utf-8")
    print(f"  skill installed  ->  {skill_dst}")

    target = (project_dir or Path(".")) / "GEMINI.md"

    if target.exists():
        content = target.read_text(encoding="utf-8")
        if _GEMINI_MD_MARKER in content:
            print("graphify already configured in GEMINI.md")
        else:
            target.write_text(content.rstrip() + "\n\n" + _GEMINI_MD_SECTION, encoding="utf-8")
            print(f"graphify section written to {target.resolve()}")
    else:
        target.write_text(_GEMINI_MD_SECTION, encoding="utf-8")
        print(f"graphify section written to {target.resolve()}")

    _install_gemini_hook(project_dir or Path("."))
    print()
    print("Gemini CLI will now check the knowledge graph before answering")
    print("codebase questions and rebuild it after code changes.")


def _install_gemini_hook(project_dir: Path) -> None:
    settings_path = project_dir / ".gemini" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    except json.JSONDecodeError:
        settings = {}
    before_tool = settings.setdefault("hooks", {}).setdefault("BeforeTool", [])
    settings["hooks"]["BeforeTool"] = [h for h in before_tool if "graphify" not in str(h)]
    settings["hooks"]["BeforeTool"].append(_GEMINI_HOOK)
    settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    print("  .gemini/settings.json  ->  BeforeTool hook registered")


def _uninstall_gemini_hook(project_dir: Path) -> None:
    settings_path = project_dir / ".gemini" / "settings.json"
    if not settings_path.exists():
        return
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    before_tool = settings.get("hooks", {}).get("BeforeTool", [])
    filtered = [h for h in before_tool if "graphify" not in str(h)]
    if len(filtered) == len(before_tool):
        return
    settings["hooks"]["BeforeTool"] = filtered
    settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    print("  .gemini/settings.json  ->  BeforeTool hook removed")


def gemini_uninstall(project_dir: Path | None = None) -> None:
    """Remove the graphify section from GEMINI.md, uninstall hook, and remove skill file."""
    # Remove skill file (mirror the install path detection)
    if platform.system() == "Windows":
        skill_dst = Path.home() / ".agents" / "skills" / "graphify" / "SKILL.md"
    else:
        skill_dst = Path.home() / ".gemini" / "skills" / "graphify" / "SKILL.md"
    if skill_dst.exists():
        skill_dst.unlink()
        print(f"  skill removed    ->  {skill_dst}")
    version_file = skill_dst.parent / ".graphify_version"
    if version_file.exists():
        version_file.unlink()
    for d in (skill_dst.parent, skill_dst.parent.parent):
        try:
            d.rmdir()
        except OSError:
            break

    target = (project_dir or Path(".")) / "GEMINI.md"
    if not target.exists():
        print("No GEMINI.md found in current directory - nothing to do")
        return
    content = target.read_text(encoding="utf-8")
    if _GEMINI_MD_MARKER not in content:
        print("graphify section not found in GEMINI.md - nothing to do")
        return
    cleaned = re.sub(r"\n*## graphify\n.*?(?=\n## |\Z)", "", content, flags=re.DOTALL).rstrip()
    if cleaned:
        target.write_text(cleaned + "\n", encoding="utf-8")
        print(f"graphify section removed from {target.resolve()}")
    else:
        target.unlink()
        print(f"GEMINI.md was empty after removal - deleted {target.resolve()}")
    _uninstall_gemini_hook(project_dir or Path("."))


_VSCODE_INSTRUCTIONS_MARKER = "## graphify"
_VSCODE_INSTRUCTIONS_SECTION = """\
## graphify

Before answering architecture or codebase questions, read `graphify-out/GRAPH_REPORT.md` if it exists.
If `graphify-out/wiki/index.md` exists, navigate it for deep questions.
Type `/graphify` in Copilot Chat to build or update the knowledge graph.
"""


def vscode_install(project_dir: Path | None = None) -> None:
    """Install graphify skill for VS Code Copilot Chat + write .github/copilot-instructions.md."""
    skill_src = Path(__file__).parent / "skill-vscode.md"
    if not skill_src.exists():
        skill_src = Path(__file__).parent / "skill-copilot.md"
    skill_dst = Path.home() / ".copilot" / "skills" / "graphify" / "SKILL.md"
    skill_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(skill_src, skill_dst)
    (skill_dst.parent / ".graphify_version").write_text(__version__, encoding="utf-8")
    print(f"  skill installed  ->  {skill_dst}")

    instructions = (project_dir or Path(".")) / ".github" / "copilot-instructions.md"
    instructions.parent.mkdir(parents=True, exist_ok=True)
    if instructions.exists():
        content = instructions.read_text(encoding="utf-8")
        if _VSCODE_INSTRUCTIONS_MARKER in content:
            print(f"  {instructions}  ->  already configured (no change)")
        else:
            instructions.write_text(content.rstrip() + "\n\n" + _VSCODE_INSTRUCTIONS_SECTION, encoding="utf-8")
            print(f"  {instructions}  ->  graphify section added")
    else:
        instructions.write_text(_VSCODE_INSTRUCTIONS_SECTION, encoding="utf-8")
        print(f"  {instructions}  ->  created")

    print()
    print("VS Code Copilot Chat configured. Type /graphify in the chat panel to build the graph.")
    print("Note: for GitHub Copilot CLI (terminal), use: graphify copilot install")


def vscode_uninstall(project_dir: Path | None = None) -> None:
    """Remove graphify VS Code Copilot Chat skill and .github/copilot-instructions.md section."""
    skill_dst = Path.home() / ".copilot" / "skills" / "graphify" / "SKILL.md"
    if skill_dst.exists():
        skill_dst.unlink()
        print(f"  skill removed    ->  {skill_dst}")
    version_file = skill_dst.parent / ".graphify_version"
    if version_file.exists():
        version_file.unlink()
    for d in (skill_dst.parent, skill_dst.parent.parent, skill_dst.parent.parent.parent):
        try:
            d.rmdir()
        except OSError:
            break

    instructions = (project_dir or Path(".")) / ".github" / "copilot-instructions.md"
    if not instructions.exists():
        return
    content = instructions.read_text(encoding="utf-8")
    if _VSCODE_INSTRUCTIONS_MARKER not in content:
        return
    cleaned = re.sub(r"\n*## graphify\n.*?(?=\n## |\Z)", "", content, flags=re.DOTALL).rstrip()
    if cleaned:
        instructions.write_text(cleaned + "\n", encoding="utf-8")
        print(f"  graphify section removed from {instructions}")
    else:
        instructions.unlink()
        print(f"  {instructions}  ->  deleted (was empty after removal)")


_ANTIGRAVITY_RULES_PATH = Path(".agent") / "rules" / "graphify.md"
_ANTIGRAVITY_WORKFLOW_PATH = Path(".agent") / "workflows" / "graphify.md"

_ANTIGRAVITY_RULES = """\
## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- If the graphify MCP server is active, utilize tools like `query_graph`, `get_node`, and `shortest_path` for precise architecture navigation instead of falling back to `grep`
- After modifying code files in this session, run `graphify update .` to keep the graph current (AST-only, no API cost)
"""

_ANTIGRAVITY_WORKFLOW = """\
# Workflow: graphify
**Command:** /graphify
**Description:** Turn any folder of files into a navigable knowledge graph

## Steps
Follow the graphify skill installed at ~/.agent/skills/graphify/SKILL.md to run the full pipeline.

If no path argument is given, use `.` (current directory).
"""


_KIRO_STEERING = """\
---
inclusion: always
---

graphify: A knowledge graph of this project lives in `graphify-out/`. \
If `graphify-out/GRAPH_REPORT.md` exists, read it before answering architecture questions, \
tracing dependencies, or searching files — it contains god nodes, community structure, \
and surprising connections the graph found. Navigate by graph structure instead of grepping raw files.
"""

_KIRO_STEERING_MARKER = "graphify: A knowledge graph of this project"


def _kiro_install(project_dir: Path) -> None:
    """Write graphify skill + steering file for Kiro IDE/CLI."""
    project_dir = project_dir or Path(".")

    # Skill file → .kiro/skills/graphify/SKILL.md
    skill_src = Path(__file__).parent / "skill-kiro.md"
    skill_dst = project_dir / ".kiro" / "skills" / "graphify" / "SKILL.md"
    skill_dst.parent.mkdir(parents=True, exist_ok=True)
    skill_dst.write_text(skill_src.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"  {skill_dst.relative_to(project_dir)}  ->  /graphify skill")

    # Steering file → .kiro/steering/graphify.md (always-on)
    steering_dir = project_dir / ".kiro" / "steering"
    steering_dir.mkdir(parents=True, exist_ok=True)
    steering_dst = steering_dir / "graphify.md"
    if steering_dst.exists() and _KIRO_STEERING_MARKER in steering_dst.read_text(encoding="utf-8"):
        print(f"  .kiro/steering/graphify.md  ->  already configured")
    else:
        steering_dst.write_text(_KIRO_STEERING, encoding="utf-8")
        print(f"  .kiro/steering/graphify.md  ->  always-on steering written")

    print()
    print("Kiro will now read the knowledge graph before every conversation.")
    print("Use /graphify to build or update the graph.")


def _kiro_uninstall(project_dir: Path) -> None:
    """Remove graphify skill + steering file for Kiro."""
    project_dir = project_dir or Path(".")
    removed = []

    skill_dst = project_dir / ".kiro" / "skills" / "graphify" / "SKILL.md"
    if skill_dst.exists():
        skill_dst.unlink()
        removed.append(str(skill_dst.relative_to(project_dir)))
        # Remove parent dir if empty
        try:
            skill_dst.parent.rmdir()
        except OSError:
            pass

    steering_dst = project_dir / ".kiro" / "steering" / "graphify.md"
    if steering_dst.exists():
        steering_dst.unlink()
        removed.append(str(steering_dst.relative_to(project_dir)))

    print("Removed: " + (", ".join(removed) if removed else "nothing to remove"))


def _antigravity_install(project_dir: Path) -> None:
    """Install graphify for Google Antigravity: skill + .agent/rules + .agent/workflows."""
    # 1. Copy skill file to ~/.agent/skills/graphify/SKILL.md
    install(platform="antigravity")

    # 1.5. Inject YAML frontmatter for native Antigravity tool discovery
    skill_dst = Path.home() / _PLATFORM_CONFIG["antigravity"]["skill_dst"]
    if skill_dst.exists():
        content = skill_dst.read_text(encoding="utf-8")
        if not content.startswith("---\n"):
            frontmatter = "---\nname: graphify-manager\ndescription: Rebuild the code graph or perform manual CLI queries when MCP server is offline.\n---\n\n"
            skill_dst.write_text(frontmatter + content, encoding="utf-8")

    # 2. Write .agent/rules/graphify.md
    rules_path = project_dir / _ANTIGRAVITY_RULES_PATH
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    if rules_path.exists():
        print(f"graphify rule already exists at {rules_path} (no change)")
    else:
        rules_path.write_text(_ANTIGRAVITY_RULES, encoding="utf-8")
        print(f"graphify rule written to {rules_path.resolve()}")

    # 3. Write .agent/workflows/graphify.md
    wf_path = project_dir / _ANTIGRAVITY_WORKFLOW_PATH
    wf_path.parent.mkdir(parents=True, exist_ok=True)
    if wf_path.exists():
        print(f"graphify workflow already exists at {wf_path} (no change)")
    else:
        wf_path.write_text(_ANTIGRAVITY_WORKFLOW, encoding="utf-8")
        print(f"graphify workflow written to {wf_path.resolve()}")

    print()
    print("Antigravity will now check the knowledge graph before answering")
    print("codebase questions. Run /graphify first to build the graph.")
    print()
    print("To enable full MCP architecture navigation, add this to ~/.gemini/antigravity/mcp_config.json:")
    print('  "graphify": {')
    print('    "command": "uv",')
    print('    "args": ["run", "--with", "graphifyy", "--with", "mcp", "-m", "graphify.serve", "${workspace.path}/graphify-out/graph.json"]')
    print('  }')


def _antigravity_uninstall(project_dir: Path) -> None:
    """Remove graphify Antigravity rules, workflow, and skill files."""
    # Remove rules file
    rules_path = project_dir / _ANTIGRAVITY_RULES_PATH
    if rules_path.exists():
        rules_path.unlink()
        print(f"graphify rule removed from {rules_path.resolve()}")
    else:
        print("No graphify Antigravity rule found - nothing to do")

    # Remove workflow file
    wf_path = project_dir / _ANTIGRAVITY_WORKFLOW_PATH
    if wf_path.exists():
        wf_path.unlink()
        print(f"graphify workflow removed from {wf_path.resolve()}")

    # Remove skill file
    skill_dst = Path.home() / _PLATFORM_CONFIG["antigravity"]["skill_dst"]
    if skill_dst.exists():
        skill_dst.unlink()
        print(f"graphify skill removed from {skill_dst}")
    version_file = skill_dst.parent / ".graphify_version"
    if version_file.exists():
        version_file.unlink()
    for d in (skill_dst.parent, skill_dst.parent.parent, skill_dst.parent.parent.parent):
        try:
            d.rmdir()
        except OSError:
            break


_CURSOR_RULE_PATH = Path(".cursor") / "rules" / "graphify.mdc"
_CURSOR_RULE = """\
---
description: graphify knowledge graph context
alwaysApply: true
---

This project has a graphify knowledge graph at graphify-out/.

- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- After modifying code files in this session, run `graphify update .` to keep the graph current (AST-only, no API cost)
"""


def _cursor_install(project_dir: Path) -> None:
    """Write .cursor/rules/graphify.mdc with alwaysApply: true."""
    rule_path = (project_dir or Path(".")) / _CURSOR_RULE_PATH
    rule_path.parent.mkdir(parents=True, exist_ok=True)
    if rule_path.exists():
        print(f"graphify rule already exists at {rule_path} (no change)")
        return
    rule_path.write_text(_CURSOR_RULE, encoding="utf-8")
    print(f"graphify rule written to {rule_path.resolve()}")
    print()
    print("Cursor will now always include the knowledge graph context.")
    print("Run /graphify . first to build the graph if you haven't already.")


def _cursor_uninstall(project_dir: Path) -> None:
    """Remove .cursor/rules/graphify.mdc."""
    rule_path = (project_dir or Path(".")) / _CURSOR_RULE_PATH
    if not rule_path.exists():
        print("No graphify Cursor rule found - nothing to do")
        return
    rule_path.unlink()
    print(f"graphify Cursor rule removed from {rule_path.resolve()}")


# OpenCode tool.execute.before plugin — fires before every tool call.
# Injects a graph reminder into bash command output when graph.json exists.
_OPENCODE_PLUGIN_JS = """\
// graphify OpenCode plugin
// Injects a knowledge graph reminder before bash tool calls when the graph exists.
import { existsSync } from "fs";
import { join } from "path";

export const GraphifyPlugin = async ({ directory }) => {
  let reminded = false;

  return {
    "tool.execute.before": async (input, output) => {
      if (reminded) return;
      if (!existsSync(join(directory, "graphify-out", "graph.json"))) return;

      if (input.tool === "bash") {
        output.args.command =
          'echo "[graphify] Knowledge graph available. Read graphify-out/GRAPH_REPORT.md for god nodes and architecture context before searching files." && ' +
          output.args.command;
        reminded = true;
      }
    },
  };
};
"""

_OPENCODE_PLUGIN_PATH = Path(".opencode") / "plugins" / "graphify.js"
_OPENCODE_CONFIG_PATH = Path("opencode.json")


def _install_opencode_plugin(project_dir: Path) -> None:
    """Write graphify.js plugin and register it in opencode.json."""
    plugin_file = project_dir / _OPENCODE_PLUGIN_PATH
    plugin_file.parent.mkdir(parents=True, exist_ok=True)
    plugin_file.write_text(_OPENCODE_PLUGIN_JS, encoding="utf-8")
    print(f"  {_OPENCODE_PLUGIN_PATH}  ->  tool.execute.before hook written")

    config_file = project_dir / _OPENCODE_CONFIG_PATH
    if config_file.exists():
        try:
            config = json.loads(config_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            config = {}
    else:
        config = {}

    plugins = config.setdefault("plugin", [])
    entry = _OPENCODE_PLUGIN_PATH.as_posix()
    if entry not in plugins:
        plugins.append(entry)
        config_file.write_text(json.dumps(config, indent=2), encoding="utf-8")
        print(f"  {_OPENCODE_CONFIG_PATH}  ->  plugin registered")
    else:
        print(f"  {_OPENCODE_CONFIG_PATH}  ->  plugin already registered (no change)")


def _uninstall_opencode_plugin(project_dir: Path) -> None:
    """Remove graphify.js plugin and deregister from opencode.json."""
    plugin_file = project_dir / _OPENCODE_PLUGIN_PATH
    if plugin_file.exists():
        plugin_file.unlink()
        print(f"  {_OPENCODE_PLUGIN_PATH}  ->  removed")

    config_file = project_dir / _OPENCODE_CONFIG_PATH
    if not config_file.exists():
        return
    try:
        config = json.loads(config_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    plugins = config.get("plugin", [])
    entry = _OPENCODE_PLUGIN_PATH.as_posix()
    if entry in plugins:
        plugins.remove(entry)
        if not plugins:
            config.pop("plugin")
        config_file.write_text(json.dumps(config, indent=2), encoding="utf-8")
        print(f"  {_OPENCODE_CONFIG_PATH}  ->  plugin deregistered")


_CODEX_HOOK = {
    "hooks": {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {
                        "type": "command",
                        "command": (
                            "[ -f graphify-out/graph.json ] && "
                            r"""echo '{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":"graphify: Knowledge graph exists. Read graphify-out/GRAPH_REPORT.md for god nodes and community structure before searching raw files."}}' """
                            "|| true"
                        ),
                    }
                ],
            }
        ]
    }
}


def _install_codex_hook(project_dir: Path) -> None:
    """Add graphify PreToolUse hook to .codex/hooks.json."""
    hooks_path = project_dir / ".codex" / "hooks.json"
    hooks_path.parent.mkdir(parents=True, exist_ok=True)

    if hooks_path.exists():
        try:
            existing = json.loads(hooks_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    else:
        existing = {}

    pre_tool = existing.setdefault("hooks", {}).setdefault("PreToolUse", [])
    existing["hooks"]["PreToolUse"] = [h for h in pre_tool if "graphify" not in str(h)]
    existing["hooks"]["PreToolUse"].extend(_CODEX_HOOK["hooks"]["PreToolUse"])
    hooks_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    print(f"  .codex/hooks.json  ->  PreToolUse hook registered")


def _uninstall_codex_hook(project_dir: Path) -> None:
    """Remove graphify PreToolUse hook from .codex/hooks.json."""
    hooks_path = project_dir / ".codex" / "hooks.json"
    if not hooks_path.exists():
        return
    try:
        existing = json.loads(hooks_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    pre_tool = existing.get("hooks", {}).get("PreToolUse", [])
    filtered = [h for h in pre_tool if "graphify" not in str(h)]
    existing["hooks"]["PreToolUse"] = filtered
    hooks_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    print(f"  .codex/hooks.json  ->  PreToolUse hook removed")


def _agents_install(project_dir: Path, platform: str) -> None:
    """Write the graphify section to the local AGENTS.md (Codex/OpenCode/OpenClaw)."""
    target = (project_dir or Path(".")) / "AGENTS.md"

    if target.exists():
        content = target.read_text(encoding="utf-8")
        if _AGENTS_MD_MARKER in content:
            print(f"graphify already configured in AGENTS.md")
        else:
            target.write_text(content.rstrip() + "\n\n" + _AGENTS_MD_SECTION, encoding="utf-8")
            print(f"graphify section written to {target.resolve()}")
    else:
        target.write_text(_AGENTS_MD_SECTION, encoding="utf-8")
        print(f"graphify section written to {target.resolve()}")

    if platform == "codex":
        _install_codex_hook(project_dir or Path("."))
    elif platform == "opencode":
        _install_opencode_plugin(project_dir or Path("."))

    print()
    print(f"{platform.capitalize()} will now check the knowledge graph before answering")
    print("codebase questions and rebuild it after code changes.")
    if platform not in ("codex", "opencode"):
        print()
        print("Note: unlike Claude Code, there is no PreToolUse hook equivalent for")
        print(f"{platform.capitalize()} — the AGENTS.md rules are the always-on mechanism.")


def _agents_uninstall(project_dir: Path, platform: str = "") -> None:
    """Remove the graphify section from the local AGENTS.md."""
    target = (project_dir or Path(".")) / "AGENTS.md"

    if not target.exists():
        print("No AGENTS.md found in current directory - nothing to do")
        return

    content = target.read_text(encoding="utf-8")
    if _AGENTS_MD_MARKER not in content:
        print("graphify section not found in AGENTS.md - nothing to do")
        return

    cleaned = re.sub(
        r"\n*## graphify\n.*?(?=\n## |\Z)",
        "",
        content,
        flags=re.DOTALL,
    ).rstrip()
    if cleaned:
        target.write_text(cleaned + "\n", encoding="utf-8")
        print(f"graphify section removed from {target.resolve()}")
    else:
        target.unlink()
        print(f"AGENTS.md was empty after removal - deleted {target.resolve()}")

    if platform == "opencode":
        _uninstall_opencode_plugin(project_dir or Path("."))


def claude_install(project_dir: Path | None = None, add_import: bool = True) -> None:
    """Write graphify guidance to .claude/graphify.md and import it from CLAUDE.md.

    Lap-27 #10: graphify owns .claude/graphify.md (project-local, safe to overwrite
    on update). The user's CLAUDE.md stays user-owned — we only add a single
    `@.claude/graphify.md` import line, and we offer --no-import for users who
    want to manage the import themselves. If a legacy `## graphify` section from
    pre-lap-27 installers is present, we migrate it out (we used to own it).
    """
    base = project_dir or Path(".")
    graphify_md = base / _GRAPHIFY_MD_RELPATH
    claude_md = base / "CLAUDE.md"

    # 1. Always write/refresh .claude/graphify.md (this file is ours).
    graphify_md.parent.mkdir(parents=True, exist_ok=True)
    graphify_md.write_text(_CLAUDE_MD_SECTION, encoding="utf-8")
    print(f"  {_GRAPHIFY_MD_RELPATH}  ->  written ({graphify_md.resolve()})")

    # 2. Migrate legacy `## graphify` section out of user's CLAUDE.md if present.
    # Tight check: heading line must be exactly `## graphify` (not e.g.
    # `## graphify isn't a small-task tool` from a project doc that mentions us).
    if claude_md.exists():
        content = claude_md.read_text(encoding="utf-8")
        new_content, n = re.subn(
            r"\n*## graphify\n.*?(?=\n## |\Z)",
            "",
            content,
            flags=re.DOTALL,
        )
        if n:
            cleaned = new_content.rstrip() + ("\n" if new_content.strip() else "")
            claude_md.write_text(cleaned, encoding="utf-8")
            print(f"  CLAUDE.md         ->  legacy '## graphify' section removed (migrated to {_GRAPHIFY_MD_RELPATH})")

    # 3. Add @-import line unless caller opted out.
    if add_import:
        _ensure_import_line(claude_md)
    else:
        print(f"  CLAUDE.md         ->  not touched (--no-import)")
        print(f"  next: add `{_CLAUDE_MD_IMPORT_LINE}` to your CLAUDE.md to load graphify guidance")

    # 4. PreToolUse hook (unchanged).
    _install_claude_hook(base)

    print()
    print("Claude Code will now check the knowledge graph before answering")
    print("codebase questions and rebuild it after code changes.")


def _ensure_import_line(claude_md: Path) -> None:
    """Add `@.claude/graphify.md` to CLAUDE.md if not present. Idempotent."""
    if claude_md.exists():
        content = claude_md.read_text(encoding="utf-8")
        # Match the import line on its own line (allowing leading whitespace).
        if re.search(rf"(?m)^\s*{re.escape(_CLAUDE_MD_IMPORT_LINE)}\s*$", content):
            print(f"  CLAUDE.md         ->  already imports {_GRAPHIFY_MD_RELPATH} (no change)")
            return
        new_content = content.rstrip() + "\n\n" + _CLAUDE_MD_IMPORT_LINE + "\n"
        claude_md.write_text(new_content, encoding="utf-8")
        print(f"  CLAUDE.md         ->  import added ({_CLAUDE_MD_IMPORT_LINE})")
    else:
        claude_md.write_text(_CLAUDE_MD_IMPORT_LINE + "\n", encoding="utf-8")
        print(f"  CLAUDE.md         ->  created with import ({_CLAUDE_MD_IMPORT_LINE})")


def _install_claude_hook(project_dir: Path) -> None:
    """Add graphify PreToolUse hook to .claude/settings.json."""
    settings_path = project_dir / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            settings = {}
    else:
        settings = {}

    hooks = settings.setdefault("hooks", {})
    pre_tool = hooks.setdefault("PreToolUse", [])

    # Match by content, not by specific matcher string — so the dedup survives
    # across versions where the matcher itself changes (e.g. Glob|Grep → Read|Glob|Grep).
    hooks["PreToolUse"] = [h for h in pre_tool if "graphify" not in str(h)]
    hooks["PreToolUse"].append(_SETTINGS_HOOK)
    settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    print(f"  .claude/settings.json  ->  PreToolUse hook registered")


def _uninstall_claude_hook(project_dir: Path) -> None:
    """Remove graphify PreToolUse hook from .claude/settings.json."""
    settings_path = project_dir / ".claude" / "settings.json"
    if not settings_path.exists():
        return
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    pre_tool = settings.get("hooks", {}).get("PreToolUse", [])
    filtered = [h for h in pre_tool if "graphify" not in str(h)]
    if len(filtered) == len(pre_tool):
        return
    settings["hooks"]["PreToolUse"] = filtered
    settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    print(f"  .claude/settings.json  ->  PreToolUse hook removed")


def claude_uninstall(project_dir: Path | None = None) -> None:
    """Remove .claude/graphify.md, import line, legacy section, and hook."""
    base = project_dir or Path(".")
    graphify_md = base / _GRAPHIFY_MD_RELPATH
    claude_md = base / "CLAUDE.md"
    did_anything = False

    # 1. Delete .claude/graphify.md (the file we own).
    if graphify_md.exists():
        graphify_md.unlink()
        print(f"  {_GRAPHIFY_MD_RELPATH}  ->  removed")
        did_anything = True

    # 2. Strip from CLAUDE.md: import line + legacy `## graphify` section.
    if claude_md.exists():
        content = claude_md.read_text(encoding="utf-8")
        original = content

        # Remove import line(s).
        content = re.sub(
            rf"(?m)^\s*{re.escape(_CLAUDE_MD_IMPORT_LINE)}\s*\n?",
            "",
            content,
        )
        # Remove legacy `## graphify` section (defensive: catches users on the
        # pre-lap-27 installer who never re-ran install).
        content = re.sub(
            r"\n*## graphify\n.*?(?=\n## |\Z)",
            "",
            content,
            flags=re.DOTALL,
        )

        if content != original:
            cleaned = content.rstrip()
            if cleaned:
                claude_md.write_text(cleaned + "\n", encoding="utf-8")
                print(f"  CLAUDE.md         ->  graphify references removed")
            else:
                claude_md.unlink()
                print(f"  CLAUDE.md         ->  was empty after removal, deleted")
            did_anything = True

    if not did_anything:
        print("graphify not configured in this project - nothing to do")

    _uninstall_claude_hook(base)


def _expand_brace_multi_target(target: str) -> list[str]:
    """Lap-25: expand `prefix{a,b,c}suffix` → multiple targets.

    Used by `peek` and `blast` to collapse N same-shaped calls into
    one (`peek @Class.{m1,m2,m3}`, `blast @Class.{m1,m2,m3}`) — the
    V2 trial 1 transcript pattern (peek __init__ then peek
    add_all_geometries on the same class). Supports a single brace
    group; only triggers when the inner contains a comma so labels
    with literal `{var}` braces pass through unchanged.
    """
    open_idx = target.find("{")
    if open_idx < 0:
        return [target]
    close_idx = target.find("}", open_idx + 1)
    if close_idx < 0:
        return [target]
    inner = target[open_idx + 1:close_idx]
    if "," not in inner:
        return [target]
    if "{" in inner:  # nested groups not supported
        return [target]
    parts = [p.strip() for p in inner.split(",") if p.strip()]
    if not parts:
        return [target]
    prefix = target[:open_idx]
    suffix = target[close_idx + 1:]
    return [f"{prefix}{p}{suffix}" for p in parts]


def _brace_expand_member_miss(target: str, chosen_label: str) -> tuple[str, str] | None:
    """Lap-26 brace-expand fuzzy-fallback guard.

    `peek @Class.{m1,m2,m3}` expands to per-member targets. If a member
    doesn't actually exist on Class, resolve_focus's fuzzy step returns
    Class itself (the parent — "cursor.load" fuzzy-matches "Cursor"),
    and the curated class-dump path renders the whole class. Repeat for
    every missing member and the agent gets N identical class dumps
    instead of N "no member" lines.

    Detect: target was `<parent>.<member>` form, chosen resolved to
    `<parent>` (the class itself, sans `.member`). Returns `(parent,
    member)` on a miss; `None` otherwise. Caller uses this only in
    multi (brace-expand) mode — single-target peek can still fuzzy-
    fall-back since the agent gets a `# matched ... (fuzzy)` line that
    makes the substitution visible without compounding.
    """
    t = target.lstrip("@").strip()
    if "." not in t or "/" in t:
        return None
    parent, _, member = t.rpartition(".")
    if not parent or not member:
        return None
    cl = chosen_label.lstrip("@").rstrip("()").lstrip(".").strip()
    pre = parent.lstrip("@").rstrip("()").strip()
    if cl.lower() == pre.lower():
        return (parent, member)
    return None


def main() -> None:
    # Check all known skill install locations for a stale version stamp.
    # Skip during install/uninstall (hook writes trigger a fresh check anyway).
    # Deduplicate paths so platforms sharing the same install dir don't warn twice.
    if not any(arg in ("install", "uninstall") for arg in sys.argv):
        for skill_dst in {Path.home() / cfg["skill_dst"] for cfg in _PLATFORM_CONFIG.values()}:
            _check_skill_version(skill_dst)

    # `graphify --version` / `-V` — standard CLI convention. Without this,
    # `--version` falls through to "unknown command".
    if len(sys.argv) >= 2 and sys.argv[1] in ("--version", "-V"):
        print(f"graphify {__version__}")
        return

    # `graphify <cmd> --help` (short, lap-27 #5) or `<cmd> --help-long`
    # (full). Without this branch, `--help` is parsed as a navigate op
    # and errors; common subcommands have entries in `_HELP_BLOCKS`.
    if len(sys.argv) >= 3 and sys.argv[1] in _HELP_BLOCKS:
        if sys.argv[2] in ("-h", "--help"):
            _print_subcmd_help(sys.argv[1], long=False)
            return
        if sys.argv[2] == "--help-long":
            _print_subcmd_help(sys.argv[1], long=True)
            return

    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        # Lap-27 #5: short by default. Cold-start agents reach for
        # verb names, not flag dumps; --help-long stays available
        # below for the times an agent commits to one call.
        _print_top_help_short()
        return

    if sys.argv[1] == "--help-long":
        print("Usage: graphify <command>")
        print()
        # Lap-21 #4 (sub-agent head-to-head): document the win/lose
        # boundary up front. Lap-21 follow-up: priming-for-large-tasks
        # is the under-sold edge — even when the task will eventually
        # need most of a file, leading with `graphify shape` (or
        # `navigate @entry`) primes the structural pivots that inform
        # every Read decision that follows.
        # Lap-21 (R3 sub-agent feedback): sharpen the body-count rule —
        # graphify wins when ≤2 bodies are needed; body-heavy tasks
        # (need most of a file) should fall straight to Read once
        # graphify has primed the file:line targets.
        print("When to use graphify vs Read:")
        print("  graphify first   always lead with shape/navigate/summarize.")
        print("                   The structural primer (entry points, callers,")
        print("                   class shape, line ranges) is cheap and informs")
        print("                   every Read that follows.")
        print("  graphify wins    when you need ≤2 bodies, structure-only answers,")
        print("                   or who-uses-X tracing across the call graph.")
        print("                   `peek` for one body, `locate <s1> <s2>...` for")
        print("                   file:line of several known symbols.")
        print("  Read wins        when you need most of a file (>~200 ln of body)")
        print("                   or the question is line-by-line (formatting,")
        print("                   surrounding context a peek-window misses). Use")
        print("                   `shape`/`navigate` to find the right offsets, ")
        print("                   then read with `offset`/`limit` — don't scout")
        print("                   with peek when you'll end up reading anyway.")
        print()
        # Lap-20d (TS-Claude #5): workflow templates retain better than
        # per-flag docs. Lead with the 80% paths so an agent who only
        # reads the first screen of help can already do useful work.
        print("Common workflows:")
        print("  Orient on a repo       graphify summarize")
        print("  Orient on a file       graphify shape <file>")
        print("  Read a function body   graphify peek <symbol>")
        print("  Read a class           graphify peek <Class>   (curated: header + per-method sig + body)")
        print("  Locate many symbols    graphify locate <s1> <s2> ...   (file:line for each, no body)")
        print("  Find callers of X      graphify navigate \"@X\" in")
        print("  Map a class            graphify navigate \"@Class\" methods --bodies 3")
        print("  Find dead methods      graphify navigate \"@Class\" dead-ends")
        print("  Find a string          graphify search \"<regex>\"")
        print("  Where does X reach Y?  graphify path \"X\" \"Y\" --edges calls")
        print("  Stale graph?           graphify changed   (then `graphify update .`)")
        print()
        print("Commands:")
        print("  install [--platform P]  copy skill to platform config dir (claude|windows|codex|opencode|aider|claw|droid|trae|trae-cn|gemini|cursor|antigravity|hermes|kiro)")
        for line in _HELP_BLOCKS["path"]:
            print(line)
        for line in _HELP_BLOCKS["explain"]:
            print(line)
        for line in _HELP_BLOCKS["changed"]:
            print(line)
        for line in _HELP_BLOCKS["summarize"]:
            print(line)
        for line in _HELP_BLOCKS["peek"]:
            print(line)
        for line in _HELP_BLOCKS["locate"]:
            print(line)
        for line in _HELP_BLOCKS["files"]:
            print(line)
        for line in _HELP_BLOCKS["doc"]:
            print(line)
        for line in _HELP_BLOCKS["shape"]:
            print(line)
        for line in _HELP_BLOCKS["search"]:
            print(line)
        print("  add <url>               fetch a URL and save it to ./raw, then update the graph")
        print("    --author \"Name\"         tag the author of the content")
        print("    --contributor \"Name\"    tag who added it to the corpus")
        print("    --dir <path>            target directory (default: ./raw)")
        print("  watch <path>            watch a folder and rebuild the graph on code changes")
        print("  update <path>           re-extract code files and update the graph (no LLM needed)")
        print("  cluster-only <path>     rerun clustering on an existing graph.json and regenerate report")
        print("  query \"<question>\"       BFS traversal of graph.json for a question")
        print("    --dfs                   use depth-first instead of breadth-first")
        print("    --budget N              cap output at N tokens (default 2000)")
        print("    --graph <path>          path to graph.json (default graphify-out/graph.json)")
        print("  save-result             save a Q&A result to graphify-out/memory/ for graph feedback loop")
        print("    --question Q            the question asked")
        print("    --answer A              the answer to save")
        print("    --type T                query type: query|path_query|explain (default: query)")
        print("    --nodes N1 N2 ...       source node labels cited in the answer")
        print("    --memory-dir DIR        memory directory (default: graphify-out/memory)")
        print("  diff <old.json> <new.json>  compare two graph snapshots and print what changed")
        print("  benchmark [graph.json]  measure token reduction vs naive full-corpus approach")
        for line in _HELP_BLOCKS["navigate"]:
            print(line)
        print("  hook install            install post-commit/post-checkout git hooks (all platforms)")
        print("  hook uninstall          remove git hooks")
        print("  hook status             check if git hooks are installed")
        print("  gemini install          write GEMINI.md section + BeforeTool hook (Gemini CLI)")
        print("  gemini uninstall        remove GEMINI.md section + BeforeTool hook")
        print("  cursor install          write .cursor/rules/graphify.mdc (Cursor)")
        print("  cursor uninstall        remove .cursor/rules/graphify.mdc")
        print("  claude install [--no-import]  write .claude/graphify.md + import line in CLAUDE.md + PreToolUse hook (Claude Code)")
        print("  claude uninstall              remove .claude/graphify.md + import line + PreToolUse hook")
        print("  codex install           write graphify section to AGENTS.md (Codex)")
        print("  codex uninstall         remove graphify section from AGENTS.md")
        print("  opencode install        write graphify section to AGENTS.md + tool.execute.before plugin (OpenCode)")
        print("  opencode uninstall      remove graphify section from AGENTS.md + plugin")
        print("  aider install           write graphify section to AGENTS.md (Aider)")
        print("  aider uninstall         remove graphify section from AGENTS.md")
        print("  copilot install         copy graphify skill to ~/.copilot/skills (GitHub Copilot CLI)")
        print("  copilot uninstall       remove graphify skill from ~/.copilot/skills")
        print("  vscode install          configure VS Code Copilot Chat (skill + .github/copilot-instructions.md)")
        print("  vscode uninstall        remove VS Code Copilot Chat configuration")
        print("  claw install            write graphify section to AGENTS.md (OpenClaw)")
        print("  claw uninstall          remove graphify section from AGENTS.md")
        print("  droid install           write graphify section to AGENTS.md (Factory Droid)")
        print("  droid uninstall        remove graphify section from AGENTS.md")
        print("  trae install            write graphify section to AGENTS.md (Trae)")
        print("  trae uninstall         remove graphify section from AGENTS.md")
        print("  trae-cn install         write graphify section to AGENTS.md (Trae CN)")
        print("  trae-cn uninstall      remove graphify section from AGENTS.md")
        print("  antigravity install     write .agent/rules + .agent/workflows + skill (Google Antigravity)")
        print("  antigravity uninstall   remove .agent/rules, .agent/workflows, and skill")
        print("  hermes install          write skill to ~/.hermes/skills/graphify/ (Hermes)")
        print("  hermes uninstall        remove skill from ~/.hermes/skills/graphify/")
        print("  kiro install            write skill to .kiro/skills/graphify/ + steering file (Kiro IDE/CLI)")
        print("  kiro uninstall          remove skill + steering file")
        print()
        return

    cmd = sys.argv[1]
    if cmd == "_hook":
        # PreToolUse hook handler — delegate to a testable function so
        # the gating logic can be unit-tested without a subprocess.
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except Exception:
            return
        out_payload = _handle_pretool_hook(payload, Path("."))
        if out_payload is None:
            return
        sys.stdout.write(json.dumps(out_payload))
        return
    if cmd == "install":
        # Default to windows platform on Windows, claude elsewhere
        default_platform = "windows" if platform.system() == "Windows" else "claude"
        chosen_platform = default_platform
        args = sys.argv[2:]
        i = 0
        while i < len(args):
            if args[i].startswith("--platform="):
                chosen_platform = args[i].split("=", 1)[1]
                i += 1
            elif args[i] == "--platform" and i + 1 < len(args):
                chosen_platform = args[i + 1]
                i += 2
            else:
                i += 1
        install(platform=chosen_platform)
    elif cmd == "claude":
        subcmd = sys.argv[2] if len(sys.argv) > 2 else ""
        if subcmd == "install":
            add_import = "--no-import" not in sys.argv[3:]
            claude_install(add_import=add_import)
        elif subcmd == "uninstall":
            claude_uninstall()
        else:
            print("Usage: graphify claude [install [--no-import] | uninstall]", file=sys.stderr)
            sys.exit(1)
    elif cmd == "gemini":
        subcmd = sys.argv[2] if len(sys.argv) > 2 else ""
        if subcmd == "install":
            gemini_install()
        elif subcmd == "uninstall":
            gemini_uninstall()
        else:
            print("Usage: graphify gemini [install|uninstall]", file=sys.stderr)
            sys.exit(1)
    elif cmd == "cursor":
        subcmd = sys.argv[2] if len(sys.argv) > 2 else ""
        if subcmd == "install":
            _cursor_install(Path("."))
        elif subcmd == "uninstall":
            _cursor_uninstall(Path("."))
        else:
            print("Usage: graphify cursor [install|uninstall]", file=sys.stderr)
            sys.exit(1)
    elif cmd == "vscode":
        subcmd = sys.argv[2] if len(sys.argv) > 2 else ""
        if subcmd == "install":
            vscode_install()
        elif subcmd == "uninstall":
            vscode_uninstall()
        else:
            print("Usage: graphify vscode [install|uninstall]", file=sys.stderr)
            sys.exit(1)
    elif cmd == "copilot":
        subcmd = sys.argv[2] if len(sys.argv) > 2 else ""
        if subcmd == "install":
            install(platform="copilot")
        elif subcmd == "uninstall":
            skill_dst = Path.home() / _PLATFORM_CONFIG["copilot"]["skill_dst"]
            removed = []
            if skill_dst.exists():
                skill_dst.unlink()
                removed.append(f"skill removed: {skill_dst}")
            version_file = skill_dst.parent / ".graphify_version"
            if version_file.exists():
                version_file.unlink()
            for d in (skill_dst.parent, skill_dst.parent.parent, skill_dst.parent.parent.parent):
                try:
                    d.rmdir()
                except OSError:
                    break
            print("; ".join(removed) if removed else "nothing to remove")
        else:
            print("Usage: graphify copilot [install|uninstall]", file=sys.stderr)
            sys.exit(1)
    elif cmd == "kiro":
        subcmd = sys.argv[2] if len(sys.argv) > 2 else ""
        if subcmd == "install":
            _kiro_install(Path("."))
        elif subcmd == "uninstall":
            _kiro_uninstall(Path("."))
        else:
            print("Usage: graphify kiro [install|uninstall]", file=sys.stderr)
            sys.exit(1)
    elif cmd in ("aider", "codex", "opencode", "claw", "droid", "trae", "trae-cn", "hermes"):
        subcmd = sys.argv[2] if len(sys.argv) > 2 else ""
        if subcmd == "install":
            _agents_install(Path("."), cmd)
        elif subcmd == "uninstall":
            _agents_uninstall(Path("."), platform=cmd)
            if cmd == "codex":
                _uninstall_codex_hook(Path("."))
        else:
            print(f"Usage: graphify {cmd} [install|uninstall]", file=sys.stderr)
            sys.exit(1)
    elif cmd == "antigravity":
        subcmd = sys.argv[2] if len(sys.argv) > 2 else ""
        if subcmd == "install":
            _antigravity_install(Path("."))
        elif subcmd == "uninstall":
            _antigravity_uninstall(Path("."))
        else:
            print("Usage: graphify antigravity [install|uninstall]", file=sys.stderr)
            sys.exit(1)
    elif cmd == "hook":
        from graphify.hooks import install as hook_install, uninstall as hook_uninstall, status as hook_status
        subcmd = sys.argv[2] if len(sys.argv) > 2 else ""
        if subcmd == "install":
            print(hook_install(Path(".")))
        elif subcmd == "uninstall":
            print(hook_uninstall(Path(".")))
        elif subcmd == "status":
            print(hook_status(Path(".")))
        else:
            print("Usage: graphify hook [install|uninstall|status]", file=sys.stderr)
            sys.exit(1)
    elif cmd == "query":
        if any(a in ("-h", "--help") for a in sys.argv[2:]):
            print("Usage: graphify query \"<question>\" [--dfs] [--budget N] [--limit N] [--graph path]")
            print()
            print("Scoring is term-bag-of-words against node labels + source files.")
            print("That works well only when the question contains specific identifiers.")
            print()
            print("Best results: include `@<label>` tokens directly in the question to")
            print("anchor the BFS — `query \"what calls @VectorIndex on insertion?\"`")
            print("starts the traversal at the resolved node and ignores generic terms.")
            print()
            print("If no anchor is given, common stopwords (what/how/why/the/is/are/...)")
            print("are filtered before term-matching, but the result is still a coarse")
            print("sweep. For deterministic answers, prefer `graphify navigate \"@X\" out`.")
            return
        if len(sys.argv) < 3:
            print("Usage: graphify query \"<question>\" [--dfs] [--budget N] [--limit N] [--graph path]", file=sys.stderr)
            sys.exit(1)
        from graphify.serve import _score_nodes, _bfs, _dfs, _subgraph_to_text
        from graphify.security import sanitize_label
        from networkx.readwrite import json_graph
        question = sys.argv[2]
        use_dfs = "--dfs" in sys.argv
        budget = 2000
        node_limit: int | None = None
        graph_path = "graphify-out/graph.json"
        args = sys.argv[3:]
        i = 0
        while i < len(args):
            if args[i] == "--budget" and i + 1 < len(args):
                try:
                    budget = int(args[i + 1])
                except ValueError:
                    print(f"error: --budget must be an integer", file=sys.stderr)
                    sys.exit(1)
                i += 2
            elif args[i].startswith("--budget="):
                try:
                    budget = int(args[i].split("=", 1)[1])
                except ValueError:
                    print(f"error: --budget must be an integer", file=sys.stderr)
                    sys.exit(1)
                i += 1
            elif args[i] == "--limit" and i + 1 < len(args):
                try:
                    node_limit = int(args[i + 1])
                except ValueError:
                    print(f"error: --limit must be an integer", file=sys.stderr)
                    sys.exit(1)
                i += 2
            elif args[i].startswith("--limit="):
                try:
                    node_limit = int(args[i].split("=", 1)[1])
                except ValueError:
                    print(f"error: --limit must be an integer", file=sys.stderr)
                    sys.exit(1)
                i += 1
            elif args[i] == "--graph" and i + 1 < len(args):
                graph_path = args[i + 1]; i += 2
            else:
                i += 1
        gp = Path(graph_path).resolve()
        if not gp.exists():
            print(f"error: graph file not found: {gp}", file=sys.stderr)
            sys.exit(1)
        if not gp.suffix == ".json":
            print(f"error: graph file must be a .json file", file=sys.stderr)
            sys.exit(1)
        try:
            import json as _json
            import networkx as _nx
            from graphify.build import build_from_json
            _raw = _json.loads(gp.read_text(encoding="utf-8"))
            # Lap-21: build_from_json restores edge direction from
            # _src/_tgt — node_link_graph yields an undirected Graph
            # (graph.json carries `directed: False`) and resolve_focus's
            # lap-20c dotted Class.method walk needs G.successors.
            G = build_from_json(_raw, directed=True)
        except Exception as exc:
            print(f"error: could not load graph: {exc}", file=sys.stderr)
            sys.exit(1)
        # Anchor extraction: `@<label>` tokens in the question are
        # explicit traversal starts (resolved via the same matcher
        # navigate uses). Without anchors `query` is a bag-of-words
        # term scan that returns generic top-N hits (`.len()`, `.new()`,
        # `.push()`); with anchors it's a deterministic BFS from a
        # concrete node, which is what most users actually want.
        from graphify.resolve import resolve_focus, label_index
        anchors_raw = [t for t in question.split() if t.startswith("@") and len(t) > 1]
        anchor_starts: list[str] = []
        if anchors_raw:
            idx = label_index(G)
            for raw in anchors_raw:
                chosen, candidates, _mt, _alts = resolve_focus(G, idx, raw)
                if chosen:
                    anchor_starts.append(chosen)
                elif candidates:
                    print(f"warning: anchor `{raw}` is ambiguous "
                          f"({len(candidates)} matches); ignoring. "
                          f"qualify with the source path: e.g. `@tools/foo.py/{raw[1:]}` "
                          f"(extension optional, leading `_` works either way).",
                          file=sys.stderr)

        # Stopword filter for the term-scoring fallback. The default
        # English question shape (`what is X?`, `how does Y work?`)
        # produced low-signal scores: every node with `is` or `does`
        # in any field tied. Drop the obvious filler so the remaining
        # nouns/identifiers actually drive ranking.
        STOPWORDS = {
            "the", "and", "for", "are", "but", "not", "you", "all",
            "any", "can", "had", "has", "have", "what", "when", "where",
            "which", "who", "why", "how", "does", "did", "this", "that",
            "with", "from", "into", "out", "about", "show", "list", "find",
            "tell", "give", "look",
        }
        terms = [t.lower() for t in question.split()
                 if len(t) > 2 and t.lower() not in STOPWORDS
                 and not t.startswith("@")]

        if anchor_starts:
            start = anchor_starts[:5]
        else:
            scored = _score_nodes(G, terms)
            if not scored:
                msg = "No matching nodes found."
                if not terms:
                    msg += (" Question contained only stopwords — "
                            "try `query \"what calls @<label>?\"` "
                            "or use `navigate \"@<label>\" out` instead.")
                else:
                    msg += (" Add an `@<label>` anchor to the question "
                            "for deterministic traversal.")
                print(msg)
                sys.exit(0)
            start = [nid for _, nid in scored[:5]]
            # Lap-8 bridge: surface the top label hits so the agent can
            # rerun as `navigate "@<label>"` rather than reading a flat
            # term-scan dump. The reporter said the fallback warning was
            # correct but didn't redirect — naming the candidates closes
            # that gap. We sample from `scored[:5]` (the BFS start set)
            # because those are the highest-relevance term matches; their
            # labels are what the agent likely meant to focus.
            top_labels = []
            for _, nid in scored[:5]:
                lbl = G.nodes[nid].get("label", nid)
                if lbl and lbl not in top_labels:
                    top_labels.append(lbl)
            if top_labels:
                hint = ", ".join(f"@{l}" for l in top_labels[:3])
                print(f"_(no `@<label>` anchor — falling back to term scan; "
                      f"did you mean to focus one of: {hint}? "
                      f"`navigate \"@<label>\"` is more deterministic.)_",
                      file=sys.stderr)
            else:
                print(f"_(no `@<label>` anchor — falling back to term scan; "
                      f"prefer `navigate \"@<label>\"` for focused traversal)_",
                      file=sys.stderr)
        nodes, edges = (_dfs if use_dfs else _bfs)(G, start, depth=2)
        # Lap-12: in the no-anchor fallback, the term-scorer's top-N (which
        # also produces the "did you mean" hint) must appear first in the
        # rendered subgraph. Without `priority_nodes`, BFS-expansion's
        # degree-sort surfaces hub neighbours (`types.ts`, generic helpers)
        # above the actual term-scored hit — the agent reads the suggestion
        # engine and the result list as contradicting each other. Threading
        # `start` through aligns them.
        priority = start if not anchor_starts else None
        print(_subgraph_to_text(G, nodes, edges, token_budget=budget,
                                priority_nodes=priority,
                                node_limit=node_limit))
    elif cmd == "save-result":
        # graphify save-result --question Q --answer A --type T [--nodes N1 N2 ...]
        import argparse as _ap
        p = _ap.ArgumentParser(prog="graphify save-result")
        p.add_argument("--question", required=True)
        p.add_argument("--answer", required=True)
        p.add_argument("--type", dest="query_type", default="query")
        p.add_argument("--nodes", nargs="*", default=[])
        p.add_argument("--memory-dir", default="graphify-out/memory")
        opts = p.parse_args(sys.argv[2:])
        from graphify.ingest import save_query_result as _sqr
        out = _sqr(
            question=opts.question,
            answer=opts.answer,
            memory_dir=Path(opts.memory_dir),
            query_type=opts.query_type,
            source_nodes=opts.nodes or None,
        )
        print(f"Saved to {out}")
    elif cmd == "path":
        if any(a in ("-h", "--help") for a in sys.argv[2:]):
            _print_subcmd_help("path")
            return
        if len(sys.argv) < 4:
            print("Usage: graphify path \"<source>\" \"<target>\" [--graph path] [--include-inferred] [--edges all|reach|calls]", file=sys.stderr)
            sys.exit(1)
        from graphify.resolve import resolve_focus, label_index
        from graphify.analyze import _is_file_node
        from networkx.readwrite import json_graph
        import networkx as _nx
        source_label = sys.argv[2]
        target_label = sys.argv[3]
        graph_path = "graphify-out/graph.json"
        include_inferred = False  # default: AST ground truth only
        # `reach` (default) excludes type_ref / rationale_for from
        # symbol-to-symbol paths so reachability means call/use chains,
        # not "mentioned in a parameter type signature". `all` opts back in
        # for users who want to see the literal connectedness.
        edge_mode = "reach"
        args = sys.argv[4:]
        for i, a in enumerate(args):
            if a == "--graph" and i + 1 < len(args):
                graph_path = args[i + 1]
            elif a == "--include-inferred":
                include_inferred = True
            elif a == "--edges" and i + 1 < len(args):
                em = args[i + 1].lower()
                if em not in ("all", "reach", "calls"):
                    print(f"error: --edges must be one of: all, reach, calls (got {em!r})", file=sys.stderr)
                    sys.exit(1)
                edge_mode = em
            elif a.startswith("--edges="):
                em = a.split("=", 1)[1].lower()
                if em not in ("all", "reach", "calls"):
                    print(f"error: --edges must be one of: all, reach, calls (got {em!r})", file=sys.stderr)
                    sys.exit(1)
                edge_mode = em
        gp = Path(graph_path).resolve()
        if not gp.exists():
            print(f"error: graph file not found: {gp}", file=sys.stderr)
            sys.exit(1)
        _raw = json.loads(gp.read_text(encoding="utf-8"))
        # Lap-21: use build_from_json so we get a DiGraph with edge
        # directions restored from `_src`/`_tgt`. Plain
        # `json_graph.node_link_graph` returns an undirected Graph
        # (graph.json carries `directed: False` even though the data is
        # directionally tagged), which broke `resolve_focus`'s lap-20c
        # dotted Class.method walk via `G.successors` — undirected
        # Graphs don't have that method.
        from graphify.build import build_from_json
        G = build_from_json(_raw, directed=True)
        # Filter the graph to EXTRACTED edges by default — INFERRED edges
        # produce string-match shortcuts (path through a docstring fragment
        # rather than a real call) that mislead more than they help.
        if not include_inferred:
            edges_to_drop = [(u, v) for u, v, d in G.edges(data=True)
                             if d.get("confidence") and d["confidence"] != "EXTRACTED"]
            G = G.copy()
            G.remove_edges_from(edges_to_drop)
        # Use navigate's resolver so `path` shares the same `@<label>` /
        # plain-label syntax, NFC/prefix/substring/fuzzy ranking, and
        # public-over-rationale preference. Without this `path` would land
        # on rationale comments first because they tie on substring score.
        idx = label_index(G)
        def _resolve(label: str) -> tuple[str | None, list[str], str]:
            chosen, candidates, match_type, _alts = resolve_focus(G, idx, label)
            return chosen, candidates, match_type
        src_nid, src_cands, src_match = _resolve(source_label)
        tgt_nid, tgt_cands, tgt_match = _resolve(target_label)
        from graphify.resolve import _is_archived_path
        for who, label, nid, cands in (("source", source_label, src_nid, src_cands),
                                        ("target", target_label, tgt_nid, tgt_cands)):
            if nid is None:
                if cands:
                    print(f"{who} '{label}' is ambiguous ({len(cands)} matches). qualify with the source path: e.g. `tools/foo.py/{label}` or `tools/foo/{label}` (extension optional). leading `_` on private symbols works either way. or re-call with one of the node IDs below — IDs bypass label resolution. pick from below:", file=sys.stderr)
                    for c in cands[:5]:
                        attrs = G.nodes[c]
                        src = attrs.get("source_file") or ""
                        loc = attrs.get("source_location") or ""
                        archived = " [archived]" if _is_archived_path(src) else ""
                        suffix = f"  — {src}{':' + loc if loc else ''}{archived}" if src else ""
                        # Lap-10: surface node ID so the agent can re-call
                        # `path <id> <id>` and bypass label resolution. Path
                        # is one-shot (no [N] interactive pick like navigate),
                        # so IDs are the only way to pin a specific candidate
                        # when path-qualification can't disambiguate further.
                        print(f"  - {attrs.get('label', c)}  [id: {c}]{suffix}", file=sys.stderr)
                else:
                    print(f"No node matching '{label}' found.", file=sys.stderr)
                sys.exit(1)
        # Auto-detect: when both endpoints are symbol nodes, drop the
        # file-graph edges (contains/imports) before pathing. Without
        # this, `path "kg/compile-v2.ts/compile" "embeddingGenerate"`
        # returned `compile() → compile-v2.ts → engine-core.ts → ...`
        # — a contains/imports chain dressed as a call path. compile()
        # does not transitively call the target via that route. When
        # either endpoint IS a file node, the user is asking about
        # the file graph, so we keep those edges.
        src_is_file = _is_file_node(G, src_nid)
        tgt_is_file = _is_file_node(G, tgt_nid)
        symbol_to_symbol = not (src_is_file or tgt_is_file)
        FILE_RELS = {"contains", "imports"}
        # Lap-7: `type_ref` and `rationale_for` aren't call-graph reachability.
        # A path through `compile() --type_ref→ DecodeEngine --type_ref→ ...`
        # walks parameter type signatures, which is "literally connected" but
        # not "compile() reaches X by calling/using it". Block by default;
        # `--edges all` opts back in.
        NON_REACH_RELS = {"type_ref", "rationale_for"}
        # Lap-8: `calls` mode for "execution-relevant" reachability — only
        # walks call/method/inheritance edges. The complement of this set is
        # blocked. Reporter case: `path "compile-v2" "embeddingGenerate"`
        # under `reach` routed compile-v2 --imports→ engine-core --contains→
        # f32ToF16Array --calls→ embeddingGenerate, which is graph distance
        # but reads as a call chain. `calls` keeps only call-graph proper.
        CALL_RELS = {"calls", "method", "impl_of", "inherits"}
        if edge_mode == "all":
            blocked: set[str] = set()
        elif edge_mode == "calls":
            # Block everything outside the call-graph proper. Compute lazily
            # from the actual graph so we don't have to enumerate every
            # relation type that might exist.
            all_rels = {d.get("relation") for _, _, d in G.edges(data=True)
                        if d.get("relation")}
            blocked = all_rels - CALL_RELS
        elif symbol_to_symbol:
            blocked = FILE_RELS | NON_REACH_RELS
        else:
            # File involved: contains/imports stay (the user is asking about
            # the file graph), but type-ref/rationale paths are still noise.
            blocked = NON_REACH_RELS
        # Mutate weights to make blocked edges effectively unreachable
        # rather than removing them — keeps the graph object intact for any
        # downstream reuse and lets a fallback try via-file routing if
        # shortest_path fails.
        for u, v, d in G.edges(data=True):
            rel = d.get("relation")
            if rel in blocked:
                d["_path_weight"] = 1000.0
            elif (not symbol_to_symbol) and rel == "contains":
                # Weight `contains` higher than semantic edges so the
                # call/method chain still beats class→file→class ties
                # (the original Lap-2 weighting).
                d["_path_weight"] = 10.0
            else:
                d["_path_weight"] = 1.0
        try:
            path_nodes = _nx.shortest_path(G, src_nid, tgt_nid, weight="_path_weight")
            # If the path traversed any blocked edge (file-graph or
            # non-reachability), shortest_path still found it but at
            # ≥1000 cost per blocked hop. Treat that as "no semantic
            # path" and fall through to the descriptive fallback. Without
            # this, a type_ref-only chain reports as a real path.
            via_blocked = blocked and any(
                G.edges[path_nodes[i], path_nodes[i + 1]].get("relation") in blocked
                for i in range(len(path_nodes) - 1)
            )
            if via_blocked:
                raise _nx.NetworkXNoPath  # treat as no semantic path
        except (_nx.NetworkXNoPath, _nx.NodeNotFound):
            if blocked:
                # Retry with all edges allowed so we can tell the user
                # what (if anything) connects them at all.
                for u, v, d in G.edges(data=True):
                    d["_path_weight"] = (
                        10.0 if d.get("relation") == "contains" else 1.0
                    )
                try:
                    via = _nx.shortest_path(G, src_nid, tgt_nid, weight="_path_weight")
                    rels = [G.edges[via[i], via[i + 1]].get("relation", "")
                            for i in range(len(via) - 1)]
                    blocked_rels = {r for r in rels if r in blocked}
                    if blocked_rels & FILE_RELS:
                        note_kind = "co-location/imports"
                    elif blocked_rels & NON_REACH_RELS:
                        note_kind = "type-ref/rationale (no call/use chain)"
                    else:
                        note_kind = "structural"
                    note = f" (only {note_kind} connects them — pass `--edges all` to see it)"
                    print(f"No semantic path between '{source_label}' and "
                          f"'{target_label}'.{note}")
                    print(f"  via {','.join(sorted(blocked_rels)) or 'mixed'} ({len(via) - 1} hops): "
                          f"{' → '.join(G.nodes[n].get('label', n) for n in via)}")
                    sys.exit(0)
                except (_nx.NetworkXNoPath, _nx.NodeNotFound):
                    pass
            hint = "" if include_inferred else " (try --include-inferred to widen)"
            print(f"No path found between '{source_label}' and '{target_label}'.{hint}")
            sys.exit(0)
        hops = len(path_nodes) - 1
        relations: list[str] = []
        segments = []
        for i in range(len(path_nodes) - 1):
            u, v = path_nodes[i], path_nodes[i + 1]
            edata = G.edges[u, v]
            rel = edata.get("relation", "")
            relations.append(rel)
            conf = edata.get("confidence", "")
            conf_str = f" [{conf}]" if conf else ""
            if i == 0:
                segments.append(G.nodes[u].get("label", u))
            segments.append(f"--{rel}{conf_str}--> {G.nodes[v].get('label', v)}")
        annotation = ""
        if hops >= 2 and all(r == "contains" for r in relations):
            annotation = "  (co-located only — these nodes share a parent file but have no semantic call/use edge between them)"
        elif hops >= 2 and all(r in ("contains", "method") for r in relations):
            annotation = "  (structural-only path: contains/method — no direct call edge between endpoints)"
        # Lap-20 field-report fix: a path that traverses the file-import
        # graph is graph-connected but not a call chain. Default
        # `--edges reach` keeps file-import edges in (some users do want the
        # file-graph view), but the rendered output reads as a multi-hop
        # call path. Cheap hint: tell the agent the path is mostly
        # file-imports and `--edges calls` would constrain to the runtime
        # call graph. NOT a default change — the existing default stays.
        # Lap-20b: loosen from "all imports_from" to "majority imports_from
        # plus optional terminal contains/method", since a real-world all-
        # imports chain typically lands on the target symbol via a final
        # `contains` edge from a file node — that whole shape still has no
        # runtime call meaning.
        import_edges = sum(1 for r in relations if r in ("imports", "imports_from"))
        terminal_structural_only = (
            relations
            and relations[-1] in ("contains", "method")
            and all(r in ("imports", "imports_from") for r in relations[:-1])
        )
        # Trigger the hint only when the path is dominated by file-level
        # imports — at least 2 import edges OR every edge is an import.
        # A short 2-hop `imports + contains` chain (a single import landing
        # on a symbol via its file) is not the failure mode the field
        # report flagged; the dangerous case is a multi-hop import
        # traversal that READS as a call path.
        mostly_imports = hops >= 2 and (
            (import_edges == hops)
            or (import_edges >= 2 and terminal_structural_only)
            or (hops >= 3 and import_edges >= hops - 1)
        )
        if mostly_imports and edge_mode != "calls":
            annotation = ("  hint: try --edges calls — path traverses "
                          "file-level imports, may not represent semantic "
                          "call flow")
        print(f"Shortest path ({hops} hops):\n  " + " ".join(segments))
        if annotation:
            print(annotation)

    elif cmd == "explain":
        if any(a in ("-h", "--help") for a in sys.argv[2:]):
            _print_subcmd_help("explain")
            return
        if len(sys.argv) < 3:
            print("Usage: graphify explain \"<node>\" [--graph path] [--include-inferred] [--limit N]", file=sys.stderr)
            sys.exit(1)
        from graphify.resolve import resolve_focus, label_index
        from networkx.readwrite import json_graph
        label = sys.argv[2]
        graph_path = "graphify-out/graph.json"
        include_inferred = False  # default: AST ground truth only, matching navigate
        limit = 20
        args = sys.argv[3:]
        i = 0
        while i < len(args):
            a = args[i]
            if a == "--graph" and i + 1 < len(args):
                graph_path = args[i + 1]; i += 2
            elif a == "--include-inferred":
                include_inferred = True; i += 1
            elif a == "--limit" and i + 1 < len(args):
                limit = int(args[i + 1]); i += 2
            elif a.startswith("--limit="):
                limit = int(a.split("=", 1)[1]); i += 1
            else:
                i += 1
        gp = Path(graph_path).resolve()
        if not gp.exists():
            print(f"error: graph file not found: {gp}", file=sys.stderr)
            sys.exit(1)
        _raw = json.loads(gp.read_text(encoding="utf-8"))
        from graphify.build import build_from_json
        # Lap-21: build_from_json restores edge direction from _src/_tgt
        # — see the path-cmd comment for why node_link_graph isn't safe.
        G = build_from_json(_raw, directed=True)
        # Use navigate's resolver so explain accepts both `@<label>` and
        # plain `<label>` and shares the prefix/substring/fuzzy ranking.
        idx = label_index(G)
        nid, candidates, match_type, _alts = resolve_focus(G, idx, label)
        if nid is None:
            if candidates:
                from graphify.resolve import _is_archived_path
                print(f"'{label}' is ambiguous ({len(candidates)} matches). qualify with the source path: e.g. `tools/foo.py/{label}` or `tools/foo/{label}` (extension optional). leading `_` on private symbols works either way. or re-call with a node ID below to bypass label resolution. pick from below:", file=sys.stderr)
                for c in candidates[:5]:
                    attrs = G.nodes[c]
                    src = attrs.get("source_file") or ""
                    loc = attrs.get("source_location") or ""
                    archived = " [archived]" if _is_archived_path(src) else ""
                    suffix = f"  — {src}{':' + loc if loc else ''}{archived}" if src else ""
                    print(f"  - {attrs.get('label', c)}  [id: {c}]{suffix}", file=sys.stderr)
            else:
                print(f"No node matching '{label}' found.")
            sys.exit(0 if not candidates else 1)
        d = G.nodes[nid]
        from graphify.analyze import _is_file_node
        is_file = _is_file_node(G, nid)
        print(f"Node: {d.get('label', nid)}")
        print(f"  ID:        {nid}")
        print(f"  Source:    {d.get('source_file', '')} {d.get('source_location', '')}".rstrip())
        print(f"  Type:      {d.get('file_type', '')}")
        print(f"  Community: {d.get('community', '')}")
        print(f"  Degree:    {G.degree(nid)}")
        if is_file:
            # Lap-16 TS field-report friction 5: file-node `explain` was an
            # unfiltered import dump that easily hit thousands of tokens.
            # Replace it with a structural summary: counts by relation,
            # top contains-children by degree, and import targets grouped
            # so the agent gets orientation, not a fan-out wall.
            from collections import Counter
            DG = G if G.is_directed() else None
            outgoing = []
            for nb in G.neighbors(nid):
                e = G.edges[nid, nb]
                if not include_inferred and e.get("confidence") != "EXTRACTED":
                    continue
                outgoing.append((nb, e))
            rel_count = Counter(e.get("relation", "") for _nb, e in outgoing)
            contains_kids = [nb for nb, e in outgoing if e.get("relation") == "contains"]
            import_targets = [nb for nb, e in outgoing
                              if e.get("relation") in ("imports", "imports_from")]
            non_struct = [nb for nb, e in outgoing
                          if e.get("relation") not in ("contains", "method",
                                                       "imports", "imports_from")]
            print(f"\nStructure ({len(outgoing)} extracted edges):")
            print(f"  Relations: " + ", ".join(f"{r}:{c}"
                  for r, c in rel_count.most_common()))
            if contains_kids:
                top = sorted(contains_kids,
                             key=lambda x: -G.degree(x))[:min(limit, 10)]
                print(f"\nTop decls by degree (of {len(contains_kids)}):")
                for nb in top:
                    print(f"  --> {G.nodes[nb].get('label', nb)}  d={G.degree(nb)}")
            if import_targets:
                # Bucket: external module stubs vs internal file targets.
                ext = [nb for nb in import_targets
                       if G.nodes[nb].get("file_type") == "external"]
                internal = [nb for nb in import_targets
                            if G.nodes[nb].get("file_type") != "external"]
                print(f"\nImports: {len(import_targets)} total — "
                      f"{len(ext)} external, {len(internal)} internal")
                # Sample top-fanout external imports so the agent sees what
                # the file pulls from third-party land without listing all.
                if ext:
                    sample = sorted(ext, key=lambda x: -G.degree(x))[:5]
                    print("  external (top-fanout): " +
                          ", ".join(G.nodes[nb].get("label", nb) for nb in sample))
            if non_struct:
                print(f"\nNon-structural edges: {len(non_struct)} "
                      f"(use `navigate @<file> out` to enumerate)")
            return
        neighbors = list(G.neighbors(nid))
        # Filter by confidence (matching navigate's default) and sort
        # EXTRACTED-first so the trustworthy edges aren't buried under
        # bulk-tagged INFERRED noise.
        def _edge_sort_key(nb):
            e = G.edges[nid, nb]
            ext = 0 if e.get("confidence") == "EXTRACTED" else 1
            return (ext, -G.degree(nb))
        neighbors_filtered = [
            nb for nb in neighbors
            if include_inferred or G.edges[nid, nb].get("confidence") == "EXTRACTED"
        ]
        dropped = len(neighbors) - len(neighbors_filtered)
        if neighbors_filtered:
            header = f"\nConnections ({len(neighbors_filtered)})"
            if dropped > 0:
                header += f" — {dropped} INFERRED hidden, --include-inferred to show"
            print(header + ":")
            sorted_nbrs = sorted(neighbors_filtered, key=_edge_sort_key)
            # Lap-16 TS field-report friction 4: explain printed 20 identical
            # `stagedDecode() [type_ref] [EXTRACTED]` lines. Group adjacent
            # rows that share (label, relation, confidence) into a single
            # collapsed entry once the run hits the threshold. Mirrors
            # navigate's listing-collapse but operates on edge-tuples
            # instead of node-summaries because explain renders edges, not
            # nodes.
            from graphify.navigate import _DUPE_COLLAPSE_THRESHOLD
            def _row(nb):
                e = G.edges[nid, nb]
                return (G.nodes[nb].get("label", nb),
                        e.get("relation", ""), e.get("confidence", ""))
            collapsed_rows: list[tuple[str, str, str, int]] = []
            i_idx = 0
            while i_idx < len(sorted_nbrs):
                key_row = _row(sorted_nbrs[i_idx])
                j_idx = i_idx + 1
                while j_idx < len(sorted_nbrs) and _row(sorted_nbrs[j_idx]) == key_row:
                    j_idx += 1
                count = j_idx - i_idx
                collapsed_rows.append((*key_row, count))
                i_idx = j_idx
            prev_extracted: bool | None = None
            boundary_inserted = False
            shown = 0  # rendered lines so far
            covered = 0  # underlying edges covered by rendered output
            for label_, rel, conf, count in collapsed_rows:
                if shown >= limit:
                    break
                is_ext = conf == "EXTRACTED"
                if (not boundary_inserted and prev_extracted is True and not is_ext):
                    print("  ── inferred below ──")
                    boundary_inserted = True
                prev_extracted = is_ext
                # explain renders edges, not nodes — duplicates here are
                # always graph artifacts (two edges with identical
                # label/rel/conf). The listing-collapse threshold (≥5) makes
                # sense for navigate's pivot listings where you want to see a
                # few same-label rows; in explain even a count of 2 is just
                # noise. Collapse anything ≥2 as `×N`.
                if count >= 2:
                    print(f"  --> {label_} [{rel}] [{conf}]  ×{count}")
                    shown += 1
                    covered += count
                else:
                    if shown < limit:
                        print(f"  --> {label_} [{rel}] [{conf}]")
                        shown += 1
                        covered += 1
            if covered < len(neighbors_filtered):
                print(f"  ... and {len(neighbors_filtered) - covered} more")
        elif dropped > 0:
            print(f"\nNo EXTRACTED edges. {dropped} INFERRED edges hidden (use --include-inferred).")

    elif cmd == "summarize":
        # Lap-21 (Gemini #3): synthesize a one-call architectural overview
        # from data the graph already carries — community hubs, entry
        # points, edge composition, language mix, freshness. The agent
        # lands with a primer instead of having to run shape on a guess
        # first.
        # Lap-23 (meta-harness task_004/005 cluster-B fix): when a target
        # symbol is passed (`summarize @<Class>`), switch modes — fuse
        # signature + method list + cross-file callers + inheritance into
        # one call. Targets the bimodal failure mode where agents fall
        # back to `read_file` on huge files because no graphify verb
        # gave them class-level context. Same blast-pattern logic: a
        # common task (class summary) collapsed from N calls to 1.
        if any(a in ("-h", "--help") for a in sys.argv[2:]):
            _print_subcmd_help("summarize")
            return
        from graphify.navigate import DEFAULT_GRAPH_PATH, load_graph, _read_body_full
        from collections import Counter as _Counter
        graph_path = DEFAULT_GRAPH_PATH
        target: str | None = None
        method_limit = 30
        caller_limit = 8
        md = False
        # Lap-26 field-report fix: `summarize @SymplecticGeometry` matched
        # 4 nodes — 1 main + 3 in `results/symplectic_v3/` (ShinkaEvolve
        # generation outputs). Disambig is exactly when archived noise
        # hurts. Match `search`'s default of "no archived" here; agents
        # who actually want the archive inspect with `--archived-only`
        # or `--all-archived`.
        archived_mode = "no"
        args = sys.argv[2:]
        i = 0
        while i < len(args):
            a = args[i]
            if a == "--graph" and i + 1 < len(args):
                graph_path = args[i + 1]; i += 2
            elif a.startswith("--graph="):
                graph_path = a.split("=", 1)[1]; i += 1
            elif a == "--methods" and i + 1 < len(args):
                method_limit = max(0, int(args[i + 1])); i += 2
            elif a.startswith("--methods="):
                method_limit = max(0, int(a.split("=", 1)[1])); i += 1
            elif a == "--callers" and i + 1 < len(args):
                caller_limit = max(0, int(args[i + 1])); i += 2
            elif a.startswith("--callers="):
                caller_limit = max(0, int(a.split("=", 1)[1])); i += 1
            elif a == "--md":
                md = True; i += 1
            elif a == "--no-archived":
                archived_mode = "no"; i += 1
            elif a == "--archived-only":
                archived_mode = "only"; i += 1
            elif a == "--all-archived":
                archived_mode = "all"; i += 1
            elif target is None and not a.startswith("--"):
                target = a; i += 1
            else:
                i += 1
        gp = Path(graph_path)
        if not gp.exists():
            print(f"error: graph not found at {gp}. run `graphify update <path>` first.",
                  file=sys.stderr)
            sys.exit(1)
        G, communities = load_graph(gp)

        # Class-summary mode: target arg → fused class context. Mirrors
        # peek/blast disambig output so the caller can re-issue with a
        # path-qualified target.
        if target:
            from graphify.resolve import label_index, resolve_focus
            from graphify.navigate import _filter_archived_ids
            idx = label_index(G)
            chosen, candidates, match_type, _alts = resolve_focus(G, idx, target)
            archived_hidden = 0
            if not chosen and candidates:
                # Lap-26: apply --no-archived (default) before disambig so the
                # common 1-main / N-archived case auto-resolves instead of
                # forcing the agent to path-qualify away ShinkaEvolve /
                # legacy generations.
                filtered, archived_hidden = _filter_archived_ids(
                    G, candidates, archived_mode)
                if len(filtered) == 1:
                    chosen = filtered[0]
                    if archived_hidden:
                        print(f"# auto-picked unique non-archived match "
                              f"(+{archived_hidden} archived hidden, "
                              f"unhide with --all-archived)")
                else:
                    candidates = filtered
            if not chosen:
                if candidates:
                    print(f"ambiguous `{target}` ({len(candidates)} matches). "
                          f"qualify with @<dir>/<file>/<symbol>:",
                          file=sys.stderr)
                    for nid in candidates[:8]:
                        a_attrs = G.nodes[nid]
                        sf_a = a_attrs.get("source_file", "?")
                        loc_a = a_attrs.get("source_location", "")
                        label_a = a_attrs.get("label", nid)
                        print(f"  {label_a}  {sf_a}{':' + loc_a[1:] if loc_a.startswith('L') else ''}",
                              file=sys.stderr)
                    if len(candidates) > 8:
                        print(f"  +{len(candidates) - 8} more", file=sys.stderr)
                    if archived_hidden:
                        print(f"  +{archived_hidden} archived hidden "
                              f"(unhide with --all-archived)",
                              file=sys.stderr)
                    sys.exit(1)
                print(f"no node matches `{target}`.", file=sys.stderr)
                sys.exit(1)
            if match_type and match_type != "exact":
                chosen_label = G.nodes[chosen].get("label", chosen)
                print(f"# matched `{target}` → {chosen_label} ({match_type})")
            nattrs = G.nodes[chosen]
            node_kind = nattrs.get("node_kind") or ""
            if node_kind not in ("class", "interface"):
                label = nattrs.get("label", chosen)
                # Lap-23: name the right verb for the target's actual shape.
                # An agent who typed `summarize @foo` for a function should
                # be redirected to peek/blast instead of getting a generic
                # error.
                # Lap-24 (post-A/B follow-up): file targets fall THROUGH
                # to `shape`. Empirical from session-benchmark t0
                # transcript: agent ran `graphify summarize <file>`,
                # got the redirect message, then re-ran `graphify shape
                # <file>` — one wasted call. The agent intent is clear
                # ("summarize this file"); honor it by dispatching into
                # shape rather than emitting an error. Header note tells
                # the agent the redirect happened so future calls go
                # straight to shape.
                if node_kind == "file":
                    from graphify.navigate import (
                        shape_file, _render_shape_text,
                    )
                    from graphify.analyze import _is_file_node
                    if _is_file_node(G, chosen):
                        print(f"# `summarize @{label}` is a file target — "
                              f"redirecting to `graphify shape`.")
                        data = shape_file(G, chosen)
                        print(_render_shape_text(data))
                        return
                if node_kind in ("function", "method", "impl_method",
                                 "iface_method"):
                    hint = "peek"
                else:
                    hint = "peek or blast"
                print(f"@{label} is a {node_kind or 'symbol'}, not a class. "
                      f"Try `graphify {hint} \"@{label}\"` instead.",
                      file=sys.stderr)
                sys.exit(1)

            label = nattrs.get("label", chosen)
            sf = nattrs.get("source_file") or "?"
            loc = nattrs.get("source_location") or ""
            loc_short = loc[1:] if isinstance(loc, str) and loc.startswith("L") else ""

            # Walk the class's methods. Same pattern as peek-class: succ
            # edges with relation method/contains, function-shaped nodes,
            # sorted by start line so output reads top-to-bottom.
            method_ids: list[str] = []
            for v in G.successors(chosen):
                rel = G.edges[chosen, v].get("relation") or ""
                if rel not in ("method", "contains"):
                    continue
                v_kind = G.nodes[v].get("node_kind") or ""
                v_label = G.nodes[v].get("label", "")
                if v_kind in ("class", "interface", "type_alias"):
                    continue
                if not (v_label.endswith("()") or v_kind in
                        ("method", "impl_method", "iface_method", "function")):
                    continue
                method_ids.append(v)

            def _method_rank(nid: str) -> tuple[int, int]:
                """Lap-27 long-running-Claude field report: summarize
                used to order methods by source line, putting
                `__init__`/`constructor` at the top while the
                actually-loaded API surface (get/post/render) sat
                below the fold or got truncated past `--methods`.
                The agent's question — "what should I look at
                first?" — is what `summarize` exists to answer;
                degree is the surface signal. Source line is the
                stable tiebreaker so equal-degree pairs don't
                shuffle between runs."""
                vloc = G.nodes[nid].get("source_location") or ""
                line = 1 << 30
                if vloc.startswith("L"):
                    try:
                        line = int(vloc[1:].split("-", 1)[0].split(":", 1)[0])
                    except ValueError:
                        pass
                return (-G.degree(nid), line)
            method_ids.sort(key=_method_rank)

            # Cross-file callers: predecessors via call edges where the
            # source file differs. This matches `blast`'s callers semantics
            # but we don't pull out edges (callees) — for a class node
            # callees are the methods themselves, already listed above.
            from graphify.navigate import _STRUCTURAL
            caller_rows: list[tuple[str, str, str, int]] = []
            seen_callers: set[str] = set()
            for u in G.predecessors(chosen):
                rel = G.edges[u, chosen].get("relation") or ""
                if rel in _STRUCTURAL:
                    continue
                u_attrs = G.nodes[u]
                u_sf = u_attrs.get("source_file") or ""
                if not u_sf or u_sf == sf:
                    continue
                u_label = u_attrs.get("label", u)
                if u_label in seen_callers:
                    continue
                seen_callers.add(u_label)
                u_loc = u_attrs.get("source_location") or ""
                u_line = 0
                if u_loc.startswith("L"):
                    try:
                        u_line = int(u_loc[1:].split("-", 1)[0].split(":", 1)[0])
                    except ValueError:
                        pass
                caller_rows.append((u_label, u_sf, u_loc, u_line))
            caller_rows.sort(key=lambda r: r[1])

            # Inheritance: succ edges with relation==inherits → parent(s).
            # pred edges with relation==inherits → children. Siblings = other
            # children of the same parent (excluding self), capped.
            parent_ids = [v for v in G.successors(chosen)
                          if G.edges[chosen, v].get("relation") == "inherits"]
            child_ids = [u for u in G.predecessors(chosen)
                         if G.edges[u, chosen].get("relation") == "inherits"]
            sibling_labels: list[str] = []
            for pid in parent_ids:
                for u in G.predecessors(pid):
                    if u == chosen:
                        continue
                    if G.edges[u, pid].get("relation") != "inherits":
                        continue
                    s_label = G.nodes[u].get("label", u)
                    if s_label not in sibling_labels:
                        sibling_labels.append(s_label)

            # Header line: dense one-shot summary stat row.
            kind_word = "class" if node_kind == "class" else "interface"
            parent_str = ""
            if parent_ids:
                p_labels = ", ".join(G.nodes[p].get("label", p) for p in parent_ids[:3])
                parent_str = f" · inherits {p_labels}"
            child_str = ""
            if child_ids:
                child_str = f" · {len(child_ids)} subclass{'es' if len(child_ids) != 1 else ''}"
            print(f"summarize {kind_word} @{label}  {sf}{':' + loc_short if loc_short else ''}  "
                  f"({len(method_ids)} method{'s' if len(method_ids) != 1 else ''} · "
                  f"{len(caller_rows)} caller{'s' if len(caller_rows) != 1 else ''}"
                  f"{parent_str}{child_str})")
            print()

            # Class signature line — first line of class body.
            class_head, _ln, _trunc = _read_body_full(sf, loc, max_lines=1, flat=True)
            if class_head:
                print("## Signature")
                print(f"  {class_head[0].rstrip()}")
                print()

            # Methods: signature line only (no body — that's what peek
            # is for). Each row: label + line range. Sorted by degree
            # desc (most-loaded API surface first), source line asc as
            # tiebreaker. Lap-27 field-report fix.
            print(f"## Methods ({len(method_ids)})")
            if method_ids:
                shown = method_ids[:method_limit] if method_limit > 0 else method_ids
                for mid in shown:
                    m = G.nodes[mid]
                    m_label = m.get("label", mid) or mid
                    m_loc = m.get("source_location") or ""
                    m_loc_short = m_loc[1:] if m_loc.startswith("L") else ""
                    disp = m_label.lstrip(".") if m_label.startswith(".") else m_label
                    if md and m_loc_short:
                        m_sf = m.get("source_file") or sf
                        print(f"  - [{disp}]({m_sf}:{m_loc_short})")
                    elif m_loc_short:
                        print(f"  - {disp}  L{m_loc_short}")
                    else:
                        print(f"  - {disp}")
                more = len(method_ids) - len(shown)
                if more > 0:
                    print(f"  +{more} more — `peek @{label}` for method bodies, "
                          f"or `summarize @{label} --methods 0` for the full list")
            else:
                print("  (none)")
            print()

            # Used by: cross-file callers, capped. Pre-answers "where is
            # this class instantiated?"
            print(f"## Used by ({len(caller_rows)} cross-file caller{'s' if len(caller_rows) != 1 else ''})")
            if caller_rows:
                shown_callers = caller_rows[:caller_limit] if caller_limit > 0 else caller_rows
                for u_label, u_sf, u_loc, _u_line in shown_callers:
                    u_loc_short = u_loc[1:] if u_loc.startswith("L") else ""
                    if md and u_loc_short:
                        print(f"  - [{u_label}]({u_sf}:{u_loc_short})")
                    elif u_loc_short:
                        print(f"  - {u_label}  {u_sf}:{u_loc_short}")
                    else:
                        print(f"  - {u_label}  {u_sf}")
                more_c = len(caller_rows) - len(shown_callers)
                if more_c > 0:
                    print(f"  +{more_c} more — `blast @{label}` for the full list")
            else:
                print("  (none — use `--include-inferred` if dynamic dispatch is suspected)")

            # Inheritance: parent, children count, siblings (capped).
            # Skip the section entirely if there's nothing to say — agents
            # don't need a "no inheritance" line for non-OO trees.
            if parent_ids or child_ids or sibling_labels:
                print()
                print("## Inheritance")
                if parent_ids:
                    for pid in parent_ids:
                        p_attrs = G.nodes[pid]
                        p_label = p_attrs.get("label", pid)
                        p_sf = p_attrs.get("source_file") or "?"
                        p_loc = p_attrs.get("source_location") or ""
                        p_loc_short = p_loc[1:] if p_loc.startswith("L") else ""
                        if p_loc_short:
                            print(f"  parent:   {p_label}  {p_sf}:{p_loc_short}")
                        else:
                            print(f"  parent:   {p_label}  {p_sf}")
                if sibling_labels:
                    sib_show = sibling_labels[:8]
                    sib_str = ", ".join(sib_show)
                    sib_more = len(sibling_labels) - len(sib_show)
                    suffix = f" (+{sib_more} more)" if sib_more > 0 else ""
                    print(f"  siblings: {sib_str}{suffix}")
                if child_ids:
                    child_labels = [G.nodes[c].get("label", c) for c in child_ids[:8]]
                    cstr = ", ".join(child_labels)
                    cmore = len(child_ids) - len(child_labels)
                    suffix = f" (+{cmore} more)" if cmore > 0 else ""
                    print(f"  children: {cstr}{suffix}")
            # Lap-27 followup: stamp the class's source_file into recent-
            # paths so a follow-up Read of the class's file is a quiet
            # passthrough on the PreToolUse hook.
            sf_chosen = G.nodes[chosen].get("source_file")
            if sf_chosen:
                _stamp_recent_files(gp, [sf_chosen])
            return

        # Top-line stats.
        n_nodes = G.number_of_nodes()
        n_edges = G.number_of_edges()
        n_communities = len(communities)
        unique_files = {a.get("source_file") for _, a in G.nodes(data=True)
                        if a.get("source_file")}
        n_files = len(unique_files)

        # Top communities by member count, label = hub.
        comm_labels = G.graph.get("community_labels") or {}
        comm_sizes = sorted(communities.items(),
                             key=lambda kv: -len(kv[1]))[:5]
        # Cross-file entry points: top fns by non-structural in-edges
        # from a different source_file. Skip method-shape labels
        # (`.foo()`) — those are member calls (`.get`, `.append`,
        # `.set`, `.items`) that swamp real entry-point ranking; the
        # entry point of a class is the class itself, not its methods.
        # Lap-24 (no-arg redesign): also carry source_location so the
        # entry-point line can render `compare() ×104  src/foo.py:42`
        # — saves a follow-up `locate`/`navigate` to find where the
        # function lives.
        from graphify.navigate import _STRUCTURAL
        # Lap-24 follow-up: count fns + classes per source_file so we
        # can flag entry points whose source_file looks "data-only"
        # (no callable surface). Empirical case: zero-tvm corpus has
        # `report()` ×142 attributed to windowed-stacked.ts:L39 — but
        # that line is `const TARGET_LAYER = 27`, and 144 other files
        # carry their own `report()` declarations. The graph resolution
        # layer phantom-merges all 142 cross-file calls into one node
        # at the wrong location. Without filtering, the "Suggested
        # next" footer pointed at a config file with no callable
        # surface. The structural-count check catches this: if the
        # entry point's source_file has 0 fns + 0 classes, the symbol
        # isn't really declared there — phantom merge — and pointing
        # the agent at it wastes a call.
        file_struct_count: dict[str, int] = {}
        for _nid, _attrs in G.nodes(data=True):
            _kind = _attrs.get("node_kind") or ""
            _sf = _attrs.get("source_file") or ""
            if not _sf:
                continue
            if _kind in ("function", "method", "impl_method", "iface_method",
                         "class", "interface", "type_alias"):
                file_struct_count[_sf] = file_struct_count.get(_sf, 0) + 1
        # Also count phantom-likely entries (label has many same-named
        # variants) so a hint can name the dispatch ambiguity.
        from collections import Counter as _LabelCounter
        label_variant_count: _LabelCounter = _LabelCounter()
        for _nid, _attrs in G.nodes(data=True):
            _lab = _attrs.get("label", "")
            _kind = _attrs.get("node_kind") or ""
            if (isinstance(_lab, str) and _lab.endswith("()")
                    and not _lab.startswith(".")
                    and _kind in ("function", "method", "impl_method", "iface_method")):
                label_variant_count[_lab] += 1
        entry_pts: list[tuple[str, int, str, str, int]] = []
        # Lap-27 #2: count vendored/generated entries we filter out so the
        # listing can show `+N vendored hidden, +M generated hidden` per
        # the omission-counts rule. Agents who *want* the library API
        # surface still see it via `--include-vendored` (when shipped).
        vendored_dropped = 0
        generated_dropped = 0
        for nid, attrs in G.nodes(data=True):
            label = attrs.get("label", "")
            if not (isinstance(label, str) and label.endswith("()")):
                continue
            if label.startswith("."):
                continue
            # Require an actual source location — primitives bound to
            # globals (`str`, `dict`) leak in as nodes without source.
            if not attrs.get("source_file"):
                continue
            sf = attrs.get("source_file") or ""
            loc = attrs.get("source_location") or ""
            ext_in = 0
            for u in G.predecessors(nid):
                ufile = G.nodes[u].get("source_file") or ""
                if not ufile or ufile == sf:
                    continue
                if G.edges[u, nid].get("relation") in _STRUCTURAL:
                    continue
                ext_in += 1
            if ext_in > 0:
                # Filter vendored/generated so the listing surfaces the
                # user's API surface, not e.g. lodash or .pb.go boilerplate.
                vc = attrs.get("vendor_class") or "first_party"
                if vc == "vendored":
                    vendored_dropped += 1
                    continue
                if vc == "generated":
                    generated_dropped += 1
                    continue
                variants = label_variant_count.get(label, 1)
                entry_pts.append((label, ext_in, sf, loc, variants))
        entry_pts.sort(key=lambda t: (-t[1], t[0]))
        top_entries = entry_pts[:5]
        # Edge composition.
        rel_counts: _Counter = _Counter()
        for _, _, d in G.edges(data=True):
            rel_counts[d.get("relation") or "<unset>"] += 1
        # Language mix by extension.
        ext_counts: _Counter = _Counter()
        for sf in unique_files:
            if not sf:
                continue
            from pathlib import Path as _Path
            ext = _Path(sf).suffix.lower() or "<no-ext>"
            ext_counts[ext] += 1

        print(f"  graphify summarize: {gp}")
        print(f"  {n_nodes} nodes · {n_edges} edges · {n_communities} communities · "
              f"{n_files} files")
        if comm_sizes:
            print()
            print("  Top communities (by hub):")
            for cid, members in comm_sizes:
                hub_label = comm_labels.get(cid, "?")
                print(f"    c{cid:<3} {hub_label:<32} {len(members)} members")
        if top_entries:
            print()
            print("  Entry points (cross-file callers):")
            phantom_labels: list[str] = []
            for lab, n, sf, loc, variants in top_entries:
                # Render `compare() ×104  src/foo.py:42`. Extract the
                # start line from the L<a>-<b> source_location format;
                # fall back to the bare filename when source_location
                # is missing (graph from older extractor).
                line = ""
                if isinstance(loc, str) and loc.startswith("L"):
                    try:
                        line = loc[1:].split("-", 1)[0].split(":", 1)[0]
                    except ValueError:
                        line = ""
                where = f"  {sf}{':' + line if line else ''}" if sf else ""
                # Lap-24 follow-up: stamp `(N variants)` when many same-
                # named symbols exist (likely dispatch / phantom-merge
                # ambiguity). Threshold at >=10 to avoid annotating
                # every overloaded helper (2-3 variants is common in
                # large TS/Python codebases — only the extreme cases
                # signal phantom-merge). Flag `(no callable surface)`
                # when the symbol's source_file has no fns/classes —
                # the call count is real but the location points at
                # the wrong place (resolution funneled cross-file
                # calls into a same-named node in a data file).
                annot = ""
                if variants and variants >= 10:
                    annot += f" ({variants} variants — likely phantom)"
                    phantom_labels.append(lab)
                struct = file_struct_count.get(sf, 0)
                if struct == 0:
                    annot += " (no callable surface in source_file)"
                print(f"    {lab:<36} ×{n}{where}{annot}")
            # Lap-26 field-report fix: phantom heuristic was opaque ("how
            # do I investigate?"). Name the verbs that surface the actual
            # variants (disambig listing). One line, fires only when at
            # least one phantom row was emitted.
            if phantom_labels:
                example = phantom_labels[0]
                print(f"  drill phantom variants: "
                      f"`graphify navigate \"@{example}\"` (disambig list) "
                      f"or `graphify locate \"{example.rstrip('()')}\"` "
                      f"(file:line per match)")
            # Lap-27 #2: surface vendored/generated drops per the
            # omission-counts rule. Naming the flag is held until those
            # flags ship — for now the line is a transparency signal.
            if vendored_dropped or generated_dropped:
                bits = []
                if vendored_dropped:
                    bits.append(f"+{vendored_dropped} vendored hidden")
                if generated_dropped:
                    bits.append(f"+{generated_dropped} generated hidden")
                print("    " + ", ".join(bits))
        if rel_counts:
            print()
            total_rel = sum(rel_counts.values())
            top_rels = rel_counts.most_common(5)
            mix = " · ".join(f"{r} {c*100//total_rel}%" for r, c in top_rels)
            print(f"  Edge mix: {mix}")
        if ext_counts:
            top_exts = ext_counts.most_common(5)
            mix = " · ".join(f"{ext} ({c})" for ext, c in top_exts)
            print(f"  Languages: {mix}")

        # Lap-27 follow-up (post-marquee dogfood): advertise the markdown
        # surface explicitly. The Languages line shows `.md (N)` but the
        # agent doesn't know graphify resolves backtick refs in those
        # files into `references` edges — they ASK for "where is X
        # mentioned" via grep instead of reaching for `wu @X`. One line
        # that names the count split + the verb closes the gap.
        ext_refs = sum(1 for _, _, d in G.edges(data=True)
                       if d.get("relation") == "references"
                       and d.get("confidence") == "EXTRACTED")
        inf_refs = sum(1 for _, _, d in G.edges(data=True)
                       if d.get("relation") == "references"
                       and d.get("confidence") == "INFERRED")
        if ext_refs or inf_refs:
            bits = []
            if ext_refs:
                bits.append(f"{ext_refs} EXTRACTED")
            if inf_refs:
                bits.append(f"{inf_refs} INFERRED")
            print(f"  Doc refs: {' + '.join(bits)} "
                  f"(`wu @<symbol>` surfaces .md/.mdx mentions of code)")

        # Lap-27 follow-up: surface CLI-script counts so agents know to
        # reach for `graphify scripts` instead of `grep -rn "if __name__"`.
        # Skip kinds with 0 to keep the line short.
        from collections import Counter as _ScriptCounter
        script_counts: _ScriptCounter = _ScriptCounter()
        for _, attrs in G.nodes(data=True):
            if attrs.get("node_kind") != "file":
                continue
            sk = attrs.get("script_kind")
            if sk:
                script_counts[sk] += 1
        if script_counts:
            order = ("main_block", "top_level", "shebang")
            parts = [f"{script_counts[k]} {k}" for k in order if script_counts[k]]
            print(f"  Scripts: {' · '.join(parts)}  "
                  f"(`graphify scripts` to list)")

        # Lap-24 (no-arg redesign): "Suggested next" footer points the
        # agent at the busiest file in the repo (top entry point's
        # source file). Without this, the agent has top-5 entry points
        # + top-5 hubs but no clear "go here next" — and has to guess
        # which verb to fire. Single-line suggestion that's
        # copy-pasteable and lands them on a real API surface.
        # Lap-24 follow-up: skip entry points flagged as phantom (no
        # callable surface in source_file) OR ambiguous (many same-
        # named variants). Empirical case: zero-tvm `report()` ×142
        # was attributed to a config file with 0 fns; following the
        # suggestion landed the agent on a dud. Pick the first entry
        # whose source_file actually has structure AND whose label is
        # unambiguous; skip the rest. Fall back to nothing if no
        # candidate qualifies.
        suggested_line = None
        for lab, _n, sf, _loc, variants in top_entries:
            if not sf:
                continue
            # Skip phantom / heavily-ambiguous entries (variants ≥ 10
            # is the same threshold used for the inline annotation).
            if variants >= 10:
                continue
            if file_struct_count.get(sf, 0) == 0:
                continue
            suggested_line = (
                f"  Suggested next: graphify shape {sf}    "
                f"# `{lab}` lives here, top-ranked entry point"
            )
            break
        # Fallback when no entry-point qualifies: suggest a top class
        # hub via summarize @<class>. Empirical case: zero-tvm corpus
        # has every fn entry-point flagged as phantom/ambiguous, so
        # all 5 entry suggestions get rejected. Without the fallback
        # the agent gets no concrete next move and has to reason from
        # the community list alone. Pick the highest-ranked community
        # whose hub looks class-shaped (PascalCase, no parens) — for
        # zero-tvm that's `Point` at c4.
        if not suggested_line and comm_sizes:
            for cid, _members in comm_sizes:
                hub_label = comm_labels.get(cid, "")
                # Class-shape: starts with uppercase, no parens, no
                # underscores in non-CamelCase form.
                if (isinstance(hub_label, str) and hub_label
                        and hub_label[0:1].isalpha() and hub_label[0].isupper()
                        and "(" not in hub_label and " " not in hub_label
                        and any(c.islower() for c in hub_label)):
                    suggested_line = (
                        f'  Suggested next: graphify summarize "@{hub_label}"    '
                        f"# top class hub (community c{cid})"
                    )
                    break
        if suggested_line:
            print()
            print(suggested_line)

        banner = G.graph.get("_freshness_banner")
        if banner:
            print()
            print(f"  {banner.strip()}")

    elif cmd == "changed":
        if any(a in ("-h", "--help") for a in sys.argv[2:]):
            _print_subcmd_help("changed")
            return
        # Lap-3 wishlist #1: surface nodes added/modified/removed since a
        # reference point (git ref or graph extract). After commits the
        # agent wants to navigate to the diff, not blind-search for new
        # symbols.
        ref: str | None = None
        graph_path = "graphify-out/graph.json"
        # Lap-26 field-report fix: the no-ref "compare against graph extract
        # mtime" mode was the default-and-only-way, but undiscoverable —
        # field report: "I didn't see a flag to ask `since the graph extract
        # even if working tree is clean`." Adding `--since-graph` as an
        # explicit alias makes the intent legible in scripts.
        explicit_since_graph = False
        args = sys.argv[2:]
        i = 0
        while i < len(args):
            a = args[i]
            if a == "--graph" and i + 1 < len(args):
                graph_path = args[i + 1]; i += 2
            elif a.startswith("--graph="):
                graph_path = a.split("=", 1)[1]; i += 1
            elif a == "--since-graph":
                explicit_since_graph = True; i += 1
            elif a in ("--since-commit", "--since") and i + 1 < len(args):
                # Lap-21 polish: discoverable named flag for the positional
                # ref. The positional is still supported for back-compat.
                ref = args[i + 1]; i += 2
            elif a.startswith("--since-commit="):
                ref = a.split("=", 1)[1]; i += 1
            elif a.startswith("--since="):
                ref = a.split("=", 1)[1]; i += 1
            elif a.startswith("--"):
                i += 1
            else:
                ref = a; i += 1
        if explicit_since_graph and ref is not None:
            print("error: --since-graph and --since-commit are mutually exclusive.",
                  file=sys.stderr)
            sys.exit(1)
        gp = Path(graph_path).resolve()
        if not gp.exists():
            print(f"error: graph file not found: {gp}", file=sys.stderr)
            sys.exit(1)
        from collections import defaultdict
        from graphify.build import build_from_json
        _raw = json.loads(gp.read_text(encoding="utf-8"))
        G = build_from_json(_raw, directed=True)
        # Build resolved-path → [node_ids] index from the graph.
        file_to_nodes: dict[str, list[str]] = defaultdict(list)
        for nid, attrs in G.nodes(data=True):
            sf = attrs.get("source_file")
            if not sf:
                continue
            try:
                key = str(Path(sf).resolve())
            except OSError:
                key = str(sf)
            file_to_nodes[key].append(nid)
        root = Path(".").resolve()
        if ref:
            import subprocess
            try:
                res = subprocess.run(
                    ["git", "diff", "--name-only", ref],
                    cwd=str(root), capture_output=True, text=True, timeout=15,
                )
                if res.returncode != 0:
                    print(f"git diff failed: {res.stderr.strip()}", file=sys.stderr)
                    sys.exit(1)
                changed_files = [p.strip() for p in res.stdout.splitlines() if p.strip()]
            except (subprocess.TimeoutExpired, FileNotFoundError):
                print("git not available or timed out", file=sys.stderr)
                sys.exit(1)
            since_label = f"vs {ref}"
        else:
            from graphify.detect import CODE_EXTENSIONS
            import os as _os
            graph_mtime = gp.stat().st_mtime
            # Heavy directories that almost never contain user code we want
            # to track. Pruning at the directory level (vs per-file filter)
            # is the difference between ~60s and <2s on repos with large
            # vendored trees (lap-11 friction report). os.walk lets us
            # mutate `dirnames` in place to skip whole subtrees.
            _SKIP_DIRS = {
                "node_modules", "target", "build", "dist", "out", "vendor",
                "venv", ".venv", "env", ".env",
                "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
                ".git", "graphify-out",
                "coverage", ".coverage", ".tox", ".nox",
                "bower_components", "jspm_packages",
                ".next", ".nuxt", ".turbo", ".cache",
            }
            changed_files = []
            for dirpath, dirnames, filenames in _os.walk(str(root), topdown=True):
                # Mutate dirnames in place so os.walk skips these subtrees.
                # Also skip dot-prefixed dirs (preserves the previous
                # behavior of pruning hidden dirs at any depth).
                dirnames[:] = [d for d in dirnames
                               if d not in _SKIP_DIRS and not d.startswith(".")]
                for fn in filenames:
                    # Cheap suffix gate first — vast majority of files
                    # in even a pruned tree aren't code.
                    if "." not in fn:
                        continue
                    suf = fn[fn.rfind("."):].lower()
                    if suf not in CODE_EXTENSIONS:
                        continue
                    f = Path(dirpath) / fn
                    try:
                        if f.stat().st_mtime > graph_mtime:
                            changed_files.append(str(f.relative_to(root)))
                    except OSError:
                        continue
            since_label = "since graph extract"
        # Bucket files relative to the graph's known set.
        modified: list[tuple[str, list[str]]] = []
        added: list[str] = []
        seen_resolved: set[str] = set()
        for rel in changed_files:
            full = (root / rel).resolve()
            seen_resolved.add(str(full))
            if str(full) in file_to_nodes and full.exists():
                modified.append((rel, file_to_nodes[str(full)]))
            elif full.exists():
                added.append(rel)
        removed: list[tuple[str, list[str]]] = []
        for resolved, nids in file_to_nodes.items():
            if not Path(resolved).exists():
                try:
                    pretty = str(Path(resolved).relative_to(root))
                except ValueError:
                    pretty = resolved
                removed.append((pretty, nids))
        if not (modified or added or removed):
            print(f"changed {since_label}: no code files changed.")
        else:
            print(f"changed {since_label}: "
                  f"{len(modified)} modified · {len(added)} added · {len(removed)} removed")
            for rel, nids in sorted(modified):
                labels = [G.nodes[n].get("label", n) for n in nids[:5]]
                more = f" +{len(nids)-5}" if len(nids) > 5 else ""
                print(f"  M {rel}  ({len(nids)} nodes: {', '.join(labels)}{more})")
            for rel in sorted(added):
                print(f"  A {rel}  (not in graph — `graphify update .` to index)")
            for rel, nids in sorted(removed):
                labels = [G.nodes[n].get("label", n) for n in nids[:5]]
                more = f" +{len(nids)-5}" if len(nids) > 5 else ""
                print(f"  D {rel}  ({len(nids)} stale nodes: {', '.join(labels)}{more})")
            if added or removed:
                print("  re-run `graphify update .` to refresh added/removed nodes.")
            if modified:
                print("  jump: `graphify navigate \"@<label>\"` for any modified node.")

    elif cmd == "peek":
        # One-shot body read. Resolves a target like navigate's `@<label>`
        # (full prefix/substring/fuzzy + path-qualifier ladder), dumps the
        # body, touches no cursor / no session / no recent-paths log.
        # Pairs with `read` (the in-session flow) — `peek` is for
        # "what does this 20-line function do?" without committing to
        # a navigation chain.
        # Lap-21 #2 (sub-agent head-to-head): when `peek <Class>` resolves
        # to a class node, switch to a curated dump — class header +
        # each method's signature + N body lines — instead of dumping
        # the entire class body. Saves the 5-call walk to find/peek
        # individual methods.
        if any(a in ("-h", "--help") for a in sys.argv[2:]):
            _print_subcmd_help("peek")
            return
        from graphify.navigate import (
            DEFAULT_GRAPH_PATH, load_graph,
            _read_body_full, _render_body_text, _read_body_preview,
            _find_leading_docstring_range,
        )
        from graphify.resolve import label_index, resolve_focus
        from graphify.analyze import _is_file_node
        args = sys.argv[2:]
        graph_path = DEFAULT_GRAPH_PATH
        max_lines = 200
        bodies = 3       # lines per method when peeking a class
        method_limit = 12  # cap on methods shown in curated dump
        md = False
        # Lap-25 cluster-B fix: agents fell back to read_file/sed when peek
        # gave them a 482-line body wall and they only wanted the cleanup
        # (--tail) or a specific range (--range). Both surfaced on EGF
        # task_004 + zero-tvm task_005.
        tail: int | None = None
        range_spec: str | None = None
        # Lap-26: --no-docstring strips a leading docstring/JSDoc block
        # from the body. After running `doc`, the agent already has the
        # docstring; re-reading it inside the body is wasted tokens.
        strip_docstring = False
        target: str | None = None
        i = 0
        while i < len(args):
            a = args[i]
            if a == "--graph" and i + 1 < len(args):
                graph_path = args[i + 1]; i += 2
            elif a.startswith("--graph="):
                graph_path = a.split("=", 1)[1]; i += 1
            elif a == "--lines" and i + 1 < len(args):
                max_lines = max(1, int(args[i + 1])); i += 2
            elif a.startswith("--lines="):
                max_lines = max(1, int(a.split("=", 1)[1])); i += 1
            elif a == "--tail" and i + 1 < len(args):
                tail = max(1, int(args[i + 1])); i += 2
            elif a.startswith("--tail="):
                tail = max(1, int(a.split("=", 1)[1])); i += 1
            elif a == "--range" and i + 1 < len(args):
                range_spec = args[i + 1]; i += 2
            elif a.startswith("--range="):
                range_spec = a.split("=", 1)[1]; i += 1
            elif a == "--bodies" and i + 1 < len(args):
                bodies = max(0, int(args[i + 1])); i += 2
            elif a.startswith("--bodies="):
                bodies = max(0, int(a.split("=", 1)[1])); i += 1
            elif a == "--no-docstring":
                strip_docstring = True; i += 1
            elif a == "--md":
                md = True; i += 1
            elif target is None:
                target = a; i += 1
            else:
                # Multiple positional args isn't currently meaningful; treat
                # the first as the target and ignore the rest with a warning
                # rather than silently dropping them.
                print(f"warning: ignoring extra arg `{a}`. peek takes a single target.",
                      file=sys.stderr)
                i += 1
        if tail is not None and range_spec is not None:
            print("error: --tail and --range are mutually exclusive.", file=sys.stderr)
            sys.exit(1)
        range_lo: int | None = None
        range_hi: int | None = None
        if range_spec is not None:
            try:
                if "-" in range_spec:
                    lo_s, hi_s = range_spec.split("-", 1)
                    range_lo = int(lo_s); range_hi = int(hi_s)
                else:
                    range_lo = range_hi = int(range_spec)
                if range_lo > range_hi:
                    raise ValueError
            except ValueError:
                print(f"error: --range expects N-M (file-absolute lines), got `{range_spec}`",
                      file=sys.stderr)
                sys.exit(1)
        if not target:
            print("Usage: graphify peek <symbol> [--lines N] [--md] [--graph PATH]",
                  file=sys.stderr)
            sys.exit(1)
        gp = Path(graph_path)
        if not gp.exists():
            print(f"error: graph not found at {gp}. run `graphify update <path>` first.",
                  file=sys.stderr)
            sys.exit(1)
        G, _comm = load_graph(gp)
        idx = label_index(G)
        # Lap-25: brace-expand `prefix{a,b,c}suffix` into N targets so
        # `peek @Class.{m1,m2,m3}` collapses three peek calls into one.
        # Pattern surfaced in V2 trial 1 transcript (peek __init__ then
        # peek add_all_geometries on GeometryAnalyzer — two separate
        # calls when the agent already had the class).
        targets = _expand_brace_multi_target(target)
        multi = len(targets) > 1
        if multi:
            print(f"# multi-peek: {len(targets)} targets")
        any_ok = False
        for ti, t in enumerate(targets):
            if multi:
                if ti > 0:
                    print()
                print(f"# [{ti+1}/{len(targets)}] {t}")
            chosen, candidates, match_type, _alts = resolve_focus(G, idx, t)
            if multi and chosen:
                miss = _brace_expand_member_miss(t, G.nodes[chosen].get("label", ""))
                if miss:
                    parent, member = miss
                    print(f"no member `{member}` on {parent}.", file=sys.stderr)
                    continue
            if not chosen:
                if candidates:
                    # Multiple matches — print a short disambig list so the
                    # caller can re-run with a more specific target. We
                    # don't run a full disambig listing here because peek
                    # is one-shot; resolution is the agent's job.
                    print(f"ambiguous `{t}` ({len(candidates)} matches). "
                          f"qualify with @<dir>/<file>/<symbol>:", file=sys.stderr)
                    for nid in candidates[:8]:
                        a = G.nodes[nid]
                        sf = a.get("source_file", "?")
                        loc = a.get("source_location", "")
                        label = a.get("label", nid)
                        print(f"  {label}  {sf}{':' + loc[1:] if loc.startswith('L') else ''}",
                              file=sys.stderr)
                    if len(candidates) > 8:
                        print(f"  +{len(candidates) - 8} more", file=sys.stderr)
                else:
                    print(f"no node matches `{t}`.", file=sys.stderr)
                if not multi:
                    sys.exit(1)
                continue
            any_ok = True
            if match_type and match_type != "exact":
                chosen_label = G.nodes[chosen].get("label", chosen)
                print(f"# matched `{t}` → {chosen_label} ({match_type})")
            nattrs = G.nodes[chosen]
            sf = nattrs.get("source_file")
            loc = nattrs.get("source_location")
            node_kind = nattrs.get("node_kind") or ""

            # Lap-21 #2: curated dump for classes. Land on the class header
            # + each method's sig + first N body lines. The agent gets a
            # one-call orientation instead of:
            #   1. peek Class            (gets a 200-line body wall)
            #   2. navigate @Class methods   (lists method ids)
            #   3. peek Class.method_a   (4-5 times, one per method)
            # Reuses _read_body_preview for per-method body windows.
            if node_kind in ("class", "interface") and sf and loc:
                label = nattrs.get("label", chosen)
                # Walk the class's methods via successor edges (`method` or
                # `contains`). Sort by start-line so the dump reads top-to-
                # bottom in source order — matches an agent reading the file.
                method_ids: list[str] = []
                for v in G.successors(chosen):
                    rel = G.edges[chosen, v].get("relation") or ""
                    if rel not in ("method", "contains"):
                        continue
                    v_kind = G.nodes[v].get("node_kind") or ""
                    v_label = G.nodes[v].get("label", "")
                    # Functions/methods only — skip nested classes, types, etc.
                    if v_kind in ("class", "interface", "type_alias"):
                        continue
                    if not (v_label.endswith("()") or v_kind in
                            ("method", "impl_method", "iface_method", "function")):
                        continue
                    method_ids.append(v)

                def _start_line(nid: str) -> int:
                    vloc = G.nodes[nid].get("source_location") or ""
                    if vloc.startswith("L"):
                        try:
                            return int(vloc[1:].split("-", 1)[0].split(":", 1)[0])
                        except ValueError:
                            return 1 << 30
                    return 1 << 30
                method_ids.sort(key=_start_line)

                # Print header.
                loc_short = loc[1:] if loc.startswith("L") else loc
                kind_word = "class" if node_kind == "class" else "interface"
                print(f"  peek {kind_word} @{label}  {sf}:{loc_short}  "
                      f"({len(method_ids)} method(s))")
                # Class header line — first line of class body, single line.
                class_head, _ln, _trunc = _read_body_full(
                    sf, loc, max_lines=1, flat=True
                )
                if class_head:
                    print(f"  {class_head[0].strip()}")

                shown = method_ids[:method_limit]
                # Lap-25 rename: inner counter used to be `idx` which
                # shadowed the outer `idx = label_index(G)` used by
                # resolve_focus on the next loop iteration in multi mode.
                for mi, mid in enumerate(shown, 1):
                    m = G.nodes[mid]
                    m_label = m.get("label", mid) or mid
                    m_loc = m.get("source_location") or ""
                    m_loc_short = m_loc[1:] if m_loc.startswith("L") else ""
                    # Strip leading dot for display since we already announced
                    # "class Foo" — `.foo()` reads as `foo()` in this scope.
                    disp = m_label.lstrip(".") if m_label.startswith(".") else m_label
                    preview = _read_body_preview(
                        m.get("source_file") or sf, m_loc, n=max(1, bodies + 1)
                    )
                    print(f"    [{mi}] {disp}  L{m_loc_short}" if m_loc_short
                          else f"    [{mi}] {disp}")
                    # First entry is the signature line; stripped to one line.
                    # Indent body lines so the per-method block reads as a unit.
                    for j, line in enumerate(preview or []):
                        if j == 0:
                            # _read_body_preview already returns the header.
                            # Skip if it just repeats the label-only sig (rare;
                            # body_preview falls back when source unreadable).
                            if line.strip().startswith("def ") or line.strip().startswith(
                                ("async def ", "function ", "static ", "public ", "private ", "protected ", "export ", "constructor")
                            ) or "(" in line:
                                print(f"        {line.strip()}")
                                continue
                        print(f"        {line.rstrip()}")
                more = len(method_ids) - len(shown)
                if more > 0:
                    print(f"    +{more} more — `peek {label}.<method>` for individual bodies")
            else:
                # Lap-25 cluster-B incidental fix: peek used `_is_file_node`
                # (an analyze.py classifier intended to filter method-stubs
                # from "knowledge gap" reports) which returns True for every
                # `.method()` label. That accidentally put method peeks into
                # flat-read mode — invisible at the 200-line default but
                # broken under --tail/--range with a 10k cap. Anchor on the
                # actual node_kind so the indent walker handles methods.
                is_file = (nattrs.get("node_kind") == "file")
                if tail is not None or range_lo is not None:
                    # Read full body (generous cap), then slice. Truncation
                    # marker drops because the caller asked for a window, not
                    # a head-cap; line numbers in the render show what's shown.
                    full_body, ln_full, _ = _read_body_full(
                        sf, loc, max_lines=10_000, flat=is_file
                    )
                    if tail is not None:
                        body = full_body[-tail:]
                        ln = ln_full + (len(full_body) - len(body))
                        trunc = False
                    else:
                        body_end = ln_full + len(full_body) - 1
                        if range_hi < ln_full or range_lo > body_end:
                            label = nattrs.get("label", chosen)
                            loc_short = loc[1:] if loc and loc.startswith("L") else (loc or "")
                            print(f"  peek @{label}: --range {range_lo}-{range_hi} "
                                  f"falls outside body L{loc_short} "
                                  f"(body lines {ln_full}-{body_end})")
                            if multi:
                                continue
                            sys.exit(1)
                        lo = max(range_lo, ln_full)
                        hi = min(range_hi, body_end)
                        body = full_body[lo - ln_full:hi - ln_full + 1]
                        ln = lo
                        trunc = False
                else:
                    body, ln, trunc = _read_body_full(sf, loc, max_lines=max_lines, flat=is_file)
                # Lap-26: --no-docstring elides a leading docstring/JSDoc
                # block from the rendered body. We pass the half-open
                # range to the renderer rather than mutating `body` so
                # file-absolute line numbers stay correct on both sides.
                ds_range = (0, 0)
                if strip_docstring and body:
                    ds_range = _find_leading_docstring_range(body)
                from graphify.navigate import _compute_owner_class
                data = {
                    "type": "body",
                    "label": nattrs.get("label", chosen),
                    "owner_class": _compute_owner_class(
                        G, chosen, nattrs.get("label")),
                    "source_file": sf,
                    "source_location": loc,
                    "lines": body,
                    "start_line": ln,
                    "truncated": trunc,
                    "docstring_range": ds_range,
                    "entry_point": "peek",
                }
                print(_render_body_text(data, md=md))
        if multi and not any_ok:
            sys.exit(1)

    elif cmd == "locate":
        # Lap-21 (R3 sub-agent feedback): multi-symbol file:line lookup,
        # no body. R3-A's graphify agent ran 3 separate navigates to
        # find the line ranges of 3 GeometryAnalyzer methods — locate
        # collapses that to one call. Resolution mirrors peek's
        # (full prefix/substring/fuzzy ladder + path qualifier) so an
        # agent reaches for the same disambiguation forms it already
        # uses on peek/navigate. Best-effort batch: a missed or ambig
        # symbol prints a per-row note but doesn't fail the whole call;
        # exit 1 only when every symbol misses.
        if any(a in ("-h", "--help") for a in sys.argv[2:]):
            _print_subcmd_help("locate")
            return
        from graphify.navigate import DEFAULT_GRAPH_PATH, load_graph
        from graphify.resolve import label_index, resolve_focus
        args = sys.argv[2:]
        graph_path = DEFAULT_GRAPH_PATH
        targets: list[str] = []
        i = 0
        while i < len(args):
            a = args[i]
            if a == "--graph" and i + 1 < len(args):
                graph_path = args[i + 1]; i += 2
            elif a.startswith("--graph="):
                graph_path = a.split("=", 1)[1]; i += 1
            else:
                targets.append(a); i += 1
        if not targets:
            print("Usage: graphify locate <symbol> [<symbol> ...] [--graph PATH]",
                  file=sys.stderr)
            sys.exit(1)
        gp = Path(graph_path)
        if not gp.exists():
            print(f"error: graph not found at {gp}. run `graphify update <path>` first.",
                  file=sys.stderr)
            sys.exit(1)
        G, _comm = load_graph(gp)
        idx = label_index(G)

        def _fmt_loc(loc: str) -> str:
            # Mirror peek's ambig-list convention: `L42-89` → `42-89`.
            return loc[1:] if isinstance(loc, str) and loc.startswith("L") else (loc or "?")

        print(f"locate: {len(targets)} target{'s' if len(targets) != 1 else ''}")
        hits = 0
        # Lap-27 followup: collect source_files of resolved targets so
        # we can stamp them into recent-paths after the loop. Reads of
        # any of these files later in the session pass through the
        # PreToolUse hook silently.
        resolved_files: set[str] = set()
        for t in targets:
            chosen, candidates, match_type, _alts = resolve_focus(G, idx, t)
            if chosen:
                hits += 1
                a = G.nodes[chosen]
                label = a.get("label", chosen)
                sf = a.get("source_file") or "?"
                loc = a.get("source_location") or ""
                tag = f" ({match_type})" if match_type and match_type != "exact" else ""
                print(f"  {label:<32} {sf}:{_fmt_loc(loc)}{tag}")
                if a.get("source_file"):
                    resolved_files.add(a["source_file"])
            elif candidates:
                # Surface up to 3 candidate locations so the agent can
                # re-issue locate with a path-qualifier without an extra
                # navigate call. The `qualify with` hint names the form.
                print(f"  {t:<32} ambiguous ({len(candidates)}) — "
                      f"qualify with @<dir>/<file>/<sym>")
                for nid in candidates[:3]:
                    a = G.nodes[nid]
                    sf = a.get("source_file") or "?"
                    loc = a.get("source_location") or ""
                    print(f"      → {sf}:{_fmt_loc(loc)}")
                if len(candidates) > 3:
                    print(f"      → +{len(candidates) - 3} more")
            else:
                print(f"  {t:<32} not found")
        if resolved_files:
            _stamp_recent_files(gp, resolved_files)
        if hits == 0:
            sys.exit(1)

    elif cmd == "files":
        # Lap-27 dispatch-corpus follow-up: list source-file nodes by
        # basename glob. Closes the `find -name "*.py"` pattern that
        # surfaced 5+ times in a real EGF Explore-agent transcript —
        # the agent reaches for `find` because there's no graphify
        # verb for "what files match this pattern?". Trailing-slash
        # dir queries (`@<dir>/`) cover one slice; this covers the
        # other (filename-pattern, possibly cross-directory).
        if any(a in ("-h", "--help") for a in sys.argv[2:]):
            _print_subcmd_help("files")
            return
        from graphify.navigate import DEFAULT_GRAPH_PATH, load_graph
        from graphify.resolve import path_glob_match
        args = sys.argv[2:]
        graph_path = DEFAULT_GRAPH_PATH
        pattern: str | None = None
        i = 0
        while i < len(args):
            a = args[i]
            if a == "--graph" and i + 1 < len(args):
                graph_path = args[i + 1]; i += 2
            elif a.startswith("--graph="):
                graph_path = a.split("=", 1)[1]; i += 1
            elif pattern is None:
                pattern = a; i += 1
            else:
                print(f"warning: ignoring extra arg `{a}`. files takes a single glob.",
                      file=sys.stderr)
                i += 1
        if not pattern:
            print("Usage: graphify files <glob> [--graph PATH]", file=sys.stderr)
            sys.exit(1)
        gp = Path(graph_path)
        if not gp.exists():
            print(f"error: graph not found at {gp}. run `graphify update <path>` first.",
                  file=sys.stderr)
            sys.exit(1)
        G, _comm = load_graph(gp)
        # Bare patterns (no `/`) match basename only; patterns with
        # `/` match the full source_file path. fnmatch's translate
        # treats `*` as "anything except /" only when explicitly
        # documented otherwise, so for path globs we still rely on
        # fnmatch which is greedy across `/`. That's fine for the
        # `<dir>/<glob>` shape agents reach for; if precise depth
        # control becomes a real friction we can layer on top later.
        path_glob = "/" in pattern
        hits: list[tuple[str, str]] = []
        for nid, attrs in G.nodes(data=True):
            if attrs.get("node_kind") != "file":
                continue
            sf = attrs.get("source_file") or ""
            if not sf:
                continue
            target = sf if path_glob else sf.rsplit("/", 1)[-1]
            if path_glob_match(target, pattern):
                hits.append((sf, attrs.get("label") or ""))
        if not hits:
            print(f"no files match `{pattern}`.", file=sys.stderr)
            sys.exit(1)
        # Sort by source_file for stable output. Path-grouped comes
        # naturally because string sort puts `tests/...` together.
        hits.sort()
        print(f"files: {len(hits)} matching `{pattern}`")
        for sf, _label in hits:
            print(f"  {sf}")
        # Lap-27 followup: stamp matched files into recent-paths so a
        # subsequent Read of any of them is a quiet passthrough.
        # Cap at 50 entries to bound the dedup-log size.
        _stamp_recent_files(gp, [sf for sf, _label in hits[:50]])

    elif cmd == "scripts":
        # Lap-27 sub-agent A/B follow-up: A/B test (n=3 per cell) showed
        # the script_kind tag on file nodes goes unreached-for. Both arms
        # tied at 5.67 calls on "list every CLI script under graphify/" —
        # condition B (with paste-prompt) didn't reach for `navigate
        # "@graphify/"` (which would surface `· script:main` badges) and
        # fell back to grep "if __name__" alongside condition A.
        # `scripts` is the verb-fusion answer: one call lists every
        # script-tagged file with kind + entry line(s), including the
        # `top_level` style (no canonical __name__ block) that no grep
        # would catch.
        if any(a in ("-h", "--help") for a in sys.argv[2:]):
            _print_subcmd_help("scripts")
            return
        from graphify.navigate import DEFAULT_GRAPH_PATH, load_graph
        from graphify.resolve import path_glob_match
        args = sys.argv[2:]
        graph_path = DEFAULT_GRAPH_PATH
        pattern: str | None = None
        kind_filter: str | None = None
        i = 0
        while i < len(args):
            a = args[i]
            if a == "--graph" and i + 1 < len(args):
                graph_path = args[i + 1]; i += 2
            elif a.startswith("--graph="):
                graph_path = a.split("=", 1)[1]; i += 1
            elif a == "--kind" and i + 1 < len(args):
                kind_filter = args[i + 1]; i += 2
            elif a.startswith("--kind="):
                kind_filter = a.split("=", 1)[1]; i += 1
            elif pattern is None:
                pattern = a; i += 1
            else:
                print(f"warning: ignoring extra arg `{a}`. scripts takes a single glob.",
                      file=sys.stderr)
                i += 1
        gp = Path(graph_path)
        if not gp.exists():
            print(f"error: graph not found at {gp}. run `graphify update <path>` first.",
                  file=sys.stderr)
            sys.exit(1)
        # Short-form aliases mirror the frontier `_meta_tag` rendering
        # so `--kind main` matches the badge agents see in listings.
        KIND_SHORT = {"main_block": "main", "top_level": "tl", "shebang": "sh"}
        SHORT_TO_LONG = {v: k for k, v in KIND_SHORT.items()}
        KIND_ORDER = {"main_block": 0, "top_level": 1, "shebang": 2}
        if kind_filter:
            if kind_filter in SHORT_TO_LONG:
                kind_filter = SHORT_TO_LONG[kind_filter]
            elif kind_filter not in KIND_SHORT:
                print(
                    f"error: unknown --kind `{kind_filter}` (one of: "
                    "main_block/main, top_level/tl, shebang/sh)",
                    file=sys.stderr,
                )
                sys.exit(1)
        G, _comm = load_graph(gp)
        path_glob = pattern is not None and "/" in pattern
        hits: list[tuple[int, str, str, list[int]]] = []
        for nid, attrs in G.nodes(data=True):
            if attrs.get("node_kind") != "file":
                continue
            kind = attrs.get("script_kind")
            if not kind:
                continue
            if kind_filter and kind != kind_filter:
                continue
            sf = attrs.get("source_file") or ""
            if not sf:
                continue
            if pattern:
                target = sf if path_glob else sf.rsplit("/", 1)[-1]
                if not path_glob_match(target, pattern):
                    continue
            entries = list(attrs.get("script_entries") or [])
            hits.append((KIND_ORDER.get(kind, 99), sf, kind, entries))
        if not hits:
            msg = "no script-tagged files"
            if pattern:
                msg += f" match `{pattern}`"
            if kind_filter:
                tail = "" if not pattern else " and"
                msg += f"{tail} kind=`{kind_filter}`"
            print(f"{msg}.", file=sys.stderr)
            sys.exit(1)
        hits.sort(key=lambda t: (t[0], t[1]))
        pad = max(len(sf) for _, sf, _, _ in hits) + 2
        header = f"scripts: {len(hits)} found"
        bits: list[str] = []
        if pattern:
            bits.append(f"matching `{pattern}`")
        if kind_filter:
            bits.append(f"kind={kind_filter}")
        if bits:
            header += " (" + ", ".join(bits) + ")"
        print(header)
        for _, sf, kind, entries in hits:
            short = KIND_SHORT.get(kind, kind)
            line_str = ", ".join(f"L{ln}" for ln in entries) if entries else "—"
            print(f"  {sf:<{pad}}script:{short:<5} {line_str}")
        _stamp_recent_files(gp, [sf for _, sf, _, _ in hits[:50]])

    elif cmd == "blast":
        # Lap-22 (meta-harness friction corpus): one-shot callers + callees
        # for a symbol — the "blast radius" of a symbol for refactor planning.
        # Two agents independently asked for this on the same EGF blast-radius
        # task in the meta-harness rollouts; today's recovery is `navigate
        # @sym in` then re-focus and `navigate @sym out --kind=calls`,
        # three calls plus session bookkeeping. blast collapses that to one
        # cursor-free call. Internally piggybacks on the existing `callers`
        # and `callees` cursor pivots (which themselves route into in/out
        # with kinds={"calls"}), so rank/drop/disambig logic stays in one
        # place.
        if any(a in ("-h", "--help") for a in sys.argv[2:]):
            _print_subcmd_help("blast")
            return
        from graphify.navigate import (
            DEFAULT_GRAPH_PATH, load_graph, Cursor,
            _pivot_data, _listing_data, _render_listing_text,
        )
        from graphify.resolve import label_index, resolve_focus
        args = sys.argv[2:]
        graph_path = DEFAULT_GRAPH_PATH
        md = False
        limit = 30
        include_inferred = False
        target: str | None = None
        i = 0
        while i < len(args):
            a = args[i]
            if a == "--graph" and i + 1 < len(args):
                graph_path = args[i + 1]; i += 2
            elif a.startswith("--graph="):
                graph_path = a.split("=", 1)[1]; i += 1
            elif a == "--limit" and i + 1 < len(args):
                limit = max(1, int(args[i + 1])); i += 2
            elif a.startswith("--limit="):
                limit = max(1, int(a.split("=", 1)[1])); i += 1
            elif a == "--md":
                md = True; i += 1
            elif a == "--include-inferred":
                include_inferred = True; i += 1
            elif target is None:
                target = a; i += 1
            else:
                print(f"warning: ignoring extra arg `{a}`. blast takes a single target.",
                      file=sys.stderr)
                i += 1
        if not target:
            print("Usage: graphify blast <symbol> [--limit N] [--md] "
                  "[--include-inferred] [--graph PATH]",
                  file=sys.stderr)
            sys.exit(1)
        gp = Path(graph_path)
        if not gp.exists():
            print(f"error: graph not found at {gp}. run `graphify update <path>` first.",
                  file=sys.stderr)
            sys.exit(1)
        G, communities = load_graph(gp)
        idx = label_index(G)
        # Lap-25: brace-expand `blast @Class.{m1,m2,m3}` into N blasts.
        # Same fusion shape as multi-peek — agents asking "who calls
        # each method of this class" today run N separate blast calls.
        targets = _expand_brace_multi_target(target)
        multi = len(targets) > 1
        if multi:
            print(f"# multi-blast: {len(targets)} targets")
        any_ok = False
        for ti, t in enumerate(targets):
            if multi:
                if ti > 0:
                    print()
                print(f"# [{ti+1}/{len(targets)}] {t}")
            chosen, candidates, match_type, _alts = resolve_focus(G, idx, t)
            if multi and chosen:
                miss = _brace_expand_member_miss(t, G.nodes[chosen].get("label", ""))
                if miss:
                    parent, member = miss
                    print(f"no member `{member}` on {parent}.", file=sys.stderr)
                    continue
            if not chosen:
                # Mirror peek's disambig output so the caller can re-issue
                # blast with a path-qualified target.
                if candidates:
                    print(f"ambiguous `{t}` ({len(candidates)} matches). "
                          f"qualify with @<dir>/<file>/<symbol>:", file=sys.stderr)
                    for nid in candidates[:8]:
                        a = G.nodes[nid]
                        sf = a.get("source_file", "?")
                        loc = a.get("source_location", "")
                        label = a.get("label", nid)
                        print(f"  {label}  {sf}{':' + loc[1:] if loc.startswith('L') else ''}",
                              file=sys.stderr)
                    if len(candidates) > 8:
                        print(f"  +{len(candidates) - 8} more", file=sys.stderr)
                else:
                    print(f"no node matches `{t}`.", file=sys.stderr)
                if not multi:
                    sys.exit(1)
                continue
            any_ok = True
            if match_type and match_type != "exact":
                chosen_label = G.nodes[chosen].get("label", chosen)
                print(f"# matched `{t}` → {chosen_label} ({match_type})")
            cursor = Cursor(current=chosen)
            extracted_only = not include_inferred

            # Two pivot calls share the cursor; their kind={"calls"} filter is
            # baked into the callers/callees branch of _pivot_data.
            _, in_ids, in_edges, in_sort, in_drops = _pivot_data(
                G, communities, cursor, "callers",
                extracted_only=extracted_only, min_confidence=None,
            )
            _, out_ids, out_edges, out_sort, out_drops = _pivot_data(
                G, communities, cursor, "callees",
                extracted_only=extracted_only, min_confidence=None,
            )
            in_listing = _listing_data(
                G, in_ids, "callers", in_edges, len(in_ids), in_sort, limit, in_drops,
                extracted_only=extracted_only,
            )
            out_listing = _listing_data(
                G, out_ids, "callees", out_edges, len(out_ids), out_sort, limit, out_drops,
                extracted_only=extracted_only,
            )

            nattrs = G.nodes[chosen]
            label = nattrs.get("label", chosen)
            sf = nattrs.get("source_file") or "?"
            loc = nattrs.get("source_location") or ""
            loc_str = (":" + loc[1:]) if isinstance(loc, str) and loc.startswith("L") else ""
            print(f"blast @{label}  {sf}{loc_str}  "
                  f"({len(in_ids)} caller{'s' if len(in_ids) != 1 else ''}, "
                  f"{len(out_ids)} callee{'s' if len(out_ids) != 1 else ''})")
            print()
            print("## Callers")
            if in_ids:
                print(_render_listing_text(in_listing, show_ops=False, md=md))
            else:
                print("  (none)")
            print()
            print("## Callees")
            if out_ids:
                print(_render_listing_text(out_listing, show_ops=False, md=md))
            else:
                print("  (none)")
        if multi and not any_ok:
            sys.exit(1)

    elif cmd == "doc":
        # One-shot rationale dump. Field-report wish: "I had to pivot from
        # `sector_transition_entropy` → method → docstring manually." `doc`
        # collapses that to a single call: resolve symbol, walk
        # rationale_for edges, dump signature + docstrings.
        if any(a in ("-h", "--help") for a in sys.argv[2:]):
            _print_subcmd_help("doc")
            return
        from graphify.navigate import (
            DEFAULT_GRAPH_PATH, load_graph,
            doc_node, _render_doc_text,
        )
        from graphify.resolve import label_index, resolve_focus
        args = sys.argv[2:]
        graph_path = DEFAULT_GRAPH_PATH
        max_lines = 40
        md = False
        fmt = "text"
        target: str | None = None
        i = 0
        while i < len(args):
            a = args[i]
            if a == "--graph" and i + 1 < len(args):
                graph_path = args[i + 1]; i += 2
            elif a.startswith("--graph="):
                graph_path = a.split("=", 1)[1]; i += 1
            elif a == "--lines" and i + 1 < len(args):
                max_lines = max(1, int(args[i + 1])); i += 2
            elif a.startswith("--lines="):
                max_lines = max(1, int(a.split("=", 1)[1])); i += 1
            elif a == "--md":
                md = True; i += 1
            elif a == "--json":
                fmt = "json"; i += 1
            elif target is None:
                target = a; i += 1
            else:
                print(f"warning: ignoring extra arg `{a}`. doc takes a single target.",
                      file=sys.stderr)
                i += 1
        if not target:
            print("Usage: graphify doc <symbol> [--lines N] [--md] [--json] [--graph PATH]",
                  file=sys.stderr)
            sys.exit(1)
        gp = Path(graph_path)
        if not gp.exists():
            print(f"error: graph not found at {gp}. run `graphify update <path>` first.",
                  file=sys.stderr)
            sys.exit(1)
        G, _comm = load_graph(gp)
        idx = label_index(G)
        # Lap-25: brace-expand `@Class.{m1,m2,m3}` so an agent can pull
        # signatures + docstrings of several methods in one call. Same
        # pattern as multi-peek / multi-blast; the verb-fusion family is
        # complete (bodies / callers+callees / sig+docstring).
        targets = _expand_brace_multi_target(target)
        multi = len(targets) > 1
        if multi:
            if fmt == "json":
                print("error: --json is incompatible with brace-expanded multi-doc.",
                      file=sys.stderr)
                sys.exit(1)
            print(f"# multi-doc: {len(targets)} targets")
        any_ok = False
        for ti, t in enumerate(targets):
            if multi:
                if ti > 0:
                    print()
                print(f"# [{ti+1}/{len(targets)}] {t}")
            chosen, candidates, match_type, _alts = resolve_focus(G, idx, t)
            if multi and chosen:
                miss = _brace_expand_member_miss(t, G.nodes[chosen].get("label", ""))
                if miss:
                    parent, member = miss
                    print(f"no member `{member}` on {parent}.", file=sys.stderr)
                    continue
            if not chosen:
                if candidates:
                    print(f"ambiguous `{t}` ({len(candidates)} matches). "
                          f"qualify with @<dir>/<file>/<symbol>:", file=sys.stderr)
                    for nid in candidates[:8]:
                        a = G.nodes[nid]
                        sf = a.get("source_file", "?")
                        loc = a.get("source_location", "")
                        label = a.get("label", nid)
                        print(f"  {label}  {sf}{':' + loc[1:] if loc.startswith('L') else ''}",
                              file=sys.stderr)
                    if len(candidates) > 8:
                        print(f"  +{len(candidates) - 8} more", file=sys.stderr)
                else:
                    print(f"no node matches `{t}`.", file=sys.stderr)
                if not multi:
                    sys.exit(1)
                continue
            any_ok = True
            if match_type and match_type != "exact":
                chosen_label = G.nodes[chosen].get("label", chosen)
                print(f"# matched `{t}` → {chosen_label} ({match_type})")
            data = doc_node(G, chosen, max_rationale_lines=max_lines)
            if fmt == "json":
                print(json.dumps(data))
            else:
                print(_render_doc_text(data, md=md))
        if multi and not any_ok:
            sys.exit(1)

    elif cmd == "shape":
        # File-shape summary: "N classes, M fns, K consts, X imports,
        # longest fn=foo() (200 ln)". Equivalent of `wc -l + ctags --list`
        # for orientation. Read-only one-shot — no cursor, no session.
        if any(a in ("-h", "--help") for a in sys.argv[2:]):
            _print_subcmd_help("shape")
            return
        from graphify.navigate import (
            DEFAULT_GRAPH_PATH, load_graph,
            shape_file, _render_shape_text,
        )
        from graphify.resolve import label_index, resolve_focus
        from graphify.analyze import _is_file_node
        args = sys.argv[2:]
        graph_path = DEFAULT_GRAPH_PATH
        # Lap-27 (sub-agent dispatch corpus): shape now accepts multiple
        # targets — `shape f1.py f2.py` runs shape on each and emits one
        # section per file. Closes the `find -o -name + per-file shape`
        # pattern. Brace-expand `shape @Class.{m1,m2,m3}` follows the
        # peek/blast/doc convention via _expand_brace_multi_target.
        targets: list[str] = []
        fmt = "text"
        # Default 8: a one-screen summary keeps shape useful as a cold-start
        # primitive. `--limit N` widens; `--all` returns the full lists.
        shape_limit: int | None = 8
        i = 0
        while i < len(args):
            a = args[i]
            if a == "--graph" and i + 1 < len(args):
                graph_path = args[i + 1]; i += 2
            elif a.startswith("--graph="):
                graph_path = a.split("=", 1)[1]; i += 1
            elif a == "--json":
                fmt = "json"; i += 1
            elif a == "--all":
                shape_limit = None; i += 1
            elif a == "--limit" and i + 1 < len(args):
                shape_limit = max(1, int(args[i + 1])); i += 2
            elif a.startswith("--limit="):
                shape_limit = max(1, int(a.split("=", 1)[1])); i += 1
            else:
                targets.append(a); i += 1
        if not targets:
            print("Usage: graphify shape <file> [<file> ...] "
                  "[--limit N | --all] [--json] [--graph PATH]",
                  file=sys.stderr)
            sys.exit(1)
        gp = Path(graph_path)
        if not gp.exists():
            print(f"error: graph not found at {gp}. run `graphify update <path>` first.",
                  file=sys.stderr)
            sys.exit(1)
        # Brace-expand each positional. A bare `shape file.py` returns
        # ["file.py"]; `shape {a,b}.py` returns ["a.py", "b.py"]. Multi
        # positional + brace-expand compose: `shape a.py {b,c}.py` →
        # ["a.py", "b.py", "c.py"].
        expanded: list[str] = []
        for t in targets:
            expanded.extend(_expand_brace_multi_target(t))
        targets = expanded
        multi = len(targets) > 1
        # JSON output for multi-target packs results into a list so the
        # consumer can iterate; single target keeps the prior dict shape
        # for backwards compat.
        json_results: list[dict] = []
        G, _comm = load_graph(gp)
        idx = label_index(G)
        any_ok = False
        for ti, target in enumerate(targets):
            if multi and fmt == "text":
                if ti > 0:
                    print()
                print(f"# [{ti+1}/{len(targets)}] shape: {target}")
            chosen, candidates, match_type, _alts = resolve_focus(G, idx, target)
            if not chosen:
                if candidates:
                    print(f"ambiguous `{target}` ({len(candidates)} matches). "
                          f"qualify with @<dir>/<file>:", file=sys.stderr)
                    for nid in candidates[:8]:
                        a = G.nodes[nid]
                        sf = a.get("source_file", "?")
                        print(f"  {a.get('label', nid)}  {sf}", file=sys.stderr)
                else:
                    print(f"no node matches `{target}`.", file=sys.stderr)
                if not multi:
                    sys.exit(1)
                continue
            if not _is_file_node(G, chosen):
                # `shape` only makes sense on a file. If the agent landed on
                # a class/fn, redirect to the file containing it.
                sf = G.nodes[chosen].get("source_file")
                print(f"error: `{target}` resolved to "
                      f"{G.nodes[chosen].get('label', chosen)} "
                      f"(not a file). try `graphify shape \"@{sf}\"` "
                      f"if you meant the file.", file=sys.stderr)
                if not multi:
                    sys.exit(1)
                continue
            any_ok = True
            data = shape_file(G, chosen, limit=shape_limit)
            if fmt == "json":
                json_results.append(data)
            else:
                print(_render_shape_text(data))
            # Lap-27 followup: stamp the file's source_file into recent-
            # paths so the PreToolUse hook bails on a Read of this same
            # file later in the session. The cli-stamp already covers
            # the within-5-min case via load_graph; this extends the
            # signal to the 5–30 min window.
            sf = G.nodes[chosen].get("source_file")
            if sf:
                _stamp_recent_files(gp, [sf])
        if fmt == "json":
            # Single target: emit the dict for back-compat. Multi: list.
            print(json.dumps(json_results[0] if len(json_results) == 1
                             else json_results))
        if not any_ok:
            sys.exit(1)

    elif cmd == "search":
        # Body-text search across nodes. Walks each non-archived code-file,
        # greps for the pattern, attributes each match line to the deepest
        # enclosing node so the agent gets back symbol context (label,
        # community, degree) instead of naked file:line tuples. Eliminates
        # the grep fallback for "where does this string appear in code".
        if any(a in ("-h", "--help") for a in sys.argv[2:]):
            _print_subcmd_help("search")
            return
        from graphify.navigate import (
            DEFAULT_GRAPH_PATH, load_graph, search_bodies, _render_search_text,
        )
        args = sys.argv[2:]
        graph_path = DEFAULT_GRAPH_PATH
        pattern: str | None = None
        kind = "code"
        archived_mode = "no"
        limit = 50
        # Default 1 line of pre/post context: a single match line on its own
        # rarely disambiguates definition vs call vs string literal vs comment.
        # The field-report from another Claude flagged --context 0 (the prior
        # default) as forcing a follow-up `peek` per hit. --context 0 still
        # disables context for callers who explicitly want minimal output.
        context = 1
        by_symbol = False
        files_only = False
        in_files: str | None = None
        md = False
        fmt = "text"
        # Lap-26: --idents auto-expands a single identifier into all 5
        # casing variants joined as a `\b(...)\b` regex. Field-report
        # friction: agent had to manually OR `modal-complexity` and
        # `modal_complexity` to catch both forms during a rename audit.
        idents_mode = False
        i = 0
        while i < len(args):
            a = args[i]
            if a == "--graph" and i + 1 < len(args):
                graph_path = args[i + 1]; i += 2
            elif a.startswith("--graph="):
                graph_path = a.split("=", 1)[1]; i += 1
            elif a == "--kind" and i + 1 < len(args):
                kind = args[i + 1]; i += 2
            elif a.startswith("--kind="):
                kind = a.split("=", 1)[1]; i += 1
            elif a == "--no-archived":
                archived_mode = "no"; i += 1
            elif a == "--archived-only":
                archived_mode = "only"; i += 1
            elif a == "--all-archived":
                archived_mode = "all"; i += 1
            elif a == "--limit" and i + 1 < len(args):
                limit = max(1, int(args[i + 1])); i += 2
            elif a.startswith("--limit="):
                limit = max(1, int(a.split("=", 1)[1])); i += 1
            elif a == "--context" and i + 1 < len(args):
                context = max(0, int(args[i + 1])); i += 2
            elif a.startswith("--context="):
                context = max(0, int(a.split("=", 1)[1])); i += 1
            elif a == "--by-symbol":
                by_symbol = True; i += 1
            elif a == "--files-only":
                files_only = True; i += 1
            elif a == "--in-files" and i + 1 < len(args):
                in_files = args[i + 1]; i += 2
            elif a.startswith("--in-files="):
                in_files = a.split("=", 1)[1]; i += 1
            elif a == "--idents":
                idents_mode = True; i += 1
            elif a == "--md":
                md = True; i += 1
            elif a == "--json":
                fmt = "json"; i += 1
            elif pattern is None:
                pattern = a; i += 1
            else:
                # `graphify search foo bar` — concatenate as alternation? No,
                # safer to error out. The user can quote the regex if they
                # need spaces.
                print(f"warning: ignoring extra arg `{a}`. search takes a single pattern.",
                      file=sys.stderr)
                i += 1
        if not pattern:
            print("Usage: graphify search <pattern> [--kind code|rationale|all] "
                  "[--limit N] [--context N] [--by-symbol] [--idents] [--md] [--json] "
                  "[--no-archived|--archived-only|--all-archived] [--graph PATH]",
                  file=sys.stderr)
            sys.exit(1)
        if idents_mode:
            from graphify.navigate import _expand_identifier_casings
            expanded, casings = _expand_identifier_casings(pattern)
            print(f"# --idents: expanded `{pattern}` → "
                  f"{', '.join(casings)}", file=sys.stderr)
            pattern = expanded
        if kind not in ("code", "rationale", "all"):
            print(f"error: --kind must be one of code|rationale|all (got `{kind}`)",
                  file=sys.stderr)
            sys.exit(1)
        gp = Path(graph_path)
        if not gp.exists():
            print(f"error: graph not found at {gp}. run `graphify update <path>` first.",
                  file=sys.stderr)
            sys.exit(1)
        G, _comm = load_graph(gp)
        data = search_bodies(G, pattern, kind=kind,
                              archived_mode=archived_mode,
                              limit=limit, context=context,
                              by_symbol=by_symbol,
                              files_only=files_only,
                              in_files=in_files)
        if fmt == "json":
            print(json.dumps(data))
        else:
            print(_render_search_text(data, md=md))
        # Lap-27 followup: stamp matched files into recent-paths so the
        # PreToolUse hook bails on a Read of any of them. Cap to the
        # first 20 hits to bound the dedup-log writes.
        match_files: set[str] = set()
        for hit in (data.get("hits") or [])[:20]:
            sf = hit.get("source_file")
            if sf:
                match_files.add(sf)
        if not match_files:
            for f in (data.get("files") or [])[:20]:
                if isinstance(f, str):
                    match_files.add(f)
                elif isinstance(f, dict) and f.get("source_file"):
                    match_files.add(f["source_file"])
        if match_files:
            _stamp_recent_files(gp, match_files)

    elif cmd == "add":
        if len(sys.argv) < 3:
            print("Usage: graphify add <url> [--author Name] [--contributor Name] [--dir ./raw]", file=sys.stderr)
            sys.exit(1)
        from graphify.ingest import ingest as _ingest
        url = sys.argv[2]
        author: str | None = None
        contributor: str | None = None
        target_dir = Path("raw")
        args = sys.argv[3:]
        i = 0
        while i < len(args):
            if args[i] == "--author" and i + 1 < len(args):
                author = args[i + 1]; i += 2
            elif args[i] == "--contributor" and i + 1 < len(args):
                contributor = args[i + 1]; i += 2
            elif args[i] == "--dir" and i + 1 < len(args):
                target_dir = Path(args[i + 1]); i += 2
            else:
                i += 1
        try:
            saved = _ingest(url, target_dir, author=author, contributor=contributor)
            print(f"Saved to {saved}")
            print("Run /graphify --update in your AI assistant to update the graph.")
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "watch":
        watch_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(".")
        if not watch_path.exists():
            print(f"error: path not found: {watch_path}", file=sys.stderr)
            sys.exit(1)
        from graphify.watch import watch as _watch
        try:
            _watch(watch_path)
        except ImportError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "cluster-only":
        watch_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(".")
        graph_json = watch_path / "graphify-out" / "graph.json"
        if not graph_json.exists():
            print(f"error: no graph found at {graph_json} — run /graphify first", file=sys.stderr)
            sys.exit(1)
        from networkx.readwrite import json_graph as _jg
        from graphify.build import build_from_json
        from graphify.cluster import cluster, score_all
        from graphify.analyze import god_nodes, surprising_connections, suggest_questions
        from graphify.report import generate
        from graphify.export import to_json, to_html
        print("Loading existing graph...")
        _raw = json.loads(graph_json.read_text(encoding="utf-8"))
        G = build_from_json(_raw)
        print(f"Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
        print("Re-clustering...")
        communities = cluster(G)
        cohesion = score_all(G, communities)
        gods = god_nodes(G)
        surprises = surprising_connections(G, communities)
        labels = {cid: f"Community {cid}" for cid in communities}
        questions = suggest_questions(G, communities, labels)
        tokens = {"input": 0, "output": 0}
        report = generate(G, communities, cohesion, labels, gods, surprises,
                          {"warning": "cluster-only mode — file stats not available"},
                          tokens, str(watch_path), suggested_questions=questions)
        out = watch_path / "graphify-out"
        (out / "GRAPH_REPORT.md").write_text(report, encoding="utf-8")
        to_json(G, communities, str(out / "graph.json"))
        to_html(G, communities, str(out / "graph.html"), community_labels=labels or None)
        print(f"Done — {len(communities)} communities. GRAPH_REPORT.md, graph.json and graph.html updated.")

    elif cmd == "update":
        watch_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(".")
        if not watch_path.exists():
            print(f"error: path not found: {watch_path}", file=sys.stderr)
            sys.exit(1)
        from graphify.watch import _rebuild_code
        print(f"Re-extracting code files in {watch_path} (no LLM needed)...")
        ok = _rebuild_code(watch_path)
        if ok:
            print("Code graph updated. For doc/paper/image changes run /graphify --update in your AI assistant.")
        else:
            print("Nothing to update or rebuild failed — check output above.", file=sys.stderr)
            sys.exit(1)

    elif cmd == "navigate" or cmd == "nav":
        from graphify.navigate import navigate, DEFAULT_GRAPH_PATH, LIST_LIMIT
        # Parse: --graph PATH, --session <id>, --no-session, --json|--format json,
        # --include-inferred, --extracted-only (no-op alias for back-compat),
        # --min-confidence FLOAT, --legend, --no-ops-hint, --limit N.
        # Remaining args = op chain.
        args = sys.argv[2:]
        graph_path: str | None = None
        session: str | bool = True  # True = ephemeral with auto-id
        fmt = "text"
        extracted_only = True   # default: AST ground truth only
        min_confidence: float | None = None
        show_legend = False
        show_ops_hint = False
        # `None` means "use navigate()'s defaults" (which differ per pivot:
        # standard listings get LIST_LIMIT, `coc` gets the smaller
        # COC_LIST_LIMIT_DEFAULT). Setting an int here bypasses both.
        limit: int | None = None
        kinds: set[str] | None = None
        node_kinds: set[str] | None = None  # --node-kind filter on node_kind attr
        bodies: int | None = None
        # Lap-27 #7: depth=None means "user didn't pass --depth". navigate()
        # resolves to the per-verb default (1 for in/out, 3 for the
        # dependents/dependencies sugar). Explicit values — including 1 —
        # are honored as-is, so `--depth=2` no longer gets clamped to 3
        # by the dependents transitive default.
        depth: int | None = None
        archived_mode: str = "all"  # --no-archived → "no" / --archived-only → "only"
        include_files: bool = False  # --include-files turns coc back on for file hubs
        code_only: bool = False  # --code-only filters rationale nodes from coc
        collapse_dupes: bool = True   # --no-collapse expands dupe-label groups
        explain_cost: bool = False    # --explain-cost short-circuits pivots to size preview
        md: bool = False              # --md wraps labels and src:line in markdown links
        transitive: bool = False      # --transitive routes script-leaf out through contains
        show_session: str | None = None  # --show-session <id> renders saved cursor without mutating it
        quiet_hints: bool = False  # --quiet-hints suppresses all hint lines
        i = 0
        ops: list[str] = []
        # `--help` / `-h` mid-args takes precedence over op parsing — without
        # this the `else: ops.append(a)` branch swallows it as a navigate op.
        if any(a in ("-h", "--help") for a in args):
            _print_subcmd_help("navigate")
            return
        while i < len(args):
            a = args[i]
            if a == "--graph" and i + 1 < len(args):
                graph_path = args[i + 1]; i += 2
            elif a.startswith("--graph="):
                graph_path = a.split("=", 1)[1]; i += 1
            elif a == "--session" and i + 1 < len(args):
                session = args[i + 1]; i += 2
            elif a.startswith("--session="):
                session = a.split("=", 1)[1]; i += 1
            elif a == "--no-session":
                session = False; i += 1
            elif a in ("--json",):
                fmt = "json"; i += 1
            elif a == "--format" and i + 1 < len(args):
                fmt = args[i + 1]; i += 2
            elif a.startswith("--format="):
                fmt = a.split("=", 1)[1]; i += 1
            elif a == "--include-inferred":
                extracted_only = False; i += 1
            elif a == "--extracted-only":
                # back-compat alias: was the opt-in flag, is now the default
                extracted_only = True; i += 1
            elif a == "--min-confidence" and i + 1 < len(args):
                min_confidence = float(args[i + 1]); i += 2
            elif a.startswith("--min-confidence="):
                min_confidence = float(a.split("=", 1)[1]); i += 1
            elif a == "--limit" and i + 1 < len(args):
                limit = int(args[i + 1]); i += 2
            elif a.startswith("--limit="):
                limit = int(a.split("=", 1)[1]); i += 1
            elif a == "--kind" and i + 1 < len(args):
                kinds = {k.strip() for k in args[i + 1].split(",") if k.strip()}
                i += 2
            elif a.startswith("--kind="):
                kinds = {k.strip() for k in a.split("=", 1)[1].split(",") if k.strip()}
                i += 1
            elif a == "--node-kind" and i + 1 < len(args):
                node_kinds = {k.strip() for k in args[i + 1].split(",") if k.strip()}
                i += 2
            elif a.startswith("--node-kind="):
                node_kinds = {k.strip() for k in a.split("=", 1)[1].split(",") if k.strip()}
                i += 1
            elif a == "--bodies" and i + 1 < len(args):
                bodies = int(args[i + 1]); i += 2
            elif a.startswith("--bodies="):
                bodies = int(a.split("=", 1)[1]); i += 1
            elif a == "--depth" and i + 1 < len(args):
                depth = max(1, int(args[i + 1])); i += 2
            elif a.startswith("--depth="):
                depth = max(1, int(a.split("=", 1)[1])); i += 1
            elif a == "--legend":
                show_legend = True; i += 1
            elif a == "--ops-hint":
                show_ops_hint = True; i += 1
            elif a == "--no-ops-hint":
                # back-compat no-op: was the opt-out flag, is now the default.
                show_ops_hint = False; i += 1
            elif a == "--no-archived":
                archived_mode = "no"; i += 1
            elif a == "--archived-only":
                archived_mode = "only"; i += 1
            elif a == "--include-files":
                include_files = True; i += 1
            elif a == "--code-only":
                code_only = True; i += 1
            elif a == "--no-collapse":
                collapse_dupes = False; i += 1
            elif a == "--explain-cost":
                explain_cost = True; i += 1
            elif a == "--md":
                md = True; i += 1
            elif a == "--transitive":
                transitive = True; i += 1
            elif a == "--show-session" and i + 1 < len(args):
                show_session = args[i + 1]; i += 2
            elif a.startswith("--show-session="):
                show_session = a.split("=", 1)[1]; i += 1
            elif a == "--quiet-hints":
                quiet_hints = True; i += 1
            else:
                ops.append(a); i += 1
        out = navigate(
            ops,
            graph_path=graph_path or DEFAULT_GRAPH_PATH,
            session=session,
            fmt=fmt,
            extracted_only=extracted_only,
            min_confidence=min_confidence,
            show_legend=show_legend,
            show_ops_hint=show_ops_hint,
            limit=limit,
            kinds=kinds,
            node_kinds=node_kinds,
            bodies=bodies,
            depth=depth,
            archived_mode=archived_mode,
            include_files=include_files,
            code_only=code_only,
            collapse_dupes=collapse_dupes,
            explain_cost=explain_cost,
            md=md,
            transitive=transitive,
            show_session=show_session,
            quiet_hints=quiet_hints,
        )
        print(out)

    elif cmd == "diff":
        if len(sys.argv) < 4:
            print("Usage: graphify diff <old-graph.json> <new-graph.json>", file=sys.stderr)
            sys.exit(1)
        old_path = Path(sys.argv[2]).resolve()
        new_path = Path(sys.argv[3]).resolve()
        for p in (old_path, new_path):
            if not p.exists():
                print(f"error: file not found: {p}", file=sys.stderr)
                sys.exit(1)
            if p.suffix != ".json":
                print(f"error: expected a .json file: {p}", file=sys.stderr)
                sys.exit(1)
        try:
            import networkx as _nx
            from networkx.readwrite import json_graph as _jg
            def _load_graph(p: Path) -> _nx.Graph:
                raw = json.loads(p.read_text(encoding="utf-8"))
                # Mirror `edges` → `links` so node_link_graph(..., edges="links")
                # accepts graphs saved by newer NetworkX (upstream PR #768).
                if "links" not in raw and "edges" in raw:
                    raw = dict(raw, links=raw["edges"])
                try:
                    return _jg.node_link_graph(raw, edges="links")
                except TypeError:
                    return _jg.node_link_graph(raw)
            G_old = _load_graph(old_path)
            G_new = _load_graph(new_path)
        except Exception as exc:
            print(f"error: could not load graphs: {exc}", file=sys.stderr)
            sys.exit(1)
        from graphify.analyze import graph_diff as _graph_diff
        diff = _graph_diff(G_old, G_new)
        print(f"Summary: {diff['summary']}")
        if diff["new_nodes"]:
            print(f"\nNew nodes ({len(diff['new_nodes'])}):")
            for n in diff["new_nodes"]:
                print(f"  + {n['label']} ({n['id']})")
        if diff["removed_nodes"]:
            print(f"\nRemoved nodes ({len(diff['removed_nodes'])}):")
            for n in diff["removed_nodes"]:
                print(f"  - {n['label']} ({n['id']})")
        if diff["new_edges"]:
            print(f"\nNew edges ({len(diff['new_edges'])}):")
            for e in diff["new_edges"]:
                print(f"  + {e['source']} --[{e['relation']}]--> {e['target']}")
        if diff["removed_edges"]:
            print(f"\nRemoved edges ({len(diff['removed_edges'])}):")
            for e in diff["removed_edges"]:
                print(f"  - {e['source']} --[{e['relation']}]--> {e['target']}")
    elif cmd == "benchmark":
        from graphify.benchmark import run_benchmark, print_benchmark
        graph_path = sys.argv[2] if len(sys.argv) > 2 else "graphify-out/graph.json"
        # Try to load corpus_words from detect output
        corpus_words = None
        detect_path = Path(".graphify_detect.json")
        if detect_path.exists():
            try:
                detect_data = json.loads(detect_path.read_text(encoding="utf-8"))
                corpus_words = detect_data.get("total_words")
            except Exception:
                pass
        result = run_benchmark(graph_path, corpus_words=corpus_words)
        print_benchmark(result)
    else:
        print(f"error: unknown command '{cmd}'", file=sys.stderr)
        print("Run 'graphify --help' for usage.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
