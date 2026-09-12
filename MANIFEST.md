# MANIFEST — prefab vs event-day, file by file

One line per file, appended when the file is created — never reconstructed.
This is the eligibility evidence for brief §6:
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
| `schema.sql` | prefab | new (DDL) | — | mike | 2026-09-12 |
| `loop/store.py` | prefab | new | — | mike | 2026-09-12 |
| `tests/test_store.py` | prefab | new | — | mike | 2026-09-12 |
| `loop/logging.py` | prefab | new (grammar) | — | mike | 2026-09-12 |
| `tests/test_log_grammar.py` | prefab | new | — | mike | 2026-09-12 |
| `loop/model.py` | prefab | `reference/app/composition.py::_RelevanceGateModelClient` shape | — | mike | 2026-09-12 |
| `tests/test_model.py` | prefab | new | — | mike | 2026-09-12 |
| `prompts/gate.md` | prefab | `reference/doctrine/participation.md` (v5) | `2d3012ecbdaa` | mike | 2026-09-12 |
| `prompts/fold.md` | prefab | `reference/dreamer/objectives/standing-debrief.md` (v5) | `6bec3ffba932` | mike | 2026-09-12 |
| `prompts/verdict.md` | prefab | `reference/dreamer/objectives/standing-verdict.md` (v1) | `1d96241861cb` | mike | 2026-09-12 |
| `prompts/notes.md` | prefab | `reference/dreamer/objectives/note-candidates.md` (v5) | `5de105577f58` | mike | 2026-09-12 |
| `prompts/curation.md` | prefab | `reference/dreamer/objectives/note-curation.md` (v5) | `22ebe913ff60` | mike | 2026-09-12 |
| `prompts/proactive.md` | prefab | `reference/dreamer/objectives/proactive.md` (v4) | `a108100a9127` | mike | 2026-09-12 |
| `prompts/turn.md` | prefab | assembled — Opus-reviewed, 2 blockers + 4 should-fixes fixed | `d066bb72ba07` | mike | 2026-09-12 |
| `tests/test_prompts.py` | prefab | new | — | mike | 2026-09-12 |
| `loop/render.py` | prefab | `reference/agent/prefill.py`, `reference/domain/decision_log.py`, `reference/language/{note_render,standing_render}.py` shapes; self_card/peer_card hedges verbatim | — | mike | 2026-09-12 |
| `tests/test_render.py` | prefab | new | — | mike | 2026-09-12 |
| `cases/gate/gate.yaml` | prefab | `reference/evals/cases/relevance_gate/gate.yaml` (12 of 40 cases, unedited) | — | mike | 2026-09-12 |
| `cases/participation/participation.yaml` | prefab | `reference/evals/cases/participation/participation.yaml` (3 twin pairs, unedited) | — | mike | 2026-09-12 |
| `cases/journey.yaml` | prefab | new (shape) | — | mike | 2026-09-12 |
| `cases/run_gate.py` | prefab | new | — | mike | 2026-09-12 |
| `cases/__init__.py` | prefab | new | — | mike | 2026-09-12 |
| `loop/config.py` (workspace property) | prefab | new | — | mike | 2026-09-12 |
| `loop/people.py` | prefab | new | — | mike | 2026-09-12 |
| `loop/observe_signal.py` | prefab | `reference/platform/signal/{listener,parser}.py` (listener near-verbatim; parser trimmed — no media/reactions/stickers/mentions/quotes; own-send + syncMessage drop kept) | — | mike | 2026-09-12 |
| `loop/observe_whatsapp.py` | prefab | `reference/platform/whatsapp/{listener,parser}.py` (listener near-verbatim; parser trimmed; from_me drop kept) | — | mike | 2026-09-12 |
| `tests/test_allowlist.py` | prefab | new | — | mike | 2026-09-12 |
| live: norty's signal-cli/whatsapp-bridge over Tailscale | prefab | `unverified: ports 18080/18081 bind to 127.0.0.1 on norty (docs/runbook.md); not reachable at norty's Tailscale ip 100.117.189.77; no tunnel attempted per "never touch norty"` | — | mike | 2026-09-12 |
| `loop/act.py` | prefab | `reference/platform/signal/send.py`, `reference/platform/whatsapp/send.py` (request shape), `reference/agent/outbound.py::OutboundComposer._scan` (mention scan, simplified: no roster confirmation) — WhatsApp outbound mentions are Ora's own addition, undocumented on the bridge, unverified until a real send | — | mike | 2026-09-12 |
| `tests/test_act.py` | prefab | new | — | mike | 2026-09-12 |
| `loop/decisions.py` | prefab | schema-side only (insert/select); shape from `reference/domain/decision_log.py`'s `recent_for` filter-before-bound discipline — the log's LOGIC (who writes) is event-day | — | mike | 2026-09-12 |
| `loop/gate_log.py` | prefab | new | — | mike | 2026-09-12 |
| `tests/test_decisions.py` | prefab | new | — | mike | 2026-09-12 |
| `loop/memory.py` | prefab | `reference/infra/honcho/client.py` shape (feed/peer_card/representation; stamp-stripping kept verbatim), trimmed to one workspace — no `WorkspaceRouter` | — | mike | 2026-09-12 |
| `tests/test_memory.py` | prefab | new | — | mike | 2026-09-12 |
| `loop/search.py` | prefab | `reference/infra/search/exa.py`, `reference/infra/search/_shared.py` shape (three-state contract, untrusted wrap kept) | — | mike | 2026-09-12 |
| `tests/test_search.py` | prefab | new | — | mike | 2026-09-12 |
| `loop/route.py` | prefab | `reference/infra/routes/{client,directions,http}.py` shape (Routes API v2, trimmed — address waypoints directly, no separate Places resolution) | — | mike | 2026-09-12 |
| `tests/test_route.py` | prefab | new | — | mike | 2026-09-12 |
| `loop/calendar.py` | prefab | new (stub — request body a documented TODO) | — | mike | 2026-09-12 |
| `tests/test_calendar_stub.py` | prefab | new | — | mike | 2026-09-12 |
| `loop_tail.py` | prefab | new | — | mike | 2026-09-12 |
| `tests/test_loop_tail.py` | prefab | new | — | mike | 2026-09-12 |
| `tests/test_journey.py` | prefab | new (shape from `reference/tests/integration/test_ambient_loop_positive_control.py`'s fixture, no sha) | — | mike | 2026-09-12 |
| `RUNBOOK.md` | prefab | new | — | mike | 2026-09-12 |
| `README.md` | prefab | new | — | mike | 2026-09-12 |
| `docs/video-storyboard.md` | prefab | new | — | mike | 2026-09-12 |
| `docs/social-post.md` | prefab | new | — | mike | 2026-09-12 |
| `docker-compose.yml` | prefab | `the reference project's docker-compose.yml` (signal-cli, whatsapp-bridge stanzas, volumes stripped to Ora's own state dirs); postgres + honcho new — honcho image/tag `unverified: no self-host stanza exists anywhere in the reference project's tree to copy; confirm against Honcho's own docs at 10:00` | — | mike | 2026-09-12 |
| `schema.sql` (media_objects, messages.media_sha256) | prefab | new — ORA-18 decision (media returns, images only, scope) implemented here; shape trimmed — no provenance JSON, no video-chunk fields | — | mike | 2026-09-12 |
| `loop/media_store.py` | prefab | new (plain sha256-keyed storage — not the reference project's rustfs custody) | — | mike | 2026-09-12 |
| `tests/test_media_store.py` | prefab | new | — | mike | 2026-09-12 |
| `loop/vision.py` | prefab | new (three-field shape; model chosen after live-testing kimi-k3 vs glm-5.3-flash — see ORA-18) | — | mike | 2026-09-12 |
| `tests/test_vision.py` | prefab | new | — | mike | 2026-09-12 |
| `loop/observe_signal.py` (image capture) | prefab | `reference/platform/signal/media.py` shape (`getAttachment`) — `unverified: no real attachment has ever arrived on the reference project's own deployment either; this is the reference project's own measured-not-proven claim, not a stronger one` | — | mike | 2026-09-12 |
| `tests/test_observe_signal_media.py` | prefab | new | — | mike | 2026-09-12 |
| `loop/render.py` (render_media_marker, join_media_into_body) | prefab | new (render-time join, adapted) | — | mike | 2026-09-12 |
| `loop/act.py` (policy half: `deliver`) | event-day | new — real send + `is_ora` row + `delivery_status`; allowlist re-checked at the wire, row written whether the send succeeded or failed | — | mike | 2026-09-12 |
| `loop/decide_gate.py` | event-day | new — relevance gate; every failure mode fails closed to `no_go` with `error_kind`; writes `gate_log` only, never `decisions` | — | mike | 2026-09-12 |
| `tests/test_decide_gate.py` | event-day | new — 12 tests, one per failure mode | — | mike | 2026-09-12 |
| `loop/decide_turn.py` | event-day | new — participation turn; `speak` without `grounded_on` is downgraded to `hold` (brake, proved by test) | — | mike | 2026-09-12 |
| `tests/test_decide_turn.py` | event-day | new — 18 tests incl. the ungrounded-speak brake | — | mike | 2026-09-12 |
| `loop/tag.py` | event-day | new — tag path; `grounded_on` validated against tool reality, never trusted from model JSON | — | mike | 2026-09-12 |
| `tests/test_tag.py` | event-day | new — 14 tests incl. false-grounding rejection | — | mike | 2026-09-12 |
