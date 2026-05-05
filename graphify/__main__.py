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

- **About to `Read` a source-code file you don't already know.** Run `graphify navigate "@<symbol>"` first. The frontier shows you whether the file is a leaf, hub, or router, and what shape of context you actually need.
- **About to chain `Grep` / `Glob` calls to trace a call graph or find who-uses-X.** That's literally what `graphify navigate` `in`/`out`/`path` are for.
- **About to implement, change, or debug something in unfamiliar territory.** Map the blast radius first: focus the entry point, run `in` to see callers, decide what's actually load-bearing.
- **You don't know where to start.** `graphify navigate "@<best-guess-label>"` is a free probe — a hit returns a frontier, a miss returns real names you can grab onto.

**Don't reach for graphify when reading**: `.json` / `.yaml` / `.toml` / `.csv` / `.md` / `.txt` / `.log` / lockfiles / build output / your own memory or scratch files. graphify only indexes source code — for data, configs, prose, and machine output, just `Read` directly.

### Default workflow

Chain ops in a single call — left-to-right, output is the last op's result. Each call prints a session id at the bottom; pass it back via `--session <id>` to resume the cursor on a later call. Default chains stay one-shot — the next call without `--session` starts fresh under a new id.

```
graphify navigate "@<symbol>"                    # focus a node, return frontier (~200 tok)
graphify navigate "@<symbol>" methods            # focus + list methods
graphify navigate "@<symbol>" methods 6 in       # focus + methods + pick 6th + show callers
graphify navigate "@<symbol>" --include-inferred # widen to LLM-inferred edges (default: AST only)
graphify navigate "@<symbol>" --json             # structured JSON for programmatic chaining
graphify navigate --session <id> in              # resume a prior session, run another op
```

Pivot ops: `in | out | methods | contains | coc | rat | parent | inh`. Pick from previous listing with `N` or `[N]`. Use `--legend` for the column-key on first invocation. Use `--limit N` to widen listings (default 25). Per-id cursor files mean parallel calls don't race on shared state.

Use `graphify path "A" "B"` for reachability between two named things (~50 tok). Use `graphify explain "X"` for a one-shot summary of a single node (~350 tok). Use `graphify query "..."` only when the question is genuinely diffuse and you've already narrowed scope — it returns a flat node dump.

### After editing code

```
graphify update .
```

Re-extracts changed files via AST. No LLM cost. Run after a session of edits to keep the graph current.

### What NOT to do

- Don't read `GRAPH_REPORT.md` end-to-end — it's a 40KB+ overview that costs ~10K tokens and the community-list section is filler in AST-only mode. Use `graphify navigate` instead.
- Don't run `graphify query` on a question you haven't narrowed yet — it caps at ~2K tokens of flat node listings, mostly noise.
"""

_CLAUDE_MD_MARKER = "## graphify"

# AGENTS.md section for Codex, OpenCode, and OpenClaw.
# All three platforms read AGENTS.md in the project root for persistent instructions.
_AGENTS_MD_SECTION = """\
## graphify

This project has a graphify knowledge graph at `graphify-out/graph.json`. Use it to keep your context window from collapsing under a heavy codebase.

### When you should reach for graphify

Before any of these moves, scout the graph first — it's 50–500x cheaper than the alternative:

- **About to read a source-code file you don't already know.** Run `graphify navigate "@<symbol>"` first. The frontier shows you whether the file is a leaf, hub, or router.
- **About to chain greps to trace a call graph or find who-uses-X.** That's what `graphify navigate` `in`/`out`/`path` are for.
- **About to implement, change, or debug in unfamiliar territory.** Map the blast radius first: focus the entry point, run `in` to see callers.
- **You don't know where to start.** `graphify navigate "@<best-guess-label>"` is a free probe.

**Don't reach for graphify when reading**: `.json` / `.yaml` / `.toml` / `.csv` / `.md` / `.txt` / `.log` / lockfiles / build output / your own memory or scratch files. graphify only indexes source code — for data, configs, prose, and machine output, just read directly.

### Default workflow

Chain ops left-to-right; output is the last op's result. Each call prints a session id; pass it via `--session <id>` to resume.

```
graphify navigate "@<symbol>" methods 6 in   # focus, list methods, pick 6th, show its callers
graphify navigate --session <id> back        # resume + ↺ pop history
```

