## graphify

This project has a graphify knowledge graph at `graphify-out/graph.json`. Use it to keep your context window from collapsing under a heavy codebase.

### When to reach for graphify

Before any of these moves, scout the graph first — it's 50–500x cheaper than the alternative:

- **Reading a source file you don't know.** `graphify shape "<file>"` or `graphify navigate "@<symbol>"` first — the frontier shows whether it's a leaf, hub, or router.
- **Tracing a call graph or who-uses-X.** That's `navigate in / out / wu / path`.
- **String search in source.** `graphify search "<pat>"` over `grep -n` — hits come attributed to symbol (label, file:line, container, community) with ±1 line of context.
- **One-function reminder.** `graphify peek "<symbol>"` — one-shot body dump, no cursor, no session.
- **Implementing in unfamiliar code.** Map the blast radius first — focus the entry point, run `in --depth=2 --kind=calls` to see callers two hops out.
- **Don't know where to start.** `graphify navigate "@<best-guess>"` is a free probe — a hit returns a frontier, a miss returns real names you can grab onto.

### When NOT to reach for graphify

Skip graphify when reading raw content of `.json` / `.yaml` / `.toml` / `.csv` / `.txt` / `.log` / lockfiles / build output / scratch files — graphify doesn't index their content. Also when you want to absorb prose flow in a `.md`/`.mdx` you'll read end-to-end: `Read` is right for that. graphify is for finding *where* something is mentioned, not for absorbing a document.

### graphify indexes markdown too

`.md` and `.mdx` are first-class file/heading/code-block nodes. Backtick refs to code symbols (`MyClass`, `compile()`) become `references` edges. So `navigate "@MyClass" wu` surfaces source call sites *and* doc mentions in one call; `search "<pat>"` matches code bodies AND markdown bodies. Kills the "where is this discussed?" grep fallback.

### Don't bail after one empty graphify call

Empty `search` is usually a regex/casing miss — try `shape <file>` or `navigate "@<best-guess>"` before falling back to grep. `@`-targets do prefix → substring → fuzzy fallback automatically; the trust-signal line in the output names which mode matched. One miss isn't a signal to switch tools.

### Verbs

```
graphify navigate "@<symbol>"                       # focus + frontier (~200 tok)
graphify navigate "@<symbol>" methods 6 in          # chain: focus, list methods, pick 6th, show callers
graphify peek "<symbol>"                            # one-shot body dump, no cursor (brace-expand: @C.{m1,m2,m3})
graphify shape "<file>" [<file> ...]                # N classes / M fns / longest fn (multi-target + brace-expand `{a,b}.py`)
graphify summarize "@<Class>"                       # sig + methods + cross-file callers + inheritance — one call instead of read_file on a huge class
graphify blast "<symbol>"                           # callers + callees side-by-side, cursor-free (brace-expand: @C.{m1,m2})
graphify search "<pattern>"                         # body-text grep, hits attributed to symbol (matches code AND markdown)
graphify search "<pat>" --files-only                # grep -l analog: one row per file with match count (no per-line snippets)
graphify search "<pat>" --in-files "<glob>"         # grep -r --include analog: restrict to source_files matching glob
graphify files "<glob>"                             # list source-file nodes by basename or path glob (find -name analog)
graphify scripts                                    # files runnable as CLI: `scripts "<glob>"` or `--kind main|tl|sh`
graphify locate <s1> <s2> ...                       # multi-symbol file:line, no body
graphify doc "<symbol>"                             # signature + docstring/rationale dump (brace-expand: @C.{m1,m2})
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

### Sessions and the cursor

`navigate` is cursor-based — chains compose left-to-right (`navigate "@X" methods 6 in` is 4 ops in one call; the cursor walks each step). A `session: <id>` line appears at the bottom **only when chaining is live** — cursor walked >1 step, chain paused on a disambig, or `--session` was passed. Resume with `--session <id>`. One-shot focus calls stay quiet. Cursor files live at `graphify-out/.navigate/<id>.json` (per-id, so parallel calls don't race); swept after 30 min.

Sibling verbs (`peek` / `blast` / `summarize` / `shape` / `search` / `locate` / `doc`) are **cursor-free by design** — no session, no side effects. Use them when you want a one-shot answer without committing to a chain.

### Dispatching sub-agents (Agent / Explore / Plan)

Sub-agents run with their own system prompts that hard-code `find` / `grep` / `glob` workflows. **They do not reliably inherit graphify guidance from CLAUDE.md** — verified A/B: an Explore sub-agent in a graphify-indexed project ran 30 `find` / `grep` / `awk` / `head` calls for a question graphify answers in 2 (`shape <file>` then `navigate "@<symbol>"`).

When you dispatch a sub-agent in this directory, paste this into the prompt:

> This project has a graphify knowledge graph at `graphify-out/graph.json`. Reach for graphify BEFORE find/grep when the question is about code structure:
> - Where is X defined? → `graphify locate X` (multi-symbol: `locate X Y Z`)
> - What's in this file? → `graphify shape <file>` (multi: `shape f1.py f2.py`)
> - Which files match a pattern? → `graphify files "<glob>"` (e.g. `*test*.py`, `tools/*.py`)
> - Which files run as CLI scripts? → `graphify scripts` (or `--kind main|tl|sh`)
> - Tell me about class X → `graphify summarize "@X"`
> - Who calls X? → `graphify navigate "@X" in --kind=calls`
> - Who uses X (incl. typed dispatch + doc mentions)? → `graphify navigate "@X" wu`
> - What strings match? → `graphify search "<pat>"` — add `--idents` for cross-casing, `--files-only` for grep-l, `--in-files "<glob>"` for grep-r --include
> - What's in this directory? → `graphify navigate "@<dir>/"` (trailing slash matters)
>
> **graphify indexes `.md`/`.mdx` too**, not just code. `wu` surfaces doc mentions; `search` matches markdown bodies.
>
> **Don't bail after one empty graphify call.** Empty `search` is usually a regex/casing miss — try `shape <file>` or `navigate "@<best-guess-symbol>"` before falling back to grep. graphify is 50–500x cheaper than chained find/grep when the question is about source-code structure.

Without this, the sub-agent burns ~20 calls on what graphify answers in 2–3.

### What NOT to do

- Don't read `GRAPH_REPORT.md` end-to-end — it's a 40KB+ overview that costs ~10K tokens and the community-list section is filler in AST-only mode. Use `graphify navigate` instead.
- Don't run `graphify query` on a question you haven't narrowed yet — it caps at ~2K tokens of flat node listings, mostly noise. Narrow with `navigate` first.
