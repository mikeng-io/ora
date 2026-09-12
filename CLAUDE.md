# CLAUDE.md — Ora

Ora is a one-day build: a net-new ambient-loop agent for the AI Tinkerers
hackathon (2026-09-12), distilled from the reference project's building blocks. **The spec is
`design/` — read it in full before touching anything.** `design/07` is the
overnight work order; `design/03 §2` is the event-day one.

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
- **Conformance** — does it follow the governing constraints (`design/`,
  `06-decisions.md`, this file)?
- **Completeness** — is anything material missing? Correct-but-incomplete
  is a fail.

The three compose: 4C is a CMS act over a claim; Grounding is CMS over
evidence. When a model judges badly, **read its input before rewriting its
instruction** (the reference project's measured lesson, `~/.claude/CMS.md`).

## What Ora is, and how strict to be

Ora is an **MVP for a demo**, not the reference project. The thinking discipline above is
not negotiable — it is how a wrong claim gets caught before it reaches the
stage. The *process* around it is MVP-grade, deliberately:

| keep (it protects the demo) | drop (it protects a decade, which Ora does not have) |
|---|---|
| CMS · Grounding · 4C on every conclusion | release fragments, ADRs, decision-index numbering beyond `design/06` |
| a test for every **brake** (allowlist, own-send drop, fail-closed, ungrounded-speak → hold) and every **demo step** (journey) — proved by reverting the line | mutation tables, architecture tests, doc-reference gates, coverage targets |
| `MANIFEST.md` appended as you go (eligibility evidence) | one-Opus-reviewer-per-change; only `design/07` items 6–7 get a review |
| `model_calls` + `gate_log` + the console grammar (the trace *is* the ops) | metrics, alerting, retention, backups, dashboards |
| `ruff` + `pytest` before a commit | mypy strictness, import contracts, CI |
| one commit per item, plain Conventional Commit subject | footers, PR bodies, merge messages |

If a rule from the reference project's `AGENTS.md` is not in this file, it does not apply.
When in doubt: does skipping it risk the demo lying? If no, skip it.

## Repo rules (Ora only — different from the reference project)

- **Work on `master`. No worktrees, no branches, no PRs.** One commit per
  item, Conventional Commit subject, body names the manifest line. Never
  `reset`; amending the last local commit is fine.
- **Never** write under `the reference project's checkout`; never change norty's config,
  compose or `.env`. Read the reference project's tree to copy from; recompute every sha you
  cite.
- `.env` is git-ignored and stays that way. Read keys from it; never print,
  log, or copy a key into any file or commit.
- `MANIFEST.md`: one line per file — `path · prefab|event-day · source ·
  sha · who · when` — **appended when the file is created**, never
  reconstructed. It is the eligibility evidence (`design/00 §2`).
- Prompt bodies under `prompts/` are the reference project's measured instructions. **Do not
  edit them.** `turn.md` is the one prompt assembled here and it is a header
  contract, not a judgement (`design/07` item 6).
- Model: `deepseek-v4-flash` on Ollama Cloud, `reasoning_effort` `none` or
  `low`, nothing else, nowhere else (ORA-5, ORA-14). No OmniRoute.
- Postgres (`DATABASE_URL`), asyncpg, `schema.sql` idempotent, no ORM, no
  migrations (ORA-16). Every model-written row points at its `model_calls`
  row; bodies carry `*_len` (ORA-17).
- The console log is part of the demo: one fixed-grammar line per event
  (`design/01 §7`), third-party loggers at WARNING, nothing else printed.
- Fail closed everywhere; silence is the default; a `speak` without
  `grounded_on` is a `hold` (`design/01 §6`).
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

- Design bundle: `design/` (`00`–`07`, `_index.yaml`), audits and the
  response: `design/reviews/`.
- Runbook for the day: `RUNBOOK.md`. Story, diagram, demo table: `README.md`.
- the reference project (read-only reference): `the reference project's checkout` — master, `434282db` or
  later. Its rules (`AGENTS.md`) do **not** apply here; this file does.
