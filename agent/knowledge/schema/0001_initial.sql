-- M1 initial schema: people, interactions, tasks, inbox_items.
-- threads/drafts deferred to M3/M4.

CREATE TABLE people (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    slug            TEXT UNIQUE NOT NULL,
    linkedin_url    TEXT,
    email           TEXT,
    x_handle        TEXT,
    role            TEXT,
    company         TEXT,
    tags            TEXT,                -- JSON array as text
    source          TEXT,
    notes_path      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE interactions (
    id              TEXT PRIMARY KEY,
    type            TEXT NOT NULL,       -- meeting, email_thread, ln_message, x_message, post_published, note
    title           TEXT,
    occurred_at     TEXT,
    summary         TEXT,
    content_path    TEXT,
    source          TEXT,
    source_ref      TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE interaction_participants (
    interaction_id  TEXT NOT NULL,
    person_id       TEXT NOT NULL,
    PRIMARY KEY (interaction_id, person_id),
    FOREIGN KEY (interaction_id) REFERENCES interactions(id) ON DELETE CASCADE,
    FOREIGN KEY (person_id)      REFERENCES people(id)       ON DELETE CASCADE
);

CREATE TABLE tasks (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    description     TEXT,
    status          TEXT NOT NULL,       -- proposed, accepted, done, rejected
    due_at          TEXT,
    person_id       TEXT,
    interaction_id  TEXT,
    source          TEXT NOT NULL,       -- manual, fathom, agent_suggestion
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (person_id)      REFERENCES people(id),
    FOREIGN KEY (interaction_id) REFERENCES interactions(id)
);

CREATE TABLE inbox_items (
    id                       TEXT PRIMARY KEY,
    type                     TEXT NOT NULL,   -- task_proposal, contact_update, draft_idea, followup_suggestion, new_person
    payload                  TEXT NOT NULL,   -- JSON
    source_interaction_id    TEXT,
    status                   TEXT NOT NULL,   -- pending, accepted, rejected
    created_at               TEXT NOT NULL,
    decided_at               TEXT,
    FOREIGN KEY (source_interaction_id) REFERENCES interactions(id)
);

CREATE INDEX idx_interactions_occurred_at ON interactions(occurred_at);
CREATE INDEX idx_tasks_status_due ON tasks(status, due_at);
CREATE INDEX idx_inbox_status ON inbox_items(status);