Pivots: `in | out | methods | contains | coc | rat | parent | inh`. Pick from listing with `N` or `[N]`.

Use `graphify path "A" "B"` for reachability (~50 tok). Use `graphify explain "X"` for a one-shot node summary (~350 tok). Use `graphify query "..."` only when the question is genuinely diffuse and you've narrowed scope.

### After editing code

```
graphify update .
```

AST-only re-extraction, no LLM cost.

### What NOT to do

- Don't read `GRAPH_REPORT.md` end-to-end — it's a 40KB+ overview, ~10K tokens. Use `graphify navigate` instead.
- Don't run `graphify query` on a question you haven't narrowed yet — flat node dumps, mostly noise.
"""

_AGENTS_MD_MARKER = "## graphify"

_GEMINI_MD_SECTION = """\
## graphify

This project has a graphify knowledge graph at `graphify-out/graph.json`. Use it to keep your context window from collapsing under a heavy codebase.

### When you should reach for graphify

Before any of these moves, scout the graph first — it's 50–500x cheaper than the alternative:

- **About to read a source-code file you don't already know.** Run `graphify navigate "@<symbol>"` first.
- **About to chain greps to trace a call graph or find who-uses-X.** That's what `graphify navigate` `in`/`out`/`path` are for.
- **About to implement, change, or debug in unfamiliar territory.** Map the blast radius first.
- **You don't know where to start.** `graphify navigate "@<best-guess-label>"` is a free probe.

**Don't reach for graphify when reading**: `.json` / `.yaml` / `.toml` / `.csv` / `.md` / `.txt` / `.log` / lockfiles / build output / your own memory or scratch files. graphify only indexes source code — for data, configs, prose, and machine output, just read directly.

### Default workflow

Chain ops left-to-right; output is the last op's result. Each call prints a session id; pass it via `--session <id>` to resume.

```
graphify navigate "@<symbol>" methods 6 in   # focus, list methods, pick 6th, show its callers
graphify navigate --session <id> back        # resume + ↺ pop history
```

Pivots: `in | out | methods | contains | coc | rat | parent | inh`. Pick from listing with `N` or `[N]`.

Use `graphify path "A" "B"` for reachability (~50 tok). Use `graphify explain "X"` for a one-shot summary (~350 tok). Use `graphify query "..."` only for genuinely diffuse questions after narrowing scope.

### After editing code

```
graphify update .
```

AST-only, no LLM cost.

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


def claude_install(project_dir: Path | None = None) -> None:
    """Write the graphify section to the local CLAUDE.md."""
    target = (project_dir or Path(".")) / "CLAUDE.md"

    if target.exists():
        content = target.read_text(encoding="utf-8")
        if _CLAUDE_MD_MARKER in content:
            print("graphify already configured in CLAUDE.md")
            return
        new_content = content.rstrip() + "\n\n" + _CLAUDE_MD_SECTION
    else:
        new_content = _CLAUDE_MD_SECTION

    target.write_text(new_content, encoding="utf-8")
    print(f"graphify section written to {target.resolve()}")

    # Also write Claude Code PreToolUse hook to .claude/settings.json
    _install_claude_hook(project_dir or Path("."))

    print()
    print("Claude Code will now check the knowledge graph before answering")
    print("codebase questions and rebuild it after code changes.")


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
    """Remove the graphify section from the local CLAUDE.md."""
    target = (project_dir or Path(".")) / "CLAUDE.md"

    if not target.exists():
        print("No CLAUDE.md found in current directory - nothing to do")
        return

    content = target.read_text(encoding="utf-8")
    if _CLAUDE_MD_MARKER not in content:
        print("graphify section not found in CLAUDE.md - nothing to do")
        return

    # Remove the ## graphify section: from the marker to the next ## heading or EOF
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
        print(f"CLAUDE.md was empty after removal - deleted {target.resolve()}")

    _uninstall_claude_hook(project_dir or Path("."))


