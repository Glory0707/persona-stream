# Security Policy

## The model in one paragraph

persona-stream is local-only software: hooks append redacted events to
`data/`, distillation writes persona files through one controlled library,
and three mechanical gates verify integrity. There is no network component,
no telemetry, no account. The security story therefore rests on three
engineering invariants — (1) the raw layer never enters version control,
(2) every secret-shaped string is redacted at ingest and re-scanned by two
gates, (3) all persona writes are auditable via the fold ledger.

## Reporting a vulnerability

Open a [security advisory](https://github.com/Glory0707/persona-stream/security/advisories/new)
rather than a public issue. Expect a response within a week; fixes land on
main and are noted in the release notes.

## If you leaked a credential through your own event stream

This project's author has lived this — the incident shaped the current gates.
The protocol that matters:

1. **Treat the credential as compromised immediately.** Rotate/revoke it
   first; house-cleaning second. Deletion after the fact does not un-leak a
   pushed git object (clone counts, GitHub keeps unreachable objects, and
   cache/archives exist).
2. **Scrub the repository history** (`git filter-repo`) *and* contact the
   platform (e.g. GitHub support can clear cached views; push protection may
   already have blocked the push).
3. **Fix the entry point, not just the instance**: if a secret reached the
   event stream, extend the ingest redaction (`collector/collect.py
   SECRET_SUBS`) so the *shape* is caught next time, then let
   `eval/check_events.py` (raw layer) and `eval/audit_persona.py
   SECRET_PATTERNS` (tracked files) confirm the clean state.
4. **Gates are gates**: if `audit_persona` reports a secret-shaped string in
   a tracked file, the nightly pipeline must treat it as a hard failure
   (exit 1) — do not weaken the pattern to get green.

## Design notes for reviewers

- The collector runs inside your agent's hook system with top-level
  exception guarding, empty stdout, and a hard time budget — it is
  engineered to be unable to break the host session.
- `append_jsonl` and ledger writes take sidecar lock files (`msvcrt` on
  Windows, plain O_APPEND fallback elsewhere) because two hook processes
  writing concurrently tore lines once in production.
- The demo and CI run a negative scan (`eval/leak_scan.py`): real machine
  paths (drive-letter homes, user directories, hostnames) and secret patterns
  must produce zero hits. A failure there is a leak, not a flake.
