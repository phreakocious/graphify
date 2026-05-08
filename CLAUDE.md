# CLAUDE.md

For agents (and humans) working on graphify itself — design preferences for the
navigator surface (`graphify navigate / peek / doc / search / shape / path / changed`).
For runtime architecture see [ARCHITECTURE.md](ARCHITECTURE.md). For per-corpus
behavior see the user-facing skill (`skill.md` and friends).

## Audience inversion

The reader is a coding agent. Not the maintainer's screen. Optimize for the
agent's context window and the questions they'll ask next, not the
human-glanceable view. A dense one-screen summary that pre-answers three
follow-up questions beats a clean header that forces three more calls.

## graphify isn't a small-task tool — it's a primer

The naive frame is "graphify when the answer is small, Read when it's big."
That undersells the edge. **graphify-first works on big tasks too**: a `shape`
call (or `navigate @entry`) before any Read primes the agent with structural
pivot data — entry points, callers, hub identity, line ranges — that informs
every Read decision that follows. The orientation cost is small; the
compounding savings across follow-up calls are large.

When you ship a feature here, ask: does this make graphify a better *primer*?
Surfacing precise line ranges, entry points by external in-edges,
class-method curated dumps, multi-symbol `locate` — these all reduce the cost
of "graphify first even when the task is large."

### Honest decision rule (R3 sub-agent feedback)

The sibling-agent head-to-head test in lap-21 R3 produced this calibration:

- **graphify wins** when ≤2 bodies are needed, structure-only answers
  (shape/summarize/coc), cross-file disambiguation of same-name symbols,
  or who-uses-X / dead-end tracing across the call graph.
- **Read wins** when comprehending most of a file's bodies (>~200 ln total)
  or line-by-line questions (formatting, surrounding context). graphify still
  primes the right offset — `shape` then targeted Read beats unprimed Read,
  and beats peek-as-scout when you'll end up reading the file anyway.
- **graphify ties Read** when the answer needs 3-5 medium bodies. Either
  approach works; pick by what's already on hand. Don't burn calls scouting
  for a marginal win.

The R3 worry that didn't pan out: free-function disambiguation works the
same as class-method disambiguation (verified against `main` colliding
across 11 files in this repo). Path-qualified resolution
(`@graphify/__main__.py/main`) lands cleanly without grep fallback.

## Token / context preservation

- **Pre-answer follow-ups, don't gate them.** A pivot listing carries
  `degree`, `community`, file:line, mtime, line count, kind tag — the
  agent shouldn't have to do `peek` just to see how big a thing is.
  See `_node_summary` and `_meta_tag` in `navigate.py`.
- **Cap one-screen output, count what was hidden.** `--limit` truncates
  with `+N more`. Search truncation gets a top banner when >50% hidden so
  the agent narrows the regex *before* iterating on the wrong top hits.
- **Preview before commit.** `--explain-cost` returns
  `would-show N nodes ≈ K bytes` instead of rendering the listing —
  lets the caller gate `coc` on a 1000-member community before
  spending the tokens.
- **No rendering by default for the cursor side-effects.** `peek` is
  cursor-free read-only; `navigate ... read` mutates session. Different
  semantics, kept distinct on purpose. Don't unify.
- **Respect the budget.** Default `--bodies` is off; `--context` defaults
  to 1; `coc` has a smaller default limit than other listings (large
  communities are the common case). Agents who want more pass the flag.
- **Fuse the call sequence, not just the tokens.** The biggest wins in
  lap-22→25 weren't on per-call output size — they were on collapsing
  N calls into one. `blast @<symbol>` returns callers + callees
  side-by-side (lap-22: −47% calls on EGF). `summarize @<Class>` fuses
  sig + methods + cross-file callers + inheritance (lap-23b: −31% calls
  on Klein). `peek @C.{m1,m2,m3}` brace-expands to N method bodies in
  one pass (lap-25). When you ship a verb here, ask: does this collapse
  a chain the agent was about to do anyway?

## Anti-guessing: hints over silence

When output could be misread, name the cause. The pattern is `_emit_hint`
in `navigate.py`. Examples that already ship:

- `drill_via_contains` — file with no direct edges has its calls one hop
  in via `contains`. Says so, names the next op.
- `class_shape_via_methods` — class has 0 direct callers but methods get
  called from many sites. Without the hint, agents read `↗in(0)` as
  "nothing uses this."
- `closure_iface_dispatch` — method-shaped focus with 0 EXTRACTED but
  inferred edges available. Names dynamic dispatch as the cause and
  points at `--include-inferred --min-confidence 0.85`.
