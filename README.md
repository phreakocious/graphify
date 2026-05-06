# graphify (navigator fork)

> **Experimental fork** of [safishamsi/graphify](https://github.com/safishamsi/graphify).
> Built by Claude, for Claude. Not maintained for community use.

This fork started as a private workspace for adding a cursor-based navigation
surface (`graphify navigate`) to graphify's existing extract → build → graph
pipeline, then grew into a small collection of bug fixes and quality-of-life
improvements that haven't landed upstream.

## Why a fork (not a PR)

Upstream graphify is actively developed but has a low merge rate on
contributed PRs — substantive third-party PRs (interface-method dispatch,
type-aware resolvers, build-merge safety) routinely sit open without review.
Submitting our work for upstream merge would block on attention that doesn't
come.

So this fork takes a different posture: **consume what's useful from
upstream** (cherry-pick valuable PRs, both merged and open), **add what
upstream is missing** (the navigator, plus targeted fixes for issues that
have explicit follow-up notes nobody picked up), and **don't expect to
contribute back productively**.

This is not a hostile fork. We track upstream and pull from it regularly.
We just don't run our work through their review queue.

## What's different from upstream/v4

### Original work

- **`graphify navigate`** — cursor-based affordance frames over `graph.json`,
  designed for LLM agents (Claude, Codex, etc.) to walk a code graph cheaply.
  Returns dense pivots (in/out/methods/coc/parent/etc.) instead of large
  text blobs. ~4000 lines in `graphify/navigate.py` plus a 100KB+ test suite
  in `tests/test_navigate.py`. See the verb table in `graphify/skill.md`.
- **`graphify/resolve.py`** — label resolution and ranking extracted as a
  standalone module. Any tool reading `graphify-out/graph.json` can resolve
  `@<label>` queries the same way navigate does.
- **`graphify peek` / `shape` / `search`** — additional CLI verbs for
  one-shot body reads, file structure summaries, and body-text grep with
  node attribution.
- **Rationale-edge sanitizer** in `build_from_json`. LLM extractors
  routinely invert the rationale relationship — emit
  `rationale --uses--> Class` instead of `Class --rationale_for--> rationale`.
  We drop those at build time. Real-world impact: 48% edge reduction on
  one Python corpus, EXTRACTED rate 40% → 77%. Upstream PR #576 noted this
  as an explicit follow-up; no upstream PR has done it.
- **Per-node hint adaptation** in navigator. Hints for `interface_kind`,
  `closure_iface_dispatch`, etc. only mention `--kind=impl_of` or
  `--kind=type_ref` when the focused node actually has those edges —
  no more sending agents to empty results on structurally-typed corpora.
- **Phantom-resolver edge-wipe fix**, imports-to-external stub creation,
  cross-file edge confidence grading, and a long tail of small extractor
  fixes that came out of running the navigator on real corpora.

### Cherry-picked from upstream

We pull both **merged** PRs (when they touch areas we care about and v4
doesn't have them yet) and **open** PRs that look stable. Each pick gets
verified against our test suite and validated on real corpora before
landing. Some examples:

- **#594** — wiki: sanitize Windows-reserved characters in filenames
- **#599** — prevent cross-file member-call name collisions
- **#662** — TS/JS named-import edges + tsconfig path alias resolution
- **#708** — TS interface/enum/type_alias as graph nodes + `new_expression` edges
- **#158** + **#164** — `graphify diff` CLI + public API
- **#576** (partial) — rationale-leak fix in cross-file resolvers

See `git log` for the full picked commit set.

### What we don't take

- Refactors that conflict with the navigator-side architecture
  (e.g., #403, a 4000-line restructure)
- Language additions for languages no consumer of this fork uses
- Platform/install integrations (Cursor, Trae, etc.) — we use it via CLI
- LSP-based extractors (#457) — quality regression risk on our test corpora

## Posture

- **Not maintained for community use.** We don't accept PRs. We may
  review issues but won't promise to act on them. If you want a maintained
  graphify, use [upstream](https://github.com/safishamsi/graphify).
- **Expect divergence.** As upstream evolves, the merge surface grows.
  We pick what's valuable; we don't track every change.
- **No release cadence.** No version tags, no PyPI, no changelogs beyond
  `git log`. Use a specific commit if you need stability.
- **Quality bar is corpus-driven.** We measure improvements on real
  graphs (currently a TypeScript ML library and a Python research
  framework). Changes that don't move the needle on those corpora get
  skipped.

## Quick start

```bash
# Install in a venv from this fork
git clone https://github.com/phreakocious/graphify
cd graphify
python -m venv .venv
.venv/bin/pip install -e .

# Build a graph for a project
.venv/bin/python -m graphify update <path/to/your/project>

# Navigate
.venv/bin/python -m graphify navigate "@<some_symbol>"
.venv/bin/python -m graphify navigate "@<some_symbol>" methods 1 in
.venv/bin/python -m graphify navigate --help

# Diff two graph snapshots
.venv/bin/python -m graphify diff old.json new.json

# Other verbs
.venv/bin/python -m graphify peek "<symbol>"
.venv/bin/python -m graphify shape "<file>"
.venv/bin/python -m graphify path "<source>" "<target>"
.venv/bin/python -m graphify explain "<symbol>"
```

For the broader graphify feature set (extraction, clustering, wiki
generation, semantic embeddings, IDE skill installation), see
[upstream's README](https://github.com/safishamsi/graphify).

## Repo layout

| Path | What |
|---|---|
| `graphify/navigate.py` | Cursor-based navigator (~4000 lines) |
| `graphify/resolve.py` | Label resolution + ranking module |
| `graphify/extract.py` | Tree-sitter extractors (heavily modified from upstream) |
| `graphify/build.py` | Graph construction + sanitization passes |
| `graphify/skill.md` | The skill file LLM agents load to learn the tool |
| `tests/test_navigate.py` | Navigator tests (~125KB) |

## Branch layout

- **`navigator`** — primary working branch. ~50 commits ahead of `upstream/v4`.
- **`v4`** — paranoia backup of `navigator` at the divergence point.
- **`origin/v7`** — stale snapshot of upstream/v7 (refresh manually).

Remotes:
- `origin` → `phreakocious/graphify` (this fork)
- `upstream` → `safishamsi/graphify` (canonical)

## Credit

The graphify CLI, extraction pipeline, and most language extractors are
from [Safi Shamsi](https://github.com/safishamsi)'s upstream. The
navigator and the divergent fixes in this fork are work done in
collaboration with Claude (Anthropic). Cherry-picked PR commits retain
their original authors.

## License

Same as upstream. See `LICENSE`.
