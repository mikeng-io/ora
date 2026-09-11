# MANIFEST — prefab vs event-day, file by file

One line per file, appended when the file is created — never reconstructed.
This is the eligibility evidence for brief §6 (`design/00 §2`, `design/03`):
the inputs and the stores are reused (prefab, source cited with a sha); what
decides is built on the day (event-day, source `—`).

| path | kind | source | sha12 | who | when |
|---|---|---|---|---|---|
| `pyproject.toml` | prefab | new | — | mike | 2026-09-12 |
| `loop/__init__.py` | prefab | new | — | mike | 2026-09-12 |
| `MANIFEST.md` | prefab | new | — | mike | 2026-09-12 |
| `.env.example` | prefab | new | — | mike | 2026-09-12 |
| `rooms.toml` | prefab | new | — | mike | 2026-09-12 |
| `people.toml` | prefab | new | — | mike | 2026-09-12 |
| `loop/config.py` | prefab | new | — | mike | 2026-09-12 |
| `tests/test_config.py` | prefab | new | — | mike | 2026-09-12 |
| `schema.sql` | prefab | new (design/01 §3 DDL) | — | mike | 2026-09-12 |
| `loop/store.py` | prefab | new | — | mike | 2026-09-12 |
| `tests/test_store.py` | prefab | new | — | mike | 2026-09-12 |
| `loop/logging.py` | prefab | new (grammar per design/01 §7) | — | mike | 2026-09-12 |
| `tests/test_log_grammar.py` | prefab | new | — | mike | 2026-09-12 |
| `loop/model.py` | prefab | `nora/app/composition.py::_RelevanceGateModelClient` shape | — | mike | 2026-09-12 |
| `tests/test_model.py` | prefab | new | — | mike | 2026-09-12 |
| `prompts/gate.md` | prefab | `nora/doctrine/participation.md` (v5) | `2d3012ecbdaa` | mike | 2026-09-12 |
| `prompts/fold.md` | prefab | `nora/dreamer/objectives/standing-debrief.md` (v5) | `e6539a3abeb0` | mike | 2026-09-12 |
| `prompts/verdict.md` | prefab | `nora/dreamer/objectives/standing-verdict.md` (v1) | `1d96241861cb` | mike | 2026-09-12 |
| `prompts/notes.md` | prefab | `nora/dreamer/objectives/note-candidates.md` (v5) | `5de105577f58` | mike | 2026-09-12 |
| `prompts/curation.md` | prefab | `nora/dreamer/objectives/note-curation.md` (v5) | `39e362cd5799` | mike | 2026-09-12 |
| `prompts/proactive.md` | prefab | `nora/dreamer/objectives/proactive.md` (v4) | `8cfc424dbc58` | mike | 2026-09-12 |
| `prompts/turn.md` | prefab | assembled (design/07 item 6) — Opus-reviewed, 2 blockers + 4 should-fixes fixed | `67a4ab3ced51` | mike | 2026-09-12 |
| `tests/test_prompts.py` | prefab | new | — | mike | 2026-09-12 |
