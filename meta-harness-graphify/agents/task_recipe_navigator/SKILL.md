# Graphify Navigator

`graphify` is your primary code-navigation surface. It's a queryable knowledge graph of the repository — every function, class, method, and the edges between them — that lets you understand structure, find symbols, and trace call relationships at a fraction of the token cost of `read_file` + `grep`.

## When graphify wins vs. read_file / grep (R3 calibration)

- **graphify wins** when ≤2 bodies are needed, structure-only answers (shape, summarize, community-of), cross-file disambiguation of same-name symbols, or who-uses-X / dead-end tracing across the call graph.
- **read_file wins** when comprehending most of a file's bodies (>~200 ln total) or line-by-line questions (formatting, surrounding context). graphify still primes the right offset — `shape` then targeted `read_file` beats unprimed reading.
- **graphify ties read_file** at 3-5 medium bodies. Pick by what's already on hand. Don't burn calls scouting for a marginal win.

**graphify-first as a primer.** Even on big tasks: a `shape` call (or `navigate @entry`) before any `read_file` primes you with structural pivot data — entry points, callers, hub identity, line ranges. The orientation cost is small; the compounding savings across follow-up calls are large.

## When to reach for `graphify search` instead of `grep`

- **About to chain `Grep` / `Glob` calls to trace a call graph or find who-uses-X.** That's literally what `graphify navigate` `in` / `out` / `path` are for.
- **About to grep for a string in source code.** Reach for `graphify search "<pattern>"` over `grep -n` when (a) you don't already know the containing symbol, or (b) you want to see parallel definitions across the repo. Search returns hits with symbol attribution (label, file:line, container, community) and ±1 line of context, repo-wide by default. Use raw `grep` for non-source files (markdown, JSON, configs) and right after recent edits when the graph is stale.

## First-call hygiene

Run `graphify update` once if `graphify-out/graph.json` doesn't exist or might be stale. The command is fast. graphify itself will surface a `!stale` banner if any indexed source file's mtime is newer than the graph — trust it.

## Task → recipe

Match the task you're doing to its recipe. The leftmost column is the question you're trying to answer; the right column is the verb sequence that gets you there cheapest.

| Task | Recipe |
|------|--------|
| **Get a file's structure** before reading it | `graphify shape "<file>"` — entry points (top fns by external in-edges), longest fn, line ranges. Cheaper than reading. |
| **Refactor blast radius** (callers + callees of one symbol) | `graphify blast "@<symbol>"` — markdown `## Callers` / `## Callees` sections in one call. Cursor-free. |
| **Who calls X** | `graphify navigate "@<X>"` then `graphify navigate in --kind=calls`. Cursor persists. |
| **What does X call** | `graphify navigate "@<X>"` then `graphify navigate out --kind=calls`. |
| **Disambiguate a same-name symbol** across files | `graphify navigate "@<name>"` returns a path-qualified disambig list; pass `@<file>/<path>/<name>` to lock onto one. |
| **Look up multiple symbols** in one call | `graphify locate <s1> <s2> <s3> ...` — file:line for each. |
| **Trace a call path** between two symbols | `graphify path "@<A>" "@<B>"` — shortest path. |
| **Read a body** without mutating cursor | `graphify peek "<symbol>"`. Accepts `Class.method`. |
| **Find dynamic dispatch** (method shows 0 callers) | `graphify navigate "@<method>"` then `wu` (where-used) — covers typed-receiver dispatch the call graph misses. |
| **Find a string in source code** | `graphify search "<pattern>"` — body-text grep with symbol attribution. Use raw `grep` for non-source files. |
| **What's changed since a git ref** | `graphify changed --since <ref>` — what's changed in graph terms. |
| **Aggregate shape across files/dirs** | `graphify summarize <file_or_dir>` — entry points across multiple files. |
| **Plain-language explanation** of one symbol | `graphify explain "<symbol>"`. |
| **Broad BFS context** around a question | `graphify query "<question>"` — use *after* navigate has narrowed scope. |
| **Focus on Nth row** of last listing | `graphify navigate "[N]"` (mid-chain pivot). |

## Reading graphify output

- **Per-row metadata**: `· g3d · 482ln` = git-mtime age (3 days) + line count. `!stale` marker = file changed but graph wasn't rebuilt. `[depth≤N]` = transitive walk depth.
- **Hint footers name the structural cause** when output could be misread — e.g. "0 callers but parent class is referenced — try `wu`". They only fire when a naive read of the output would miss something. Trust them.
- **Omission counts are never silent**: `+N more`, `+N INFERRED hidden`, `+N hidden via --node-kind`. When you see one, the named flag would unhide. Use `--explain-cost` to preview a listing's byte cost before committing.
- **First row is usually the right one.** For cross-file same-name symbols, the disambiguation listing returns path-qualified labels (`@graphify/__main__.py/main`); pass the qualified form to lock onto the right one.
- **Edge confidence**: edges are tagged EXTRACTED (AST-derived, trustworthy), INFERRED (heuristic, gated by `--min-confidence`), or AMBIGUOUS. Default views show only EXTRACTED.

## Skip graphify for

Non-code files (`.json`, `.yaml`, `.toml`, `.csv`, `.md`, `.txt`, `.log`, lockfiles). graphify only indexes source. Use `read_file` directly.
