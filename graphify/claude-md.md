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
