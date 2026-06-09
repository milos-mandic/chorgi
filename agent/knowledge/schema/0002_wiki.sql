-- Wiki: LLM-clustered topics over bookmarks.
-- Bookmarks remain the source of truth in skills/bookmarks/bookmarks.json;
-- these tables only hold topic membership + synthesized article state.

CREATE TABLE wiki_topics (
    id               TEXT PRIMARY KEY,
    slug             TEXT UNIQUE NOT NULL,
    title            TEXT NOT NULL,
    summary          TEXT,
    article_md       TEXT,
    article_built_at TEXT,
    bookmark_count   INTEGER NOT NULL DEFAULT 0,
    article_dirty    INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE wiki_topic_bookmarks (
    topic_id     TEXT NOT NULL,
    bookmark_url TEXT NOT NULL,
    assigned_at  TEXT NOT NULL,
    assigned_by  TEXT NOT NULL,
    PRIMARY KEY (topic_id, bookmark_url),
    FOREIGN KEY (topic_id) REFERENCES wiki_topics(id) ON DELETE CASCADE
);

CREATE INDEX idx_wiki_topic_bookmarks_url ON wiki_topic_bookmarks(bookmark_url);
CREATE INDEX idx_wiki_topics_dirty ON wiki_topics(article_dirty) WHERE article_dirty = 1;
