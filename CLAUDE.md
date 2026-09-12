# CLAUDE.md — Ora

Ora is a one-day build: a net-new ambient-loop agent for the AI Tinkerers
hackathon (2026-09-12), distilled from a private reference project's
building blocks. **The spec lived in `design/`, a local, gitignored
directory of planning notes — not part of this public repo.** If it is
present on disk, read it in full before touching anything. If it is
absent (a fresh clone), `MANIFEST.md` / `RUNBOOK.md` / `README.md` is the
whole spec.

## How to think (this is not optional)

**CMS — the primitives.** Every act, at every grain, is three operations:

- **Comprehend** — turn the input into a model you can compute on: entities,
  intent, constraints, current state. Interpret **once**; everything after
  computes on that model and never re-reads the raw text.
- **Measure** — reduce the model to a **decidable signal**, bound to the
  real property, never a proxy. PASS / FAIL / ERROR stay distinct; an ERROR
  is never a PASS. Cover what must **not** be true.
- **Serialize** — fix the result into canonical, explicit state **with
  provenance** (file:line, tool output, sha, row id). `serialize(N)` is the
  input you `comprehend(N+1)`.

**Grounding — the first principle.** Every claim binds to a verifiable
source before it is asserted as fact: *anchored* (file:line, tool output,
config value, a row) · *derived* (from anchored claims, not yet checked) ·
*unanchored* (may not be asserted). An unanchored assertion is fabrication;
«I don't know» is cheaper than a wrong answer. **No self-attestation:**
"done" is decided by evidence — a green test, a read-back, a row — never by
the doer. An exit code is not proof. A model's "I did X" is a claim.

**4C — the critique principle.** Before any conclusion, PR-sized or one
line, run four axes and say which failed:

- **Coherence** — does it hold together as one model?
- **Consistency** — does it contradict anything already established (the
  bundle, the schema, a prior verified fact)?
- **Conformance** — does it follow the governing constraints (this file and
  the manifest)?
- **Completeness** — is anything material missing? Correct-but-incomplete
  is a fail.

The three compose: 4C is a CMS act over a claim; Grounding is CMS over
evidence. When a model judges badly, **read its input before rewriting its
instruction** (a measured lesson from the reference project).

## What Ora is, and how strict to be

Ora is an **MVP for a demo**, not the reference project it was distilled
from. The thinking discipline above is not negotiable — it is how a wrong
claim gets caught before it reaches the stage. The *process* around it is
MVP-grade, deliberately:

| keep (it protects the demo) | drop (it protects a decade, which Ora does not have) |
|---|---|
| CMS · Grounding · 4C on every conclusion | release fragments, ADRs, decision-index numbering |
| a test for every **brake** (allowlist, own-send drop, fail-closed, ungrounded-speak → hold) and every **demo step** (journey) — proved by reverting the line | mutation tables, architecture tests, doc-reference gates, coverage targets |
| `MANIFEST.md` appended as you go (eligibility evidence) | one-Opus-reviewer-per-change; only the media feature and select items got a review |
| `model_calls` + `gate_log` + the console grammar (the trace *is* the ops) | metrics, alerting, retention, backups, dashboards |
| `ruff` + `pytest` before a commit | mypy strictness, import contracts, CI |
| one commit per item, plain Conventional Commit subject | footers, PR bodies, merge messages |

If a rule from the reference project's own `AGENTS.md` is not in this file,
it does not apply. When in doubt: does skipping it risk the demo lying? If
no, skip it.

## Repo rules (Ora only — different from the reference project)

- **Work on `master`. No worktrees, no branches, no PRs.** One commit per
  item, Conventional Commit subject, body names the manifest line. Never
  `reset`; amending the last local commit is fine.
- **Never** write under the reference project's local checkout (a separate,
  private repo on this machine); never change `norty`'s config, compose, or
  `.env` (the server the reference project is deployed on). Read the
  reference project's tree to copy from; recompute every sha you cite.
- `.env` is git-ignored and stays that way. Read keys from it; never print,
  log, or copy a key into any file or commit.
- `MANIFEST.md`: one line per file — `path · prefab|event-day · source ·
  sha · who · when` — **appended when the file is created**, never
  reconstructed. It is the eligibility evidence.
- Prompt bodies under `prompts/` are the reference project's measured
  instructions, copied verbatim including their own self-reference. **Do
  not edit them.** `turn.md` is the one prompt assembled here and it is a
  header contract, not a judgement.
- Model: `deepseek-v4-flash` on Ollama Cloud, `reasoning_effort` `none` or
  `low`, nothing else, nowhere else (ORA-5, ORA-14). No OmniRoute.
- Postgres (`DATABASE_URL`), asyncpg, `schema.sql` idempotent, no ORM, no
  migrations (ORA-16). Every model-written row points at its `model_calls`
  row; bodies carry `*_len` (ORA-17).
- The console log is part of the demo: one fixed-grammar line per event,
  third-party loggers at WARNING, nothing else printed.
- Fail closed everywhere; silence is the default; a `speak` without
  `grounded_on` is a `hold`.
- A verification that cannot run is marked `unverified: <why>` in the
  manifest. Faking a pass is the one unforgivable thing here.

## Verification discipline

- **Prove a brake by reverting it:** the allowlist, the own-send drop, the
  fail-closed paths and the ungrounded-speak downgrade each have one test
  that reddens when the line is removed. Everything else: a test if it is
  cheap, none if it is not.
- Live checks name their target (`norty` bridges, `ora-verify` Honcho
  workspace, Ollama Cloud) and their result, in the commit body.
- `ruff check .` and `pytest -q` before every commit; the journey test's six
  `xfail(strict=True)` are the event-day scoreboard — do not remove a marker
  until its step is real.

## Where things are

- Runbook for the day: `RUNBOOK.md`. Story, diagram, demo table: `README.md`.
- The reference project (read-only reference): its own local checkout on
  this machine — master, or later. Its own rules do **not** apply here;
  this file does.
