# persona-stream

**Biographical persona distillation for AI coding agents** — turn months of
real collaboration behavior into a versioned, auditable, injectable persona
file set. Open-source reference implementation: hooks collect everything,
a nightly LLM distillation *folds* it under a controlled write path, three
mechanical gates audit the result, and every new session starts with the
persona injected.

简体中文一句话：把你在终端里的真实协作行为，夜复一夜蒸馏成一份可回溯、可证伪、带遗忘生命周期的 agent 人格档案；隐私边界是工程约束而非口头承诺。

```text
hooks (5 events) ──▶ data/events-*.jsonl          append-only, redacted, NEVER committed
                          │
                 nightly LLM distillation          follows distiller/FOLD_RULES.md
                          │                        writes ONLY via distiller/foldlib.py
                          ▼
                 persona/  (YAML + md)             policies · beliefs · threads · people
                          │                        · protocols · episodes · DIGEST
                 three mechanical gates            check_events · audit_persona · check_injection
                          │
                          ▼
                 SessionStart injection            core + snapshot + digest + people index
                                                  + cwd-targeted threads + FTS5 recall
```

## Why this and not another memory library

The agent-memory field is crowded (Mem0, Letta, Zep/Graphiti, Cognee, …), and
almost all of it solves *task-assistant memory*: chat facts, user preferences,
session continuity. persona-stream targets something adjacent but different —
**a persona**: how you like work done (policies with evidence and
invalidation), what you currently believe (version chains, contradictions
recorded not arbitrated), what is unfinished (thread lifecycle with mechanical
forgetting), and which shorthand protocols you and your agent have agreed on.

Two things it refuses to compromise on:

1. **Privacy boundaries are engineering, not policy.** The raw event layer
   (`data/`) is append-only and gitignored *by construction*; every write into
   the persona layer goes through one controlled library (`foldlib.py`: dedup,
   size limits, YAML pre-check, atomic replace, sidecar locks, a fold ledger);
   secret scanning is a *gate*, not a suggestion; and the demo below fails if
   so much as a real machine path or hostname appears in its synthetic data.
2. **Every conclusion is auditable.** Policies, beliefs and thread blocks carry
   `evidence: [event-id]` pointer chains back into the raw event stream
   (searchable via FTS5, no embeddings). `eval/metrics.py` computes fold
   coverage and evidence back-link rate nightly; a broken pointer is an
   exit-code-1 error, not a note.

For related work this project learned from (A-MEM, Zep/Graphiti, Mem0, Letta,
Generative Agents, MemoryBank) and what it changed, see [NOTICE](NOTICE) — an
honest lineage is part of the deliverable.

## Quickstart (5 minutes, zero secrets)

```bash
git clone https://github.com/Glory0707/persona-stream
cd persona-stream
pip install pyyaml pytest          # matplotlib optional, for KPI plots
make demo                          # or: python synthetic/demo.py
```

The demo:

1. generates **90 days of synthetic events** for a fictional person
   (林小测) across three fictional projects — corrections, a repeated kickoff
   command, emotional dips, a project that goes quiet, noise, even a fake API
   key that must be redacted on ingest;
2. runs a **deterministic rule-based distiller** through the exact same
   controlled write path the nightly LLM uses (production folding is done by
   an LLM following `distiller/FOLD_RULES.md`; the demo replaces only the
   *semantic judgment*, not the pipeline);
3. runs the three gates (`check_events` → `audit_persona` →
   `check_injection`), prints the **full SessionStart injection text**, the
   fold ledger, and the KPI line;
4. asserts expectations as it goes — which threads must be in the digest,
   which policy must be invalidated, that the dormant/closed lifecycle fired,
   that evidence back-link rate is 1.0 and broken pointers are zero — **and
   that no real name or machine path leaked anywhere**. The privacy discipline
   is enforced by the demo itself.

All demo artifacts live under `demo-home/` (gitignored). Delete it and
re-run anytime; output is deterministic for a given `--seed`.

## Run it on yourself (the real usage)

The framework reads and writes a *data home* — raw events plus the persona
file set — pointed to by `PERSONA_HOME` (default: the repository itself,
matching the layout in `schema/README.md`).

1. **Wire the hooks** (ZCode shape; see `examples/hooks/` for a copy-paste
   example and notes for Claude Code/Cursor-style hook systems):

   - `UserPromptSubmit` / `Stop` / `PermissionRequest` / `PostToolUse` /
     `PostToolUseFailure` → `python collector/collect.py <HookName>`
   - `SessionStart` → `python collector/inject.py`

   The collector is deliberately boring: top-level try/except, always exit 0,
   empty stdout, no network, <100 ms budget. It can never take your session
   down.