- `method_no_callers_try_wu` (lap-21 #2) — function with 0 callers AND
  0 inferred but parent class is referenced; typed-receiver dispatch is
  the likely cause; suggest `wu` (where-used).
- Stale-graph banner — `_check_graph_freshness` compares graph.json
  mtime to indexed source files; banner fires on every `load_graph`
  when stale. Was the user's #1 leverage item: silent staleness was the
  failure mode driving `peek` to wrong line numbers.
- `--transitive` no-op notice — when the flag silently degrades (focus
  is a symbol, or file already has direct out), surface a `note:` line
  so the agent doesn't wonder whether the flag did anything.

When you add a hint, the rule is: **fires only when the output is
actually misleading without it**, and **names the structural cause**,
not just the symptom. Use `_emit_hint` so cursor-side bookkeeping
(`hints_emitted`) suppresses repeats within a session.

## Surface real data, not labels

Agents need ground truth to plan and to commit. Numbers beat words.

- `source_location` is `L<start>-<end>` (TS extractor stamps the actual
  end via `node.end_point[0] + 1`). `peek` and `doc` use this to walk
  the body precisely; `shape` uses it to pick the longest fn.
- `--md` renders labels as `[label](src:line)` so IDE/Claude-Code
  surfaces a clickable link.
- `_meta_tag` appends compact `· g3d · 482ln` (git-mtime age + line
  count) to listing rows. `g3d` = "last commit 3 days ago"; `!stale`
  marker when file mtime > graph.json mtime.
- `shape <file>` surfaces an `entry points:` line — top-3 fns ranked by
  external (cross-file, non-structural) in-edges. Lands the agent on
  the API surface before they read any code. Each fn carries a `×N`
  marker for cross-file callers and pins past `--limit` so a public
  fn at the file's tail isn't hidden inside `+N more` (lap-24/26).
  On CLI-script files with no external callers, falls back to
  `entry points: L<n> [from __main__] / [top-level] / [shebang]` so
  investigation scripts stop reading as dead-end leaves (lap-27).
- `.md` / `.mdx` files index as first-class nodes (file/heading/code-block).
  Backtick-quoted tokens that resolve to code symbols become `references`
  edges — `wu @MyClass` returns call sites *and* doc mentions in one
  pass; `search "<pat>"` matches code bodies *and* markdown bodies.
  Eliminates the "where is this discussed?" grep fallback (lap-27).
- `script_kind` tag on Python / JS / TS file nodes — `main_block`
  (canonical `if __name__ == "__main__":` / `require.main` /
  `import.meta.main`), `top_level` (bare-identifier call to an
  own-defined fn — the no-clunky-main investigation style), or
  `shebang` only. Frontier renders `· script:main` / `· script:tl`
  / `· script:sh` so the agent knows a file is runnable before
  deciding to read it as library or entry point (lap-27).
- `[depth≤N]` tag on listings produced by `_transitive_walk` so agents
  can tell whether a `--depth=3` walk fanned out or dead-ended at hop 1.

## Omission counts: never silent

The rule (see `feedback_omission_counts` in memory): every filter that
hides items surfaces the count. Inline format on the frontier:
`+Ninf` (inferred), `+Nlc` (low confidence), `+Nxl` (cross-language),
`+Nkind` (--kind-filtered), `+Narch` (archived). Listing format:
`+N INFERRED hidden`, `+N hidden via --node-kind`, etc. with the flag
that would unhide them named explicitly.

When you add a new filter, add a drop bucket. Don't ship a quiet drop.

## Cache discipline

Per-file extraction cache is keyed on `AST_CACHE_VERSION` in
`extract.py`. **Any change to extracted node identities, labels, edge
relations, or `source_location` format MUST bump the version in the same
commit.** A missed bump causes `graphify update` to reuse stale cache
cells and present a fix as "NOT FIXED" (lap-20a→20b incident). The
constant lives at the top of `extract.py` with a version-history
comment block — add an entry whenever you bump.

Exception: changes that only run at MERGE time (e.g., phantom-node
resolution, markdown-`references`-edge resolution) operate over
cached output and don't need a bump.

## Hint vs auto-act vs default change

Three escalation levels. Pick the lowest one that solves the friction:

1. **Hint** — output stays the same, a footer/note line tells the
   agent what to do next. Good when the existing data is correct
   but easy to misread. (Most lap-21 #2 work was this level.)
2. **Auto-act** — fold the next step into the current call (e.g.,
   `_listing_data` filtering external nodes by default). Good when
   the next step is unambiguous and reversible by a flag.
3. **Default change** — flip a flag's default, change a verb's
   semantics. Reserve for cases where the *current* default is
   misleading enough to be load-bearing wrong. Discuss before
   shipping; document the migration in the commit and in
   `navigate_v3_backlog.md`.

The `default path → --edges calls` proposal is held at level 3 because
it would break "find any connection" use cases — discuss before
flipping.

## Style

- **Comments explain *why*, not *what*.** A comment that says
  "increment counter" is noise; a comment that says "increment counter
  *because* the producer drops back to 0 mid-pass and we need the
  delta for the omission line" is load-bearing.
- **Reference incidents in comments when the code is non-obvious.**
  Most surprising code in `navigate.py` carries a `Lap-N field-report
  fix:` preamble naming the specific friction the fix addressed. This
  is the single best signal for "should I refactor this?" — refactors
  that lose the lap-anchor risk reintroducing the friction.
- **Tests are field reports too.** When a fix lands, the test usually
  has a docstring describing the original failure mode (`peek
  returned 6 args-only lines for a 41-line fn`). Keep that — future
  refactors against this code lean on it to know what mode the test
  is exercising.
- **Don't refactor away the friction names.** `wu` looks unidiomatic
  vs `where_used`; the short form is deliberate so chains
  (`@X wu --kind=calls`) stay readable. Same for `coc`, `inh`, `sib`.

## When in doubt

The hierarchy of authority for design decisions:

1. **What an agent in the wild actually did wrong** (field reports >
   speculation). The lap commits in `navigate_v3_backlog.md` are the
   record.
2. **What the agent would have to type to recover** (good error
   messages name the next op).
3. **What the maintainer would prefer** (this doc).

If a field report contradicts this doc, the field report wins —
update the doc.

# IMPORTANT: we are dogfooding. friction should be surfaced so it can be addressed!  this is our project's CLAUDE.md contents:

@.claude/graphify.md

