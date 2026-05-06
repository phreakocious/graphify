# Graphify Navigator (Baseline)

You have access to `graphify`, a CLI that exposes a queryable knowledge graph of the working repository's code. Use it as your primary entry point for code navigation — prefer it over read_file / grep when you need to:

- Find a function/class/method by name → `graphify navigate <name>`
- Walk callers/callees of a symbol → `graphify navigate <name> --edges callers` / `--edges calls`
- See where a file's symbols are imported → `graphify navigate <path> --imports`
- Search the graph for a pattern → `graphify search <query>`

**First-call hygiene:** before navigating, run `graphify update` once if the graph might be stale. The command is fast.

**When read_file / bash grep is correct:** when graphify returns no match, when you need exact line content of a file you've already located, or when navigating non-code (markdown, configs, data).

**Reading graphify output:** entries are ranked by relevance. The first entry is usually the right one. Hints at the bottom (`→ try this next`) point to follow-up calls that are often higher value than re-running with different args.