2. **Distill nightly.** Schedule a job (cron / Task Scheduler) that runs your
   LLM with [`distiller/AUTOMATION_PROMPT.md`](distiller/AUTOMATION_PROMPT.md)
   as the prompt. It folds pending events into the persona layer strictly via
   `foldlib.py`, then advances the cursor (`data/state.json`) as the last
   step — any failure before that is an idempotent re-run.

3. **Let the gates police you.** `python eval/check_events.py`,
   `python eval/audit_persona.py`, `python eval/check_injection.py` — exit
   codes are CI-able. The third gate rebuilds the injection text and verifies
   that what the writer wrote is actually what the next session will see
   (write-side success ≠ receiver-side visibility).

4. **Maintenance** — `python distiller/foldlib.py pending` (what's unfolded),
   `python distiller/foldlib.py sweep` (mechanical forgetting:
   open→dormant at 21 idle days, dormant→closed at 60, closed→archived at 30),
   `python eval/build_index.py` + `python eval/search_events.py "关键词"`
   (FTS5 recall), `python eval/metrics.py` (coverage / back-link), and
   `python eval/backup.py` (weekly manifest backup, sha256, 8 generations,
   optional cross-project targets).

Verified against GLM and Claude model families; the distillation prompt is
plain text — tune it for other models freely.

## The persona file set (see schema/README.md for the full contract)

| File | What it holds | Discipline |
|---|---|---|
| `core.md` | hand-written kernel + `AUTO:` region the nightlies may rewrite | manual zone is human-confirm-only |
| `SNAPSHOT.md` | quick persona glance for injection | budgeted |
| `policies/<domain>.yaml` | situation → tendency rules, confidence, evidence; soft-invalidation | `invalidated:` keeps history |
| `beliefs/<topic>.yaml` | versioned beliefs; contradictions recorded, not arbitrated | append-only versions |
| `threads/*.yaml` | open lines of work; `related:` links, `paths:` for cwd targeting | 4-level lifecycle, 32 KB cap |
| `threads/DIGEST.md` | ≤8 open threads, regenerated mechanically from thread files | hand-editing forbidden |
| `people.yaml`, `assets.yaml` | one-line indexes injected; full evidence on demand | brief-length budgets |
| `protocols.yaml` | agreed shorthand commands | evidence stripped at injection |
| `episodes/`, `journal/` | dated event cards; daily-question journal | folded like everything else |

Long-text fields use YAML block scalars everywhere — bare scalars with quotes
and colons broke real deployments twice before the rule existed.

## Privacy model (one page)

- `data/` (raw events, including full AI replies and tool payloads) lives only
  on your disk and is excluded from every commit path. Tests and the demo use
  synthetic data only.
- Ingest redacts secret-shaped strings (`sk-`, `ghp_`, `ark-`, `AKID`, JWT,
  `id.secret`, `Bearer`, `password=…`) at capture time;
  `eval/check_events.py` re-scans the raw layer, and `eval/audit_persona.py`
  scans every tracked file in the repo as a second gate.
- The fold ledger (`data/fold_ledger.jsonl`) records every fold with the
  evidence ids it consumed — deletion-by-rewrite is visible, not silent.
- This repository's own demo *asserts* the negative case: synthetic data is
  scanned for real machine paths and real names, and the demo fails if any
  appear. (`eval/leak_scan.py` runs the same scan in CI.)
- If you keep your data home inside a clone of this repo, `data/` and
  `persona/` are gitignored by default — but **verify** before ever pointing a
  remote at a home that contains real events. If a key ever does leak, treat
  it as compromised and rotate; see [SECURITY.md](SECURITY.md).

## Repository layout

```text
collector/   collect.py (5 hook events → JSONL) · inject.py (SessionStart)
distiller/   foldlib.py (the ONLY write path) · FOLD_RULES.md · AUTOMATION_PROMPT.md
eval/        check_events · audit_persona · check_injection · build_index
             search_events · metrics · backup · plot_kpi · leak_scan
synthetic/   generate.py · distill_demo.py · demo.py   ← start here
schema/      README.md — the data contract (authoritative)
tests/       142 regression tests (pytest, no network, no real data)
examples/    hook configuration examples
```

## Status & scope

This is the author's own production pipeline, extracted: the private deployment
has been running nightly since 2026-09 over thousands of real events. What is
open-source is the **framework** (collector, fold library, gates, schema,
tests, demo); the author's persona archive and event stream are private and
are not part of this repository. Issues and PRs welcome within that scope —
please do not open PRs containing personal persona data; see
[CONTRIBUTING.md](CONTRIBUTING.md).

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
