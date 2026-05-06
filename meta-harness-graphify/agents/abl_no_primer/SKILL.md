# Graphify Navigator

`graphify` is your primary code-navigation surface. It's a queryable knowledge graph of the repository — every function, class, method, and the edges between them — that lets you understand structure, find symbols, and trace call relationships at a fraction of the token cost of `read_file` + `grep`.

## When graphify wins vs. read_file (R3 calibration)

- **graphify wins** when ≤2 bodies are needed, structure-only answers (shape, summarize, community-of), cross-file disambiguation of same-name symbols, or who-uses-X / dead-end tracing across the call graph.
- **read_file wins** when comprehending most of a file's bodies (>~200 ln total) or line-by-line questions (formatting, surrounding context). graphify still primes the right offset — `shape` then targeted `read_file` beats unprimed reading.
- **graphify ties read_file** at 3-5 medium bodies. Pick by what's already on hand. Don't burn calls scouting for a marginal win.

## When to reach for `graphify search` instead of `grep`

- **About to chain `Grep` / `Glob` calls to trace a call graph or find who-uses-X.** That's literally what `graphify navigate` `in` / `out` / `path` are for.
- **About to grep for a string in source code.** Reach for `graphify search "<pattern>"` over `grep -n` when (a) you don't already know the containing symbol, or (b) you want to see parallel definitions across the repo. Search returns hits with symbol attribution (label, file:line, container, community) and ±1 line of context, repo-wide by default. Use raw `grep` for non-source files (markdown, JSON, configs) and right after recent edits when the graph is stale.

## First-call hygiene

Run `graphify update` once if `graphify-out/graph.json` doesn't exist or might be stale. The command is fast. graphify itself will surface a `!stale` banner if any indexed source file's mtime is newer than the graph — trust it.

## Verb cheat-sheet

| Verb | Use for |
|------|---------|
| `graphify navigate "@<label>"` | focus a symbol; frontier shows callers / callees / methods / containers, cursor persists for chains. |
| `graphify navigate <op>` | apply pivot from current frontier — `in`, `out`, `methods`, `contains`, `coc` (community), `wu` (where-used), `inh`, `siblings`, `dead-ends`, `read`. |
| `graphify navigate "[N]"` | focus on Nth row of last listing. |
| `graphify peek "<symbol>"` | one-shot body read, no cursor mutation. Accepts `Class.method`. |
| `graphify shape "<file>"` | structure summary: N classes / M fns / longest fn / entry points (top fns by external in-edges). Cheaper than reading. |
| `graphify search "<pattern>"` | body-text grep across all nodes; returns `(label, file:line, container, community, degree)`. |
| `graphify locate <s1> <s2>...` | multi-symbol file:line lookup in one call. |
| `graphify path "<A>" "<B>"` | shortest path between two concepts. |
| `graphify explain "<symbol>"` | one-shot plain-language explanation. |
| `graphify query "<question>"` | broad BFS context — use *after* navigate has narrowed scope. |
| `graphify summarize <file/dir>` | aggregate shape + entry points across multiple files. |
| `graphify changed --since <ref>` | what's changed in graph terms since a git ref. |

## Reading graphify output

- **Per-row metadata**: `· g3d · 482ln` = git-mtime age (3 days) + line count. `!stale` marker = file changed but graph wasn't rebuilt. `[depth≤N]` = transitive walk depth.
- **Hint footers name the structural cause** when output could be misread — e.g. "0 callers but parent class is referenced — try `wu`". They only fire when a naive read of the output would miss something. Trust them.
- **Omission counts are never silent**: `+N more`, `+N INFERRED hidden`, `+N hidden via --node-kind`. When you see one, the named flag would unhide. Use `--explain-cost` to preview a listing's byte cost before committing.
- **First row is usually the right one.** For cross-file same-name symbols, the disambiguation listing returns path-qualified labels (`@graphify/__main__.py/main`); pass the qualified form to lock onto the right one.
- **Edge confidence**: edges are tagged EXTRACTED (AST-derived, trustworthy), INFERRED (heuristic, gated by `--min-confidence`), or AMBIGUOUS. Default views show only EXTRACTED.

## Skip graphify for

Non-code files (`.json`, `.yaml`, `.toml`, `.csv`, `.md`, `.txt`, `.log`, lockfiles). graphify only indexes source. Use `read_file` directly.
