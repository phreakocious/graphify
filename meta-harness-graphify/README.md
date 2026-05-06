# meta-harness-graphify

Phase 1 MVP. See `docs/SPEC.md` for the design and `docs/PLAN.md` for the implementation plan.

## Quick start

```bash
cd meta-harness-graphify
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
ANTHROPIC_API_KEY=... .venv/bin/python meta_harness.py smoke
```

`smoke` runs one rollout (baseline candidate × seed task × 1 trial) and prints metrics.
