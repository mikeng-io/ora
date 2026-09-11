-- source: design/01-architecture.md §3 (Ora's own DDL, written for this repo)
-- Postgres schema for Ora's loop (design/01 §3, ORA-16). Applied at start by
-- loop/store.py; idempotent (every statement is CREATE ... IF NOT EXISTS /
-- CREATE INDEX IF NOT EXISTS), no ORM, no migrations.

CREATE TABLE IF NOT EXISTS messages (
  id BIGSERIAL PRIMARY KEY,
  platform TEXT NOT NULL,            -- 'signal' | 'whatsapp'
  conversation_id TEXT NOT NULL,     -- the room, as the bridge names it
  workspace TEXT NOT NULL,           -- from rooms.toml
  sender_id TEXT NOT NULL,           -- platform id; '' for Ora's own rows
  person_id TEXT,                    -- from people.toml, NULL if unmapped
  is_ora BOOLEAN NOT NULL DEFAULT FALSE,
  delivery_status TEXT,              -- 'sent' | 'failed' | NULL for human rows
  ts TIMESTAMPTZ NOT NULL,
  body TEXT NOT NULL,
  body_len INTEGER NOT NULL,         -- chars
  reply_to_id BIGINT,
  raw JSONB
);
CREATE INDEX IF NOT EXISTS messages_room_ts ON messages(platform, conversation_id, ts);

CREATE TABLE IF NOT EXISTS standing (
  platform TEXT NOT NULL, conversation_id TEXT NOT NULL,
  body TEXT NOT NULL,                -- ONE paragraph, <= 1200 chars
  body_len INTEGER NOT NULL,
  fold_count INTEGER NOT NULL DEFAULT 0,
  last_folded_row_id BIGINT,         -- watermark: fold reads since here
  last_call_id BIGINT,               -- the model_calls row that wrote it
  updated_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (platform, conversation_id)
);

CREATE TABLE IF NOT EXISTS note (
  id TEXT PRIMARY KEY,               -- short id (N1, N2 for seeded; 8-hex otherwise)
  workspace TEXT NOT NULL,
  room_label TEXT NOT NULL,          -- the room it was raised in, by label (never the workspace name)
  title TEXT NOT NULL,
  closing_condition TEXT NOT NULL,   -- what would settle it, in the room's words
  anchor_at TIMESTAMPTZ,             -- the date it is about, if any
  anchor_place TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  created_call_id BIGINT,            -- NULL for seeded notes (disclosed)
  retired_at TIMESTAMPTZ,
  retired_reason TEXT,               -- 'closed' | 'expired' | 'moot'
  cited_row_id BIGINT,               -- the message that closed it; MAY be an is_ora row (ORA-8)
  strikes INTEGER NOT NULL DEFAULT 0,   -- raised by her, unanswered (39 D6)
  revisit_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS reminders (
  id TEXT PRIMARY KEY,               -- R1, R2 … (short, readable on screen)
  workspace TEXT NOT NULL,
  note_id TEXT,
  due_at TIMESTAMPTZ NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('pending','done','cancelled','failed')),
  intent TEXT NOT NULL,              -- what to raise, in her words — never rendered to another decider
  intent_len INTEGER NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  created_call_id BIGINT,
  resolved_at TIMESTAMPTZ,
  resolved_by TEXT                   -- 'fired' | 'participation:cancel' | 'curation:moot'
);

CREATE TABLE IF NOT EXISTS decisions (
  id BIGSERIAL PRIMARY KEY,
  workspace TEXT NOT NULL,
  writer TEXT NOT NULL CHECK (writer IN ('participation','proactive_decide','proactive_act','tag')),
  verdict TEXT NOT NULL CHECK (verdict IN ('spoke','silent','cancelled','remind','decline','replied','failed')),
  platform TEXT NOT NULL, conversation_id TEXT NOT NULL,
  note_id TEXT,                      -- the note it was about, if any — the id, NEVER the title
  reminder_id TEXT,                  -- the reminder a 'cancelled' verdict cancelled
  grounded_on TEXT,                  -- 'note:<id>' | 'standing' | 'tool:<name>' | NULL (ORA-10)
  reason TEXT NOT NULL DEFAULT '',   -- a code label (e.g. 'held_cooldown'), never model prose
  call_id BIGINT,                    -- the model_calls row behind it
  ts TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS gate_log (   -- observability ONLY: read by loop_tail and the runbook queries, never by a decider (DEC-179 kept)
  id BIGSERIAL PRIMARY KEY,
  platform TEXT NOT NULL, conversation_id TEXT NOT NULL,
  verdict TEXT NOT NULL,             -- 'go' | 'no_go'
  score REAL, error_kind TEXT,       -- error_kind set when it failed closed
  window_rows INTEGER NOT NULL,      -- rows in the judged window
  window_end_row_id BIGINT NOT NULL,
  call_id BIGINT,
  ts TIMESTAMPTZ NOT NULL
);

-- ORA-18: images only, plain storage (no rustfs custody), one row per unique
-- byte (sha256). `path` is a local file under ./state/media/, never a bind
-- into signal-cli's own attachment store. `comprehended=false` is an index
-- entry that says the bytes exist and claims nothing about them yet
-- (design/19-media-comprehension.md §2's rule, kept).
CREATE TABLE IF NOT EXISTS media_objects (
  sha256 TEXT PRIMARY KEY,
  media_type TEXT NOT NULL,
  size INTEGER NOT NULL,
  path TEXT NOT NULL,
  title TEXT,
  description TEXT,
  content TEXT,
  comprehended BOOLEAN NOT NULL DEFAULT FALSE,
  describe_model TEXT,
  created_at TIMESTAMPTZ NOT NULL
);

-- Added after messages already shipped (design/07 built text-only); a plain
-- ALTER keeps schema.sql idempotent without a migration tool (ORA-16).
ALTER TABLE messages ADD COLUMN IF NOT EXISTS media_sha256 TEXT REFERENCES media_objects(sha256);

CREATE TABLE IF NOT EXISTS model_calls (  -- one row per model call, every stage; the trace, in a table
  id BIGSERIAL PRIMARY KEY,
  stage TEXT NOT NULL,               -- 'gate' | 'turn' | 'tag' | 'fold' | 'notes' | 'curation' | 'proactive_decide' | 'proactive_act'
  platform TEXT, conversation_id TEXT,
  model TEXT NOT NULL,               -- deepseek-v4-flash
  reasoning TEXT NOT NULL,           -- 'none' | 'low'
  prompt_sha TEXT NOT NULL,          -- sha256[:12] of system+user
  prompt_chars INTEGER NOT NULL,
  prompt_tokens INTEGER, completion_tokens INTEGER, reasoning_tokens INTEGER,
  latency_ms INTEGER NOT NULL,
  finish_reason TEXT,                -- 'stop' | 'length' | error kind
  ok BOOLEAN NOT NULL,
  output_chars INTEGER,
  started_at TIMESTAMPTZ NOT NULL
);
