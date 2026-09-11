# CLAUDE.md — Ora

Ora is a one-day build: a net-new ambient-loop agent for the AI Tinkerers
hackathon (2026-09-12), distilled from Nora's building blocks. **The spec is
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
instruction** (Nora's measured lesson, `~/.claude/CMS.md`).

## Repo rules (Ora only — different from Nora)

- **Work on `master`. No worktrees, no branches, no PRs.** One commit per
  item, Conventional Commits, body names the manifest line. Never squash,
  never amend a pushed commit, never `reset`.
- **Never** write under `~/Workplace/nora`; never change norty's config,
  compose or `.env`. Read Nora's tree to copy from; recompute every sha you
  cite.
- `.env` is git-ignored and stays that way. Read keys from it; never print,
  log, or copy a key into any file or commit.
- `MANIFEST.md`: one line per file — `path · prefab|event-day · source ·
  sha · who · when` — **appended when the file is created**, never
  reconstructed. It is the eligibility evidence (`design/00 §2`).
- Prompt bodies under `prompts/` are Nora's measured instructions. **Do not
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

- **Prove a fix by reverting it:** a behaviour exists when removing its
  line reddens a named test. "Tests green" is not evidence of anything in
  particular.
- Live checks name their target (`norty` bridges, `ora-verify` Honcho
  workspace, Ollama Cloud) and their result, in the commit body.
- `ruff check .` and `pytest -q` before every commit; the journey test's six
  `xfail(strict=True)` are the event-day scoreboard — do not remove a marker
  until its step is real.

## Where things are

- Design bundle: `design/` (`00`–`07`, `_index.yaml`), audits and the
  response: `design/reviews/`.
- Runbook for the day: `RUNBOOK.md`. Story, diagram, demo table: `README.md`.
- Nora (read-only reference): `~/Workplace/nora` — master, `434282db` or
  later. Its rules (`AGENTS.md`) do **not** apply here; this file does.
