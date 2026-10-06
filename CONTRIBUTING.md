# Contributing

Thanks for your interest. Read the two rules first — they are what make this
project publishable at all.

## The two red lines

1. **No real persona data, ever.** Do not open PRs, issues, or gists
   containing your (or anyone's) raw events, persona files, screenshots of
   real sessions, or `data/` contents. If a reproduction needs personal
   context, rebuild it with `synthetic/generate.py` or write a synthetic
   fixture. Maintainers will close and, if necessary, purge PRs that violate
   this.
2. **Secrets are a gate, not a suggestion.** CI runs `eval/leak_scan.py`
   (secret patterns + machine-path blacklist) over the whole tree. If CI flags
   your branch, fix the fixture — never add your real string to the exemption
   list.

## What is in scope

- Framework code: `collector/`, `distiller/foldlib.py`, `eval/`, `schema/`,
  `tests/`, `synthetic/`
- The demo pipeline and its assertions
- Documentation, hook examples for other agent platforms
- Regression tests for any behavior change

## Out of scope

- Personal persona archives (yours or anyone's) as repository content
- Cloud/telemetry features — this tool is local-only by design
- Embedding/vector backends — the file+FTS5 choice is deliberate (see README);
  propose alternatives only if they keep the zero-dependency property

## Development

```bash
pip install -e ".[dev]"     # pyyaml + pytest
python -m pytest tests/ -q  # 141 tests, no network, no real data
python synthetic/demo.py    # the e2e pipeline with assertions
python eval/leak_scan.py    # the scan CI will run on your PR
```

Notes for reviewers and contributors:

- `foldlib.py` is the single write path into the persona layer. New mutation
  kinds belong there (dedup check, YAML pre-check, atomic replace, ledger
  row) — not as direct file edits in callers.
- Tests must be hermetic: no fixture may read the real `persona/` or `data/`
  (the recall-tests had exactly this bug before extraction; there is an
  autouse fixture isolating them — keep new tests on the same pattern).
- Windows is the primary platform (sidecar `msvcrt` locks with POSIX
  fallbacks); CI runs both `ubuntu-latest` and `windows-latest`.