def main() -> None:
    # Check all known skill install locations for a stale version stamp.
    # Skip during install/uninstall (hook writes trigger a fresh check anyway).
    # Deduplicate paths so platforms sharing the same install dir don't warn twice.
    if not any(arg in ("install", "uninstall") for arg in sys.argv):
        for skill_dst in {Path.home() / cfg["skill_dst"] for cfg in _PLATFORM_CONFIG.values()}:
            _check_skill_version(skill_dst)

    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: graphify <command>")
        print()
        print("Commands:")
        print("  install [--platform P]  copy skill to platform config dir (claude|windows|codex|opencode|aider|claw|droid|trae|trae-cn|gemini|cursor|antigravity|hermes|kiro)")
        print("  path \"A\" \"B\"            shortest path between two nodes in graph.json")
        print("    --graph <path>          path to graph.json (default graphify-out/graph.json)")
        print("  explain \"X\"             plain-language explanation of a node and its neighbors")
        print("    --graph <path>          path to graph.json (default graphify-out/graph.json)")
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
        print("  benchmark [graph.json]  measure token reduction vs naive full-corpus approach")
        print("  navigate [ops...]       cursor-based graph navigation (LLM-friendly)")
        print("    @<label>                focus on a node by label/id (fuzzy fallback for typos)")
        print("    in | out | methods | contains    list typed pivots")
        print("    callers | callees       sugar for `in --kind=calls` / `out --kind=calls`")
        print("    coc                     co-community siblings (same Leiden cluster)")
        print("    rat | inh | parent      rationale anchors / inherits / structural parent")
        print("    siblings                structural peers (same parent file/class)")
        print("    read | body             dump full body of focused node inline (no separate Read needed)")
        print("    [N] | N                 focus on Nth item from previous listing in the chain")
        print("    back | reset            pop history / clear cursor")
        print("    --session <id>          resume a prior session (id is printed at the bottom of every output)")
        print("    --no-session            disable session entirely (no disk, no id printed)")
        print("    --json                  structured JSON output")
        print("    --include-inferred      include LLM-inferred edges (default: AST-extracted only)")
        print("    --min-confidence X      drop edges below score X (only meaningful with --include-inferred)")
        print("    --kind <rel[,rel,...]>  restrict in/out listings to edges of these relations (e.g. calls,uses)")
        print("    --bodies N              show first N source lines under each contains/methods item")
        print("    --depth N               for in/out, walk N hops via non-structural edges (default 1)")
        print("    --limit N               max items per listing (default 25)")
        print("    --legend                prepend column-key legend")
        print("    --no-ops-hint           omit the ops cheat-sheet line")
        print("    --graph <path>          path to graph.json (default graphify-out/graph.json)")
        print("    Chain ops in one call: graphify navigate @Foo methods 1 in")
        print("  hook install            install post-commit/post-checkout git hooks (all platforms)")
        print("  hook uninstall          remove git hooks")
        print("  hook status             check if git hooks are installed")
        print("  gemini install          write GEMINI.md section + BeforeTool hook (Gemini CLI)")
        print("  gemini uninstall        remove GEMINI.md section + BeforeTool hook")
        print("  cursor install          write .cursor/rules/graphify.mdc (Cursor)")
        print("  cursor uninstall        remove .cursor/rules/graphify.mdc")
        print("  claude install          write graphify section to CLAUDE.md + PreToolUse hook (Claude Code)")
        print("  claude uninstall        remove graphify section from CLAUDE.md + PreToolUse hook")
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
        # PreToolUse hook handler — read JSON tool call on stdin, decide
        # whether to emit the navigate-nudge. Suppresses on non-code Read
        # because graphify only indexes source; nudging on a .md/.json/.yaml
        # read is noise. Glob and Grep stay un-gated since both search code.
        # Stdlib-only and quick-return so the hook adds minimal latency.
        import os.path as _osp
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except Exception:
            return
        tool = payload.get("tool_name") or ""
        inp = payload.get("tool_input") or {}
        fp = (inp.get("file_path") or "").strip()
        if tool == "Read":
            ext = _osp.splitext(fp)[1].lower()
            # Mirror of graphify.detect.CODE_EXTENSIONS — kept inline so the
            # hook stays stdlib-only (importing detect pulls in the rest of
            # the package). Update both lists if either changes.
            CODE_EXTS = {
                ".py", ".ts", ".js", ".jsx", ".tsx", ".mjs", ".ejs", ".go",
                ".rs", ".java", ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp",
                ".rb", ".swift", ".kt", ".kts", ".cs", ".scala", ".php",
                ".lua", ".toc", ".zig", ".ps1", ".ex", ".exs", ".m", ".mm",
                ".jl", ".vue", ".svelte", ".dart", ".v", ".sv",
            }
            if ext not in CODE_EXTS:
                return
            # Reading our own report → no nudge needed
            if "graphify-out/" in fp:
                return
            # Lap-3: suppress the nudge if the file was just surfaced by a
            # graphify navigate call. The session log is written by
            # navigate._record_session_paths and lives alongside graph.json.
            # Both sides are realpath'd because macOS aliases /tmp →
            # /private/tmp; raw abspath wouldn't match.
            try:
                import time as _t
                recent_log = Path("graphify-out/.session/recent-paths")
                if recent_log.exists():
                    now = _t.time()
                    target = _osp.realpath(fp) if fp else ""
                    for line in recent_log.read_text(encoding="utf-8").splitlines():
                        ts_str, _, path = line.partition("\t")
                        try:
                            if now - float(ts_str) > 600:
                                continue
                        except ValueError:
                            continue
                        if path and target and path == target:
                            return
            except Exception:
                pass
        msg = (
            "graphify-out/graph.json exists. Before reading/grepping "
            "unfamiliar code, scout it cheaper: `graphify navigate "
            "\"@<symbol>\"` returns a dense affordance frame (~200 tok). "
            "Then pivot with in/out/methods/coc/parent/[N], or jump to "
            "file:line once a node is load-bearing. See "
            "~/.claude/skills/graphify/SKILL.md."
        )
        # Lap-3: one-shot staleness banner when the graph is conspicuously
        # behind the working tree. Stamp file rate-limits to one banner per
        # 30min so it doesn't piggyback on every code read.
        try:
            import time as _t
            graph_p = Path("graphify-out/graph.json")
            if graph_p.exists():
                now = _t.time()
                graph_age = now - graph_p.stat().st_mtime
                if graph_age > 86400:  # > 1 day
                    stamp = Path("graphify-out/.session/banner-stamp")
                    last_banner = 0.0
                    if stamp.exists():
                        try:
                            last_banner = float(stamp.read_text().strip())
                        except (OSError, ValueError):
                            last_banner = 0.0
                    if now - last_banner > 1800:  # 30min
                        days = int(graph_age // 86400)
                        days_str = f"{days}d" if days >= 1 else f"{int(graph_age // 3600)}h"
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
        out_payload = {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": msg,
        }}
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
            claude_install()
        elif subcmd == "uninstall":
            claude_uninstall()
        else:
            print("Usage: graphify claude [install|uninstall]", file=sys.stderr)
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
        if len(sys.argv) < 3:
            print("Usage: graphify query \"<question>\" [--dfs] [--budget N] [--graph path]", file=sys.stderr)
            sys.exit(1)
        from graphify.serve import _score_nodes, _bfs, _dfs, _subgraph_to_text
        from graphify.security import sanitize_label
        from networkx.readwrite import json_graph
        question = sys.argv[2]
        use_dfs = "--dfs" in sys.argv
        budget = 2000
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
            _raw = _json.loads(gp.read_text(encoding="utf-8"))
            try:
                G = json_graph.node_link_graph(_raw, edges="links")
            except TypeError:
                G = json_graph.node_link_graph(_raw)
        except Exception as exc:
            print(f"error: could not load graph: {exc}", file=sys.stderr)
            sys.exit(1)
        terms = [t.lower() for t in question.split() if len(t) > 2]
        scored = _score_nodes(G, terms)
        if not scored:
            print("No matching nodes found.")
            sys.exit(0)
        start = [nid for _, nid in scored[:5]]
        nodes, edges = (_dfs if use_dfs else _bfs)(G, start, depth=2)
        print(_subgraph_to_text(G, nodes, edges, token_budget=budget))
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
        if len(sys.argv) < 4:
            print("Usage: graphify path \"<source>\" \"<target>\" [--graph path] [--include-inferred]", file=sys.stderr)
            sys.exit(1)
        from graphify.navigate import resolve_focus, label_index
        from networkx.readwrite import json_graph
        import networkx as _nx
        source_label = sys.argv[2]
        target_label = sys.argv[3]
        graph_path = "graphify-out/graph.json"
        include_inferred = False  # default: AST ground truth only
        args = sys.argv[4:]
        for i, a in enumerate(args):
            if a == "--graph" and i + 1 < len(args):
                graph_path = args[i + 1]
            elif a == "--include-inferred":
                include_inferred = True
        gp = Path(graph_path).resolve()
        if not gp.exists():
            print(f"error: graph file not found: {gp}", file=sys.stderr)
            sys.exit(1)
        _raw = json.loads(gp.read_text(encoding="utf-8"))
        try:
            G = json_graph.node_link_graph(_raw, edges="links")
        except TypeError:
            G = json_graph.node_link_graph(_raw)
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
        for who, label, nid, cands in (("source", source_label, src_nid, src_cands),
                                        ("target", target_label, tgt_nid, tgt_cands)):
            if nid is None:
                if cands:
                    print(f"{who} '{label}' is ambiguous ({len(cands)} matches). pick a more specific label.", file=sys.stderr)
                    for c in cands[:5]:
                        print(f"  - {G.nodes[c].get('label', c)}", file=sys.stderr)
                else:
                    print(f"No node matching '{label}' found.", file=sys.stderr)
                sys.exit(1)
        # Weight `contains` (structural co-location) higher than semantic
        # edges so a class→method→callee path beats a class→file→class
        # shortcut of the same hop count. Without this, A and B that share
        # a parent file always look "2 hops apart" via `contains`, which is
        # technically true but uninformative — the real relationship is
        # the call/method chain, even when it's the same length.
        # (Both routes are 2 hops; the weighted path makes the semantic
        # one cheaper so shortest_path prefers it.)
        STRUCTURAL_RELS = {"contains"}
        for u, v, d in G.edges(data=True):
            d["_path_weight"] = 10.0 if d.get("relation") in STRUCTURAL_RELS else 1.0
        try:
            path_nodes = _nx.shortest_path(G, src_nid, tgt_nid, weight="_path_weight")
        except (_nx.NetworkXNoPath, _nx.NodeNotFound):
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
        print(f"Shortest path ({hops} hops):\n  " + " ".join(segments))
        if annotation:
            print(annotation)

    elif cmd == "explain":
        if len(sys.argv) < 3:
            print("Usage: graphify explain \"<node>\" [--graph path] [--include-inferred] [--limit N]", file=sys.stderr)
            sys.exit(1)
        from graphify.navigate import resolve_focus, label_index
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
        try:
            G = json_graph.node_link_graph(_raw, edges="links")
        except TypeError:
            G = json_graph.node_link_graph(_raw)
        # Use navigate's resolver so explain accepts both `@<label>` and
        # plain `<label>` and shares the prefix/substring/fuzzy ranking.
        idx = label_index(G)
        nid, candidates, match_type, _alts = resolve_focus(G, idx, label)
        if nid is None:
            if candidates:
                print(f"'{label}' is ambiguous ({len(candidates)} matches). pick one:", file=sys.stderr)
                for c in candidates[:5]:
                    print(f"  - {G.nodes[c].get('label', c)}", file=sys.stderr)
            else:
                print(f"No node matching '{label}' found.")
            sys.exit(0 if not candidates else 1)
        d = G.nodes[nid]
        print(f"Node: {d.get('label', nid)}")
        print(f"  ID:        {nid}")
        print(f"  Source:    {d.get('source_file', '')} {d.get('source_location', '')}".rstrip())
        print(f"  Type:      {d.get('file_type', '')}")
        print(f"  Community: {d.get('community', '')}")
        print(f"  Degree:    {G.degree(nid)}")
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
            prev_extracted: bool | None = None
            boundary_inserted = False
            for nb in sorted_nbrs[:limit]:
                edata = G.edges[nid, nb]
                rel = edata.get("relation", "")
                conf = edata.get("confidence", "")
                is_ext = conf == "EXTRACTED"
                if (not boundary_inserted and prev_extracted is True and not is_ext):
                    print("  ── inferred below ──")
                    boundary_inserted = True
                prev_extracted = is_ext
                print(f"  --> {G.nodes[nb].get('label', nb)} [{rel}] [{conf}]")
            if len(neighbors_filtered) > limit:
                print(f"  ... and {len(neighbors_filtered) - limit} more")
        elif dropped > 0:
            print(f"\nNo EXTRACTED edges. {dropped} INFERRED edges hidden (use --include-inferred).")

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
        show_ops_hint = True
        limit = LIST_LIMIT
        kinds: set[str] | None = None
        bodies: int | None = None
        depth: int = 1
        i = 0
        ops: list[str] = []
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
            elif a == "--no-ops-hint":
                show_ops_hint = False; i += 1
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
            bodies=bodies,
            depth=depth,
        )
        print(out)

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
