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
| `loop/render.py` | prefab | `nora/agent/prefill.py`, `nora/domain/decision_log.py`, `nora/language/{note_render,standing_render}.py` shapes; self_card/peer_card hedges verbatim | — | mike | 2026-09-12 |
| `tests/test_render.py` | prefab | new | — | mike | 2026-09-12 |
| `cases/gate/gate.yaml` | prefab | `nora/evals/cases/relevance_gate/gate.yaml` (12 of 40 cases, unedited) | — | mike | 2026-09-12 |
| `cases/participation/participation.yaml` | prefab | `nora/evals/cases/participation/participation.yaml` (3 twin pairs, unedited) | — | mike | 2026-09-12 |
| `cases/journey.yaml` | prefab | new (design/04 §2 shape) | — | mike | 2026-09-12 |
| `cases/run_gate.py` | prefab | new | — | mike | 2026-09-12 |
| `cases/__init__.py` | prefab | new | — | mike | 2026-09-12 |
| `loop/config.py` (workspace property) | prefab | new | — | mike | 2026-09-12 |
| `loop/people.py` | prefab | new | — | mike | 2026-09-12 |
| `loop/observe_signal.py` | prefab | `nora/platform/signal/{listener,parser}.py` (listener near-verbatim; parser trimmed — no media/reactions/stickers/mentions/quotes; own-send + syncMessage drop kept) | — | mike | 2026-09-12 |
| `loop/observe_whatsapp.py` | prefab | `nora/platform/whatsapp/{listener,parser}.py` (listener near-verbatim; parser trimmed; from_me drop kept) | — | mike | 2026-09-12 |
| `tests/test_allowlist.py` | prefab | new | — | mike | 2026-09-12 |
| live: norty's signal-cli/whatsapp-bridge over Tailscale | prefab | `unverified: ports 18080/18081 bind to 127.0.0.1 on norty (docs/runbook.md); not reachable at norty's Tailscale ip 100.117.189.77; no tunnel attempted per "never touch norty"` | — | mike | 2026-09-12 |
| `loop/act.py` | prefab | `nora/platform/signal/send.py`, `nora/platform/whatsapp/send.py` (request shape), `nora/agent/outbound.py::OutboundComposer._scan` (mention scan, simplified: no roster confirmation) — WhatsApp outbound mentions are Ora's own addition, undocumented on the bridge, unverified until a real send | — | mike | 2026-09-12 |
| `tests/test_act.py` | prefab | new | — | mike | 2026-09-12 |
| `loop/decisions.py` | prefab | schema-side only (insert/select); shape from `nora/domain/decision_log.py`'s `recent_for` filter-before-bound discipline — the log's LOGIC (who writes) is event-day | — | mike | 2026-09-12 |
| `loop/gate_log.py` | prefab | new | — | mike | 2026-09-12 |
| `tests/test_decisions.py` | prefab | new | — | mike | 2026-09-12 |
| `loop/memory.py` | prefab | `nora/infra/honcho/client.py` shape (feed/peer_card/representation; stamp-stripping kept verbatim), trimmed to one workspace — no `WorkspaceRouter` | — | mike | 2026-09-12 |
| `tests/test_memory.py` | prefab | new | — | mike | 2026-09-12 |
| `loop/search.py` | prefab | `nora/infra/search/exa.py`, `nora/infra/search/_shared.py` shape (three-state contract, untrusted wrap kept) | — | mike | 2026-09-12 |
| `tests/test_search.py` | prefab | new | — | mike | 2026-09-12 |
| `loop/route.py` | prefab | `nora/infra/routes/{client,directions,http}.py` shape (Routes API v2, trimmed — address waypoints directly, no separate Places resolution) | — | mike | 2026-09-12 |
| `tests/test_route.py` | prefab | new | — | mike | 2026-09-12 |
| `loop/calendar.py` | prefab | new (stub — request body a documented TODO) | — | mike | 2026-09-12 |
| `tests/test_calendar_stub.py` | prefab | new | — | mike | 2026-09-12 |
| `loop_tail.py` | prefab | new | — | mike | 2026-09-12 |
| `tests/test_loop_tail.py` | prefab | new | — | mike | 2026-09-12 |
| `tests/test_journey.py` | prefab | new (shape from `nora/tests/integration/test_ambient_loop_positive_control.py`'s fixture, no sha) | — | mike | 2026-09-12 |
| `RUNBOOK.md` | prefab | new (design/04 §1-2, §4) | — | mike | 2026-09-12 |
| `README.md` | prefab | new (design/00, 01) | — | mike | 2026-09-12 |
| `docs/video-storyboard.md` | prefab | new (design/05 §3) | — | mike | 2026-09-12 |
| `docs/social-post.md` | prefab | new | — | mike | 2026-09-12 |
| `docker-compose.yml` | prefab | `nora/docker-compose.yml` (signal-cli, whatsapp-bridge stanzas, volumes stripped to Ora's own state dirs); postgres + honcho new — honcho image/tag `unverified: no self-host stanza exists anywhere in Nora's tree to copy; confirm against Honcho's own docs at 10:00` | — | mike | 2026-09-12 |
